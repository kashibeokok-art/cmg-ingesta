"""Tests de la migracion del historico, con un CMG_DB falso en miniatura.

No se toca la base real: se construye una de juguete en `tmp_path` con el mismo
formato Hive y el mismo esquema de columnas que tiene la de verdad.
"""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from cmg_ingesta.domain import esquema
from cmg_ingesta.extract import cmg_db, migrar
from cmg_ingesta.silver import escribir


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def crear_cmg_db_falso(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    meses: list[tuple[int, int]],
    horas: list[int] | None = None,
) -> None:
    """Escribe un CMG_DB de juguete: 1 barra, el dia 1 de cada mes, 4 cuartos/hora.

    Usa las MISMAS columnas que la base antigua, incluido `cmg_usd_mwh` en FLOAT.
    """
    rango = horas if horas is not None else list(range(24))
    lista = ", ".join(str(h) for h in rango)
    for anio, mes in meses:
        carpeta = base / f"anio={anio}" / f"mes={mes}"
        carpeta.mkdir(parents=True, exist_ok=True)
        destino = str(carpeta / "data.parquet").replace("'", "''")
        con.execute(f"""
            COPY (
                SELECT 'BARRA_X' AS barra,
                       DATE '{anio}-{mes:02d}-01' AS fecha,
                       CAST(h AS UTINYINT) AS hora,
                       CAST(m AS UTINYINT) AS minuto,
                       'A' AS bloque,
                       CAST(49.10532 AS FLOAT) AS cmg_usd_mwh,
                       CAST(NULL AS TIMESTAMP) AS fecha_hora
                FROM (SELECT unnest([{lista}]) AS h) t,
                     (SELECT unnest([0, 15, 30, 45]) AS m) u
            ) TO '{destino}' (FORMAT PARQUET)
        """)


# ------------------------------------------------------------------ rutas


def test_ruta_mes(tmp_path: Path) -> None:
    assert cmg_db.ruta_mes(tmp_path, 2023, 5) == tmp_path / "anio=2023" / "mes=5"


