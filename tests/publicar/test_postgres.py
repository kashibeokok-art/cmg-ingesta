"""Tests de la publicacion a PostgreSQL (sql/cmg_cen.sql + publicar/postgres.py).

Necesitan un PostgreSQL de verdad: el lenguaje de particiones, COPY y permisos no
se puede imitar. Se corren solo si existe la variable CMG_PRUEBA_PG con la
conexion a un servidor de PRUEBAS donde el usuario sea administrador, por ejemplo:

    $env:CMG_PRUEBA_PG = "host=localhost port=55433 user=postgres"

(No empieza con CMGI_ a proposito: el programa rechaza las CMGI_* que no conoce.)
Cada test crea su propia base temporal y la borra al terminar. Nunca usar el
servidor real.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import psycopg
import pytest

from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import ingerir_cen
from cmg_ingesta.publicar import postgres as pg
from cmg_ingesta.silver import escribir

BASE = os.environ.get("CMG_PRUEBA_PG")
pytestmark = pytest.mark.skipif(not BASE, reason="sin CMG_PRUEBA_PG: no hay PostgreSQL de pruebas")

Conn = psycopg.Connection[tuple[object, ...]]


@pytest.fixture
def base() -> Iterator[str]:
    """Una base nueva con el esquema aplicado. Devuelve su nombre."""
    nombre = f"cmg_prueba_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(f"{BASE} dbname=postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE \"{nombre}\" TEMPLATE template0 ENCODING 'UTF8'")
    try:
        with psycopg.connect(f"{BASE} dbname={nombre}", autocommit=True) as c:
            c.execute(pg.ESQUEMA_SQL.read_text(encoding="utf-8"))
        yield nombre
    finally:
        with psycopg.connect(f"{BASE} dbname=postgres", autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')


def _url(base: str, usuario: str) -> str:
    sin_usuario = " ".join(p for p in str(BASE).split() if not p.startswith("user="))
    return f"{sin_usuario} dbname={base} user={usuario}"


@pytest.fixture
def carga(base: str) -> Iterator[Conn]:
    """Conexion como cmg_carga: el usuario que usa `cmg publicar-sql`."""
    with pg.conectar(_url(base, "cmg_carga")) as c:
        yield c


def mes_silver(
    con: duckdb.DuckDBPyConnection,
    silver: Path,
    anio: int,
    mes: int,
    valor: float,
    origen: str = "pagina_cen_def",
    barras: tuple[str, ...] = ("A.BLANCAS_____013", "STA.ELVIRA____013"),
    dias: int = 2,
) -> None:
    """Escribe un mes de Silver con `dias` dias completos y un valor fijo."""
    lista = ", ".join("'" + b + "'" for b in barras)
    consulta = f"""
        SELECT b AS barra, make_date({anio}, {mes}, d) AS fecha,
               CAST(h AS UTINYINT) AS hora, CAST(m AS UTINYINT) AS minuto,
               CASE WHEN h BETWEEN 8 AND 17 THEN 'B' WHEN h BETWEEN 18 AND 22 THEN 'C'
                    ELSE 'A' END AS bloque,
               CAST({valor} AS DOUBLE) AS cmg_usd_mwh, false AS es_hora_extra,
               CAST(make_date({anio}, {mes}, d) AS TIMESTAMP)
                   + to_hours(h) + to_minutes(m) AS fecha_hora,
               '{origen}' AS origen, now()::TIMESTAMP AS ingerido_en
        FROM (SELECT unnest([{lista}]) AS b), (SELECT unnest(range(24)) AS h),
             (SELECT unnest([0, 15, 30, 45]) AS m), (SELECT unnest(range(1, {dias + 1})) AS d)
    """
    escribir.escribir_particion(con, consulta, silver, anio, mes)


def publicar(
    con: duckdb.DuckDBPyConnection,
    carga: Conn,
    tmp_path: Path,
    forzar: bool = False,
    desde: Mes | None = None,
    quitar: bool = False,
    avisar: pg.Avisar = pg._nada,
) -> list[pg.ResultadoMes]:
    return pg.publicar(
        con,
        carga,
        tmp_path / "silver",
        tmp_path / "bronze",
        tmp_path / "staging",
        forzar=forzar,
        desde=desde,
        quitar=quitar,
        avisar=avisar,
    )


def uno(c: Conn, consulta: str, params: tuple[object, ...] = ()) -> object:
    fila = c.execute(consulta, params).fetchone()
    assert fila is not None
    return fila[0]


# ------------------------------------------------------------------ esquema


def test_el_esquema_se_puede_volver_a_correr(base: str) -> None:
    with psycopg.connect(f"{BASE} dbname={base}", autocommit=True) as c:
        c.execute(pg.ESQUEMA_SQL.read_text(encoding="utf-8"))
        assert uno(c, "SELECT count(*) FROM cmg.origen") == 3


# ------------------------------------------------------------- primera carga


def test_un_mes_nuevo_queda_consultable(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2025, 3, 50.0)
    resultados = publicar(con, carga, tmp_path)

    assert [(r["anio"], r["mes"], r["accion"], r["filas"], r["dias"]) for r in resultados] == [
        (2025, 3, "nuevo", 2 * 2 * 96, 2)
    ]
    assert uno(carga, "SELECT count(*) FROM cmg.cmg_15min WHERE barra = 'A.BLANCAS_____013'") == 192
    fila = carga.execute(
        "SELECT bloque, cmg_usd_mwh, fecha_hora, origen FROM cmg.cmg_15min "
        "WHERE barra = 'A.BLANCAS_____013' AND fecha = '2025-03-02' AND hora = 13 AND minuto = 15"
    ).fetchone()
    assert fila == ("B", 50.0, datetime(2025, 3, 2, 13, 15), "pagina_cen_def")
    assert uno(carga, "SELECT count(*) FROM cmg.dia") == 2
    assert uno(carga, "SELECT count(*) FROM cmg.revision") == 0  # nada que comparar


def test_la_segunda_corrida_no_sube_nada(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2025, 3, 50.0)
    publicar(con, carga, tmp_path)
    assert publicar(con, carga, tmp_path) == []


def test_varios_meses_y_particiones(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2024, 12, 40.0)
    mes_silver(con, tmp_path / "silver", 2025, 1, 41.0)
    publicar(con, carga, tmp_path)
    particiones = [
        str(f[0])
        for f in carga.execute(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = 'cmg.cuarto'::regclass ORDER BY 1"
        ).fetchall()
    ]
    assert particiones == ["cuarto_2024_12", "cuarto_2025_01"]
    # ninguna tabla temporal quedo dando vueltas
    assert uno(carga, "SELECT count(*) FROM pg_tables WHERE tablename LIKE %s", ("%_nuevo",)) == 0


# ----------------------------------------------------------- revisiones


def test_un_preliminar_que_pasa_a_definitivo_queda_como_revision(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    """El caso diario: el mes se subio con el pre; despues llega el def con otros valores."""
    silver = tmp_path / "silver"
    mes_silver(con, silver, 2026, 8, 40.0, origen="pagina_cen_pre")
    publicar(con, carga, tmp_path)

    mes_silver(con, silver, 2026, 8, 42.5, origen="pagina_cen_def")
    (r,) = publicar(con, carga, tmp_path)

    assert (r["accion"], r["revisiones"]) == ("reemplazado", 2)
    filas = carga.execute(
        "SELECT fecha, origen_antes, origen_despues, cuartos, cuartos_distintos, dif_max_usd, "
        "dif_prom_usd FROM cmg.revision ORDER BY fecha"
    ).fetchall()
    assert filas == [
        (date(2026, 8, 1), 3, 2, 192, 192, 2.5, 2.5),
        (date(2026, 8, 2), 3, 2, 192, 192, 2.5, 2.5),
    ]
    assert uno(carga, "SELECT DISTINCT cmg_usd_mwh FROM cmg.cuarto") == 42.5
    assert uno(carga, "SELECT DISTINCT origen_id FROM cmg.dia") == 2


def test_volver_a_subir_lo_mismo_no_inventa_revisiones(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2026, 8, 40.0)
    publicar(con, carga, tmp_path)
    (r,) = publicar(con, carga, tmp_path, forzar=True)
    assert (r["accion"], r["revisiones"]) == ("reemplazado", 0)


def test_un_dia_nuevo_en_el_mes_no_es_revision(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    """El mes en curso crece dia a dia: agregar un dia no es revisar los anteriores."""
    silver = tmp_path / "silver"
    mes_silver(con, silver, 2026, 10, 40.0, dias=2)
    publicar(con, carga, tmp_path)
    mes_silver(con, silver, 2026, 10, 40.0, dias=3)
    (r,) = publicar(con, carga, tmp_path)
    assert (r["dias"], r["revisiones"]) == (3, 0)


# --------------------------------------------------------------- barras


def test_las_barras_conservan_su_numero_y_no_gastan_la_secuencia(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    """smallint llega a 32.767: si cada carga gastara un numero por barra, se agotaria."""
    silver = tmp_path / "silver"
    mes_silver(con, silver, 2025, 1, 40.0, barras=("A", "B"))
    publicar(con, carga, tmp_path)
    antes = {
        str(n): i for n, i in carga.execute("SELECT nombre, barra_id FROM cmg.barra").fetchall()
    }
    for i in range(3):
        mes_silver(con, silver, 2025, 1, 41.0 + i, barras=("A", "B", "C"))
        publicar(con, carga, tmp_path)
    despues = {
        str(n): i for n, i in carga.execute("SELECT nombre, barra_id FROM cmg.barra").fetchall()
    }
    assert {k: despues[k] for k in antes} == antes
    assert uno(carga, "SELECT max(barra_id) FROM cmg.barra") == 3


# ------------------------------------------------------------- documentos


def _bronze_con(bronze: Path, dia: date, tipo: str) -> cen.EntradaManifiesto:
    nombre = f"Antecedentes_CMG_Real_{tipo}_{dia:%y%m%d}.zip"
    e: cen.EntradaManifiesto = {
        "url": "https://x/" + nombre, "nombre": nombre, "tipo": tipo, "version": 1,
        "reemision": 0, "fecha_operacion": dia.isoformat(), "fecha_publicacion": "2026-08-20",
        "sha256": uuid.uuid4().hex * 2, "bytes": 1, "descargado_en": "2026-10-13T10:00:00",
    }  # fmt: skip
    m = cen.leer_manifiesto(bronze)
    m[nombre] = e
    cen.guardar_manifiesto(bronze, m)
    return e


def test_cada_dia_queda_con_su_documento(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    bronze = tmp_path / "bronze"
    pre = _bronze_con(bronze, date(2026, 8, 1), "pre")
    defi = _bronze_con(bronze, date(2026, 8, 1), "def")
    ingerir_cen.guardar_estado(bronze, {"2026-08": ingerir_cen.huella([(date(2026, 8, 1), defi)])})
    mes_silver(con, tmp_path / "silver", 2026, 8, 40.0, dias=1)
    publicar(con, carga, tmp_path)

    assert uno(carga, "SELECT documento FROM cmg.dia WHERE fecha = '2026-08-01'") == defi["nombre"]
    assert uno(carga, "SELECT count(*) FROM cmg.documento") == 2  # tambien el pre
    assert uno(carga, "SELECT tipo FROM cmg.documento WHERE nombre = %s", (pre["nombre"],)) == "pre"


def test_silver_atrasado_respecto_del_bronze_no_se_publica(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    """Se bajo un def pero no se ingirio: la base diria un documento que no es el de los datos."""
    bronze = tmp_path / "bronze"
    _bronze_con(bronze, date(2026, 8, 1), "def")
    ingerir_cen.guardar_estado(bronze, {"2026-08": ["otra-huella"]})
    mes_silver(con, tmp_path / "silver", 2026, 8, 40.0, dias=1)
    with pytest.raises(ValueError, match="ingerir-pagina"):
        publicar(con, carga, tmp_path)


# --------------------------------------------------------------- permisos


def test_el_usuario_de_lectura_lee_pero_no_escribe(
    con: duckdb.DuckDBPyConnection, carga: Conn, base: str, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2025, 3, 50.0)
    publicar(con, carga, tmp_path)  # crea la particion DESPUES del esquema
    with pg.conectar(_url(base, "cmg_lectura")) as lectura:
        assert uno(lectura, "SELECT count(*) FROM cmg.cmg_15min") == 384
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            lectura.execute("DELETE FROM cmg.cuarto")


def test_marca_de_tiempo_de_la_carga(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2025, 3, 50.0)
    antes = datetime.now(UTC)
    publicar(con, carga, tmp_path)
    cargado = uno(carga, "SELECT cargado_en FROM cmg.carga_mes")
    assert isinstance(cargado, datetime) and cargado >= antes


# -------------------------------------------------------------------- Gold


def mes_silver_variado(con: duckdb.DuckDBPyConnection, silver: Path, anio: int, mes: int) -> None:
    """Valores que cambian por barra, dia, hora y minuto, con ceros al mediodia (como el
    vertimiento solar): asi un promedio de promedios daria distinto que el correcto."""
    consulta = f"""
        SELECT b AS barra, make_date({anio}, {mes}, d) AS fecha,
               CAST(h AS UTINYINT) AS hora, CAST(m AS UTINYINT) AS minuto,
               CASE WHEN h BETWEEN 8 AND 17 THEN 'B' WHEN h BETWEEN 18 AND 22 THEN 'C'
                    ELSE 'A' END AS bloque,
               CAST(CASE WHEN h BETWEEN 11 AND 14 THEN 0
                         ELSE 20 + h * 3.25 + m / 15.0 + d * 0.5 + len(b) END AS DOUBLE)
                   AS cmg_usd_mwh,
               false AS es_hora_extra,
               CAST(make_date({anio}, {mes}, d) AS TIMESTAMP)
                   + to_hours(h) + to_minutes(m) AS fecha_hora,
               'pagina_cen_def' AS origen, now()::TIMESTAMP AS ingerido_en
        FROM (SELECT unnest(['A.BLANCAS_____013', 'X']) AS b), (SELECT unnest(range(24)) AS h),
             (SELECT unnest([0, 15, 30, 45]) AS m), (SELECT unnest(range(1, 4)) AS d)
    """
    escribir.escribir_particion(con, consulta, silver, anio, mes)


def test_gold_mensual_da_lo_mismo_que_el_gold_del_programa(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    """La regla vive en dos lugares (DuckDB del menu/Excel y PostgreSQL): este test
    exige que den lo mismo, numero por numero."""
    from cmg_ingesta.gold import bloques_mes

    silver = tmp_path / "silver"
    mes_silver_variado(con, silver, 2025, 6)
    publicar(con, carga, tmp_path)

    programa = bloques_mes.resumen_mensual(con, silver, "A.BLANCAS_____013", (2025, 6), (2025, 6))
    base = carga.execute(
        'SELECT cuartos, "A", "B", "C", "Solar", "NoSolar", "Total" FROM cmg.cmg_mensual '
        "WHERE barra = 'A.BLANCAS_____013'"
    ).fetchone()
    assert base is not None
    fila = programa.iloc[0]
    assert base[0] == fila["cuartos"]
    for i, col in enumerate(["A", "B", "C", "Solar", "NoSolar", "Total"], start=1):
        assert base[i] == pytest.approx(fila[col], rel=1e-12), col


def test_gold_por_bloque_da_lo_mismo_que_el_gold_del_programa(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    from cmg_ingesta.gold import bloques_mes

    silver = tmp_path / "silver"
    mes_silver_variado(con, silver, 2025, 6)
    publicar(con, carga, tmp_path)

    programa = bloques_mes.resumen_por_bloque_detallado(
        con, silver, "A.BLANCAS_____013", (2025, 6), (2025, 6)
    ).set_index("bloque")
    filas = carga.execute(
        "SELECT bloque, cuartos, promedio, desv_estandar, minimo, maximo, en_cero "
        "FROM cmg.cmg_mensual_bloque WHERE barra = 'A.BLANCAS_____013' ORDER BY bloque"
    ).fetchall()
    assert [f[0] for f in filas] == ["A", "B", "C"]
    for bloque, cuartos, prom, desv, minimo, maximo, cero in filas:
        p = programa.loc[str(bloque)]
        assert (cuartos, cero) == (p["cuartos"], p["en_cero"])
        assert prom == pytest.approx(p["promedio"], rel=1e-12)
        assert desv == pytest.approx(p["desv_estandar"], rel=1e-12)
        assert (minimo, maximo) == (pytest.approx(p["minimo"]), pytest.approx(p["maximo"]))
    assert dict((f[0], f[6]) for f in filas)["B"] == 3 * 4 * 4  # 11..14 h en cero, 3 dias


def test_no_solar_no_es_el_promedio_de_los_promedios(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    mes_silver_variado(con, tmp_path / "silver", 2025, 6)
    publicar(con, carga, tmp_path)
    a, c, no_solar = carga.execute(
        "SELECT a, c, no_solar FROM cmg.mes_barra m JOIN cmg.barra b USING (barra_id) "
        "WHERE b.nombre = 'A.BLANCAS_____013'"
    ).fetchone() or (0.0, 0.0, 0.0)
    assert no_solar != pytest.approx((float(str(a)) + float(str(c))) / 2)


def test_gold_tiene_una_fila_por_barra_y_tres_por_bloque(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2025, 3, 50.0)
    publicar(con, carga, tmp_path)
    assert uno(carga, "SELECT count(*) FROM cmg.mes_barra") == 2
    assert uno(carga, "SELECT count(*) FROM cmg.mes_bloque") == 2 * 3


def test_una_revision_rehace_el_gold_del_mes(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    """Llega el definitivo: el promedio mensual y el aviso de preliminar se actualizan solos."""
    silver = tmp_path / "silver"
    mes_silver(con, silver, 2026, 8, 40.0, origen="pagina_cen_pre")
    publicar(con, carga, tmp_path)
    fila = carga.execute(
        'SELECT "Total", dias, dias_preliminar FROM cmg.cmg_mensual '
        "WHERE barra = 'A.BLANCAS_____013'"
    ).fetchone()
    assert fila == (40.0, 2, 2)

    mes_silver(con, silver, 2026, 8, 42.5, origen="pagina_cen_def")
    publicar(con, carga, tmp_path)
    fila = carga.execute(
        'SELECT "Total", dias, dias_preliminar FROM cmg.cmg_mensual '
        "WHERE barra = 'A.BLANCAS_____013'"
    ).fetchone()
    assert fila == (42.5, 2, 0)
    assert uno(carga, "SELECT count(*) FROM cmg.mes_barra") == 2  # reemplazado, no duplicado


def test_el_usuario_de_lectura_ve_el_gold(
    con: duckdb.DuckDBPyConnection, carga: Conn, base: str, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2025, 3, 50.0)
    publicar(con, carga, tmp_path)
    with pg.conectar(_url(base, "cmg_lectura")) as lectura:
        assert uno(lectura, "SELECT count(*) FROM cmg.cmg_mensual") == 2
        assert uno(lectura, "SELECT count(*) FROM cmg.cmg_mensual_bloque") == 6


# ---------------------------------------------------------- desde un mes (CMGI_DESDE)


def test_con_desde_no_se_suben_los_meses_anteriores(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    for anio, mes in [(2022, 11), (2022, 12), (2023, 1)]:
        mes_silver(con, tmp_path / "silver", anio, mes, 50.0)

    resultados = publicar(con, carga, tmp_path, desde=(2023, 1))

    assert [(r["anio"], r["mes"]) for r in resultados] == [(2023, 1)]
    assert pg.meses_en_la_base(carga) == [(2023, 1)]
    assert publicar(con, carga, tmp_path, desde=(2023, 1)) == []  # y no insiste


def test_quitar_anteriores_deja_la_base_como_si_nunca_se_hubieran_subido(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    silver = tmp_path / "silver"
    mes_silver(con, silver, 2022, 12, 40.0, barras=("VIEJA_________220", "A.BLANCAS_____013"))
    mes_silver(con, silver, 2023, 1, 50.0)
    publicar(con, carga, tmp_path)  # como la carga que se corto: todo Silver
    # un dia de 2022 que cambio de version, para que haya una revision que borrar
    mes_silver(con, silver, 2022, 12, 41.0, barras=("VIEJA_________220", "A.BLANCAS_____013"))
    publicar(con, carga, tmp_path)
    assert uno(carga, "SELECT count(*) FROM cmg.revision WHERE fecha < '2023-01-01'") == 2

    avisos: list[str] = []
    resultados = publicar(con, carga, tmp_path, desde=(2023, 1), quitar=True, avisar=avisos.append)

    assert resultados == []  # 2023-01 ya estaba: no se vuelve a subir
    assert "2022-12: quitado de la base" in avisos
    assert pg.meses_en_la_base(carga) == [(2023, 1)]
    assert uno(carga, "SELECT to_regclass('cmg.cuarto_2022_12')") is None
    for tabla in ("cmg_15min", "mes_barra", "mes_bloque", "dia", "revision"):
        n = uno(carga, f"SELECT count(*) FROM cmg.{tabla} WHERE fecha < '2023-01-01'")
        assert n == 0, tabla
    assert uno(carga, "SELECT count(*) FROM cmg.carga_mes WHERE anio = 2022") == 0
    # la barra que solo tenia datos en 2022 se va; las demas conservan su numero
    assert uno(carga, "SELECT count(*) FROM cmg.barra WHERE nombre = 'VIEJA_________220'") == 0
    assert uno(carga, "SELECT count(*) FROM cmg.cmg_15min WHERE fecha = '2023-01-01'") == 2 * 96
    assert uno(carga, "SELECT count(*) FROM cmg.cmg_mensual") == 2


def test_quitar_anteriores_borra_documentos_de_esos_dias(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    """Documentos solo hay desde 2024-08 (la pagina): se prueba con 'desde' 2024-09."""
    bronze = tmp_path / "bronze"
    viejo = _bronze_con(bronze, date(2024, 8, 1), "def")
    nuevo = _bronze_con(bronze, date(2024, 9, 1), "def")
    ingerir_cen.guardar_estado(
        bronze,
        {
            "2024-08": ingerir_cen.huella([(date(2024, 8, 1), viejo)]),
            "2024-09": ingerir_cen.huella([(date(2024, 9, 1), nuevo)]),
        },
    )
    mes_silver(con, tmp_path / "silver", 2024, 8, 40.0, dias=1)
    mes_silver(con, tmp_path / "silver", 2024, 9, 50.0, dias=1)
    publicar(con, carga, tmp_path)
    assert uno(carga, "SELECT count(*) FROM cmg.documento") == 2

    pg.quitar_anteriores(carga, (2024, 9))

    nombres = [f for (f,) in carga.execute("SELECT nombre FROM cmg.documento").fetchall()]
    assert nombres == [nuevo["nombre"]]
    assert uno(carga, "SELECT documento FROM cmg.dia") == nuevo["nombre"]


def test_sin_quitar_solo_avisa_que_hay_meses_anteriores(
    con: duckdb.DuckDBPyConnection, carga: Conn, tmp_path: Path
) -> None:
    mes_silver(con, tmp_path / "silver", 2022, 12, 40.0)
    mes_silver(con, tmp_path / "silver", 2023, 1, 50.0)
    publicar(con, carga, tmp_path)

    avisos: list[str] = []
    publicar(con, carga, tmp_path, desde=(2023, 1), avisar=avisos.append)

    assert pg.meses_en_la_base(carga) == [(2022, 12), (2023, 1)]  # nada se borro
    assert any("--quitar-anteriores" in a and "2022-12" in a for a in avisos)


def test_quitar_anteriores_es_todo_o_nada(
    con: duckdb.DuckDBPyConnection, base: str, carga: Conn, tmp_path: Path
) -> None:
    """Si el ULTIMO paso falla, lo ya borrado (particiones, Gold, dias) vuelve."""
    silver = tmp_path / "silver"
    mes_silver(con, silver, 2022, 11, 50.0, barras=("VIEJA_________220",))  # 1 barra
    mes_silver(con, silver, 2022, 12, 50.0)
    mes_silver(con, silver, 2023, 1, 50.0)
    publicar(con, carga, tmp_path)
    # algo ajeno al programa apunta a la barra vieja: borrarla (el ultimo paso) falla
    with psycopg.connect(f"{BASE} dbname={base}", autocommit=True) as admin:
        admin.execute(
            "CREATE TABLE public.ajena (barra_id smallint REFERENCES cmg.barra);"
            "INSERT INTO public.ajena SELECT barra_id FROM cmg.barra "
            "WHERE nombre = 'VIEJA_________220'"
        )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        pg.quitar_anteriores(carga, (2023, 1))

    assert pg.meses_en_la_base(carga) == [(2022, 11), (2022, 12), (2023, 1)]
    assert uno(carga, "SELECT count(*) FROM cmg.carga_mes") == 3
    assert uno(carga, "SELECT count(*) FROM cmg.mes_barra WHERE fecha < '2023-01-01'") == 3
    assert uno(carga, "SELECT count(*) FROM cmg.cmg_15min WHERE fecha < '2023-01-01'") == 6 * 96