def test_mes_disponible(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    crear_cmg_db_falso(con, tmp_path, [(2023, 5)])
    assert cmg_db.mes_disponible(tmp_path, 2023, 5)
    assert not cmg_db.mes_disponible(tmp_path, 2023, 6)


def test_meses_a_migrar_recorta_al_tramo_del_maestro(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """2025 en adelante NO se migra: esa fuente queda descartada."""
    crear_cmg_db_falso(con, tmp_path, [(2020, 12), (2021, 1), (2024, 12), (2025, 1)])
    assert cmg_db.meses_a_migrar(tmp_path) == [(2021, 1), (2024, 12)]


def test_meses_a_migrar_vacio(tmp_path: Path) -> None:
    assert cmg_db.meses_a_migrar(tmp_path) == []


def test_rango_de_fechas_cubre_el_mes_completo() -> None:
    desde, hasta = cmg_db.rango_de_fechas([(2021, 1), (2024, 12)])
    assert desde == date(2021, 1, 1)
    assert hasta == date(2024, 12, 31)


def test_rango_de_fechas_con_febrero_bisiesto() -> None:
    _, hasta = cmg_db.rango_de_fechas([(2024, 2)])
    assert hasta == date(2024, 2, 29)


def test_rango_de_fechas_sin_meses_falla() -> None:
    with pytest.raises(ValueError, match="no hay meses"):
        cmg_db.rango_de_fechas([])


# ------------------------------------------------------------- el mapeo SQL


def test_el_mapeo_produce_las_columnas_del_contrato(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    crear_cmg_db_falso(con, tmp_path, [(2023, 5)])
    sql = cmg_db.sql_desde_cmg_db(tmp_path, 2023, 5)
    columnas = [d[0] for d in con.execute(f"DESCRIBE SELECT * FROM ({sql})").fetchall()]
    assert columnas == esquema.columnas_archivo()


def test_el_mapeo_recalcula_el_bloque(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """La base falsa dice 'A' en todas las horas; el mapeo NO le cree."""
    crear_cmg_db_falso(con, tmp_path, [(2023, 5)])
    sql = cmg_db.sql_desde_cmg_db(tmp_path, 2023, 5)
    filas = con.execute(
        f"SELECT DISTINCT hora, bloque FROM ({sql}) WHERE hora IN (7, 8, 17, 18, 22, 23) "
        "ORDER BY hora"
    ).fetchall()
    assert filas == [(7, "A"), (8, "B"), (17, "B"), (18, "C"), (22, "C"), (23, "A")]


def test_el_mapeo_marca_la_hora_extra(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    crear_cmg_db_falso(con, tmp_path, [(2023, 4)], horas=[23, 24])
    sql = cmg_db.sql_desde_cmg_db(tmp_path, 2023, 4)
    filas = con.execute(
        f"SELECT DISTINCT hora, es_hora_extra, fecha_hora IS NULL FROM ({sql}) ORDER BY hora"
    ).fetchall()
    assert filas == [(23, False, False), (24, True, True)]


def test_el_mapeo_agrega_linaje(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    crear_cmg_db_falso(con, tmp_path, [(2023, 5)])
    sql = cmg_db.sql_desde_cmg_db(tmp_path, 2023, 5)
    fila = con.execute(f"SELECT DISTINCT origen, ingerido_en IS NOT NULL FROM ({sql})").fetchone()
    assert fila == (esquema.ORIGEN_MAESTRO, True)


# ------------------------------------------------------------- la migracion


def test_migracion_completa(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    origen, destino = tmp_path / "vieja", tmp_path / "nueva"
    crear_cmg_db_falso(con, origen, [(2023, 5), (2023, 6)])

    reporte = migrar.migrar_historico(con, origen, destino)

    assert len(reporte) == 2
    assert escribir.meses_escritos(destino) == [(2023, 5), (2023, 6)]
    tot = migrar.totales(reporte)
    assert tot["filas"] == 2 * 96


def test_migracion_es_idempotente(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    origen, destino = tmp_path / "vieja", tmp_path / "nueva"
    crear_cmg_db_falso(con, origen, [(2023, 5)])

    migrar.migrar_historico(con, origen, destino)
    migrar.migrar_historico(con, origen, destino)

    assert escribir.filas_en_particion(con, destino, 2023, 5) == 96


def test_migracion_reporta_el_dia_corto_de_septiembre(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """2023-09-03 es el dia de 23 horas; con 24 horas de datos sobra la hora 0."""
    origen, destino = tmp_path / "vieja", tmp_path / "nueva"
    carpeta = origen / "anio=2023" / "mes=9"
    carpeta.mkdir(parents=True)
    salida = str(carpeta / "data.parquet").replace("'", "''")
    con.execute(f"""
        COPY (
            SELECT 'BARRA_X' AS barra, DATE '2023-09-03' AS fecha,
                   CAST(h AS UTINYINT) AS hora, CAST(m AS UTINYINT) AS minuto,
                   'A' AS bloque, CAST(0.0 AS FLOAT) AS cmg_usd_mwh,
                   CAST(NULL AS TIMESTAMP) AS fecha_hora
            FROM (SELECT unnest(range(24)) AS h) t,
                 (SELECT unnest([0, 15, 30, 45]) AS m) u
        ) TO '{salida}' (FORMAT PARQUET)
    """)

    reporte = migrar.migrar_historico(con, origen, destino)
    assert reporte[0]["dias_mal"] == 1
    assert reporte[0]["hora_fantasma"] == 4


def test_migracion_sin_datos_falla(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no se encontro ningun mes"):
        migrar.migrar_historico(con, tmp_path / "vacia", tmp_path / "nueva")


def test_migracion_limpia_temporales_huerfanas(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    origen, destino = tmp_path / "vieja", tmp_path / "nueva"
    crear_cmg_db_falso(con, origen, [(2023, 5)])
    huerfana = destino / "anio=2023" / f"mes=5{escribir.SUFIJO_TMP}"
    huerfana.mkdir(parents=True)

    avisos: list[str] = []
    migrar.migrar_historico(con, origen, destino, avisar=avisos.append)

    assert not huerfana.exists()
    assert any("temporal" in a for a in avisos)
