"""Tests del mapeo ZIP de la pagina -> esquema canonico, y de su orquestacion.

Los ZIP son sinteticos pero con la forma real (fixture `zip_cen` de conftest):
mismos miembros, mismo encabezado, valores PRE y DEF distintos a proposito para
que se note cual columna se leyo.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import pytest

from cmg_ingesta.domain import esquema
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import ingerir_cen, pagina_cen
from cmg_ingesta.quality import deriva
from cmg_ingesta.silver import escribir

ArmarZipCen = Callable[..., Path]


def entrada(dia: date, tipo: str, version: int = 0, reemision: int = 0) -> cen.EntradaManifiesto:
    nombre = f"Antecedentes_CMG_Real_{tipo}_{dia:%y%m%d}_v{version}-{reemision}.zip"
    return {
        "url": "https://x/" + nombre,
        "nombre": nombre,
        "tipo": tipo,
        "version": version,
        "reemision": reemision,
        "fecha_operacion": dia.isoformat(),
        "fecha_publicacion": None,
        "sha256": "x",
        "bytes": 1,
        "descargado_en": "2026-10-06T00:00:00",
    }


# ========================================================= elegir_por_dia


def test_def_gana_a_pre() -> None:
    d = date(2026, 9, 28)
    pre, defi = entrada(d, "pre"), entrada(d, "def")
    elegidos = pagina_cen.elegir_por_dia({pre["nombre"]: pre, defi["nombre"]: defi})
    assert elegidos[d]["tipo"] == "def"


def test_entre_iguales_gana_la_version_y_luego_la_reemision() -> None:
    d = date(2026, 9, 6)
    v0, v2, v2r1 = entrada(d, "pre"), entrada(d, "pre", 2), entrada(d, "pre", 2, 1)
    m = {e["nombre"]: e for e in (v2r1, v0, v2)}
    assert pagina_cen.elegir_por_dia(m)[d]["nombre"] == v2r1["nombre"]


def test_tipo_desconocido_no_se_usa() -> None:
    """Primero hay que catalogarlo: no se ingiere un archivo que no se entiende."""
    d = date(2026, 1, 15)
    e = entrada(d, "desconocido")
    assert pagina_cen.elegir_por_dia({e["nombre"]: e}) == {}


def test_antes_del_inicio_de_la_fuente_no_se_usa() -> None:
    """Hasta 2024-07 es del Maestro: la pagina no lo pisa aunque haya ZIP en el Bronze."""
    e = entrada(date(2024, 7, 31), "def")
    assert pagina_cen.elegir_por_dia({e["nombre"]: e}) == {}


def test_desde_agosto_2024_si_se_usa() -> None:
    e = entrada(date(2024, 8, 1), "def")
    assert list(pagina_cen.elegir_por_dia({e["nombre"]: e})) == [date(2024, 8, 1)]


# =================================================================== mapeo


def leer_dia(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    zip_cen: ArmarZipCen,
    dia: date,
    tipo: str,
    **kw: object,
) -> list[tuple[object, ...]]:
    ruta = zip_cen(tmp_path / f"{tipo}.zip", dia, **kw)
    csv = pagina_cen.extraer_comparativo(ruta, tmp_path)
    return con.execute(
        f"SELECT * FROM ({pagina_cen.sql_un_dia(csv, dia, tipo)}) ORDER BY barra, hora, minuto"
    ).fetchall()


def columnas(filas: list[tuple[object, ...]], nombre: str) -> list[object]:
    i = esquema.columnas_archivo().index(nombre)
    return [f[i] for f in filas]


def test_def_lee_la_columna_def(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    filas = leer_dia(con, tmp_path, zip_cen, date(2026, 6, 15), "def", pre=40.0, definitivo=50.0)
    assert set(columnas(filas, "cmg_usd_mwh")) == {50.0}
    assert set(columnas(filas, "origen")) == {esquema.ORIGEN_PAGINA_DEF}


def test_pre_lee_la_columna_pre_y_no_los_ceros_de_def(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    """LA trampa de la fuente: en un ZIP pre, CMG_REAL_DEF viene en 0 (real, 2026-09-28)."""
    filas = leer_dia(con, tmp_path, zip_cen, date(2026, 6, 15), "pre", pre=40.0, definitivo=0)
    assert set(columnas(filas, "cmg_usd_mwh")) == {40.0}
    assert set(columnas(filas, "origen")) == {esquema.ORIGEN_PAGINA_PRE}


def test_dia_normal_96_cuartos_por_barra(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    filas = leer_dia(con, tmp_path, zip_cen, date(2026, 6, 15), "def")
    assert len(filas) == 2 * 96
    assert columnas(filas, "fecha")[0] == date(2026, 6, 15)


def test_dia_largo_conserva_la_hora_24(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    """2026-04-04: 100 cuartos, la 24 es la segunda 23:00 (verificado en el ZIP real)."""
    d = date(2026, 4, 4)
    filas = leer_dia(con, tmp_path, zip_cen, d, "def", horas=list(range(25)))
    assert len(filas) == 2 * 100
    extra = [f for f in filas if columnas([f], "hora")[0] == 24]
    assert len(extra) == 2 * 4
    assert set(columnas(extra, "es_hora_extra")) == {True}
    assert set(columnas(extra, "fecha_hora")) == {None}  # seria ambigua
    assert set(columnas(extra, "bloque")) == {"A"}


def test_dia_corto_descarta_la_hora_fantasma(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    """2026-09-06: el CEN publica la hora 0 en 0,00, pero esa hora no existio."""
    d = date(2026, 9, 6)
    filas = leer_dia(con, tmp_path, zip_cen, d, "def", horas=list(range(24)))
    assert len(filas) == 2 * 92
    assert 0 not in columnas(filas, "hora")


def test_fecha_hora_se_arma_con_hora_y_minuto(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    from datetime import datetime

    filas = leer_dia(con, tmp_path, zip_cen, date(2026, 6, 15), "def", horas=[13])
    assert columnas(filas, "fecha_hora")[1] == datetime(2026, 6, 15, 13, 15)


def test_zip_sin_comparativo_falla_claro(tmp_path: Path) -> None:
    import zipfile

    ruta = tmp_path / "a.zip"
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr("otra_cosa.csv", "x")
    with pytest.raises(ValueError, match="CmgBarrasComparativo"):
        pagina_cen.extraer_comparativo(ruta, tmp_path)


def test_unir_sin_consultas_falla() -> None:
    with pytest.raises(ValueError):
        pagina_cen.unir([])


# ============================================================ orquestacion


@pytest.fixture
def bronze(tmp_path: Path, zip_cen: ArmarZipCen) -> Callable[..., Path]:
    """Devuelve una funcion que agrega un ZIP al Bronze y lo anota en el manifiesto."""
    carpeta = tmp_path / "bronze"
    carpeta.mkdir()

    def _agregar(dia: date, tipo: str = "def", **kw: object) -> Path:
        e = entrada(dia, tipo)
        ruta = zip_cen(carpeta / e["nombre"], dia, **kw)
        e["sha256"] = cen.sha256_bytes(ruta.read_bytes())
        m = cen.leer_manifiesto(carpeta)
        m[e["nombre"]] = e
        cen.guardar_manifiesto(carpeta, m)
        return ruta

    return _agregar


def correr(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    forzar: bool = False,
    conservar_zip: bool = True,
) -> tuple[list[ingerir_cen.FilaReporteCen], list[deriva.Hallazgo]]:
    reporte, hallazgos = ingerir_cen.ingerir_pagina(
        con,
        tmp_path / "bronze",
        tmp_path / "silver",
        tmp_path / "staging",
        forzar=forzar,
        conservar_zip=conservar_zip,
    )
    return reporte, hallazgos


def silver(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> list[tuple[object, ...]]:
    patron = str(tmp_path / "silver" / "**" / "*.parquet")
    return con.execute(
        f"SELECT fecha, origen, min(cmg_usd_mwh), count(*) "
        f"FROM read_parquet('{patron}', hive_partitioning=true) GROUP BY ALL ORDER BY fecha"
    ).fetchall()


def test_ingesta_de_un_mes(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 6, 15))
    bronze(date(2026, 6, 16), "pre")
    reporte, hallazgos = correr(con, tmp_path)

    assert len(reporte) == 1
    fila = reporte[0]
    assert (fila["anio"], fila["mes"], fila["dias"], fila["dias_pre"]) == (2026, 6, 2, 1)
    assert fila["filas"] == 2 * 2 * 96
    assert not ingerir_cen.hay_problemas(reporte)
    assert hallazgos == []
    assert silver(con, tmp_path) == [
        (date(2026, 6, 15), esquema.ORIGEN_PAGINA_DEF, 50.0, 192),
        (date(2026, 6, 16), esquema.ORIGEN_PAGINA_PRE, 40.0, 192),
    ]
    assert not any((tmp_path / "staging").iterdir())  # los CSV temporales se borraron


def test_segunda_corrida_sin_cambios_no_reescribe(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 6, 15))
    correr(con, tmp_path)
    reporte, _ = correr(con, tmp_path)
    assert reporte == []


def test_forzar_reescribe_aunque_no_haya_cambios(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 6, 15))
    correr(con, tmp_path)
    reporte, _ = correr(con, tmp_path, forzar=True)
    assert len(reporte) == 1


def test_cuando_llega_el_def_el_mes_se_reescribe_con_el(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    """El caso diario: hoy hay pre; en 8 dias llega el def y lo reemplaza solo."""
    d = date(2026, 6, 15)
    bronze(d, "pre", pre=40.0)
    correr(con, tmp_path)
    assert silver(con, tmp_path)[0][1:3] == (esquema.ORIGEN_PAGINA_PRE, 40.0)

    bronze(d, "def", definitivo=55.0)
    reporte, _ = correr(con, tmp_path)
    assert len(reporte) == 1
    assert silver(con, tmp_path) == [(d, esquema.ORIGEN_PAGINA_DEF, 55.0, 192)]


def test_solo_se_reescriben_los_meses_que_cambiaron(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 5, 31))
    bronze(date(2026, 6, 1))
    correr(con, tmp_path)
    bronze(date(2026, 6, 2))
    reporte, _ = correr(con, tmp_path)
    assert [(f["anio"], f["mes"]) for f in reporte] == [(2026, 6)]


def test_un_dia_con_hallazgo_critico_no_entra_y_se_reintenta(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    """Horas 1..24 en un dia normal = base 1: entraria todo corrido una hora."""
    bronze(date(2026, 6, 15))
    bronze(date(2026, 6, 16), horas=list(range(1, 25)))
    reporte, hallazgos = correr(con, tmp_path)

    fila = reporte[0]
    assert fila["dias"] == 1
    assert len(fila["omitidos"]) == 1 and "2026-06-16" in fila["omitidos"][0]
    assert ingerir_cen.hay_problemas(reporte)
    assert [x[0] for x in silver(con, tmp_path)] == [date(2026, 6, 15)]
    assert any(h["tipo"] == "convencion_hora_base_1" for h in hallazgos)

    # sin huella guardada: la proxima corrida lo vuelve a intentar
    reporte2, _ = correr(con, tmp_path)
    assert len(reporte2) == 1


def test_el_dia_corto_reporta_la_hora_fantasma_descartada(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 9, 6), horas=list(range(24)))
    reporte, _ = correr(con, tmp_path)
    assert reporte[0]["hora_fantasma"] == 2 * 4
    assert reporte[0]["filas"] == 2 * 92
    assert reporte[0]["dias_mal"] == 0  # descartada, el dia queda con su largo correcto
    assert not ingerir_cen.hay_problemas(reporte)


def test_el_dia_largo_entra_completo(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 4, 4), horas=list(range(25)))
    reporte, _ = correr(con, tmp_path)
    assert reporte[0]["filas"] == 2 * 100
    assert reporte[0]["dias_mal"] == 0


def test_un_zip_que_falta_en_disco_se_omite(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 6, 15))
    bronze(date(2026, 6, 16)).unlink()
    reporte, _ = correr(con, tmp_path)
    assert "no en disco" in reporte[0]["omitidos"][0]


def test_sin_archivos_desde_el_inicio_falla_y_explica(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2024, 7, 31))
    with pytest.raises(ValueError, match="descargar-cen"):
        correr(con, tmp_path)


def test_estado_corrupto_reingiere_todo(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 6, 15))
    correr(con, tmp_path)
    (tmp_path / "bronze" / ingerir_cen.ESTADO).write_text("{roto", encoding="utf-8")
    reporte, _ = correr(con, tmp_path)
    assert len(reporte) == 1


def test_la_pagina_usa_los_mismos_nombres_que_el_historico(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    """Si la pagina volviera a publicar con Ñ, la serie no se parte."""
    filas = leer_dia(
        con, tmp_path, zip_cen, date(2026, 6, 15), "def", barras=("PEÑABLANCA____013",)
    )
    assert set(columnas(filas, "barra")) == {"PENABLANCA____013"}


def test_un_dia_con_archivo_sin_clasificar_no_se_ingiere_y_se_reporta(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    """El archivo raro podria ser la version vigente: no se elige otro EN SILENCIO."""
    bronze(date(2026, 6, 15))
    bronze(date(2026, 6, 16))
    m = cen.leer_manifiesto(tmp_path / "bronze")
    raro = entrada(date(2026, 6, 16), "desconocido")
    m[raro["nombre"]] = raro
    cen.guardar_manifiesto(tmp_path / "bronze", m)

    reporte, _ = correr(con, tmp_path)

    assert [x[0] for x in silver(con, tmp_path)] == [date(2026, 6, 15)]
    assert any("2026-06-16" in o and "sin clasificar" in o for o in reporte[0]["omitidos"])
    assert ingerir_cen.hay_problemas(reporte)


def test_elegir_por_dia_desempata_por_fecha_de_publicacion() -> None:
    d = date(2026, 6, 15)
    a, b = entrada(d, "def", 3), entrada(d, "def", 3, reemision=0)
    a["nombre"], b["nombre"] = "a.zip", "b.zip"
    a["fecha_publicacion"], b["fecha_publicacion"] = "2026-08-17", "2026-08-25"
    assert pagina_cen.elegir_por_dia({"a": a, "b": b})[d]["nombre"] == "b.zip"


# ============================================== testigo en lugar del ZIP (poco disco)


def liviano(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, forzar: bool = False
) -> tuple[list[ingerir_cen.FilaReporteCen], list[deriva.Hallazgo]]:
    """Como en un equipo con CMGI_CONSERVAR_ZIP=false."""
    return correr(con, tmp_path, forzar=forzar, conservar_zip=False)


def archivos(tmp_path: Path) -> list[str]:
    return sorted(r.name for r in (tmp_path / "bronze").iterdir() if r.suffix == ".zip")


def ingerido_en(con: duckdb.DuckDBPyConnection, tmp_path: Path, dia: date) -> object:
    patron = str(tmp_path / "silver" / "**" / "*.parquet")
    fila = con.execute(
        f"SELECT min(ingerido_en) FROM read_parquet('{patron}') WHERE fecha = DATE '{dia}'"
    ).fetchone()
    assert fila is not None
    return fila[0]


def test_por_defecto_los_zip_se_conservan(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    zip_ = bronze(date(2026, 6, 15))
    correr(con, tmp_path)
    assert zip_.exists()
    assert not ingerir_cen.ruta_testigo(tmp_path / "bronze", zip_.name).exists()


def test_sin_conservar_cada_zip_queda_como_testigo(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    zip_ = bronze(date(2026, 6, 15))
    sha = cen.sha256_bytes(zip_.read_bytes())

    liviano(con, tmp_path)

    assert not zip_.exists()
    testigo = ingerir_cen.leer_testigo(tmp_path / "bronze", zip_.name)
    assert testigo is not None
    assert (testigo["sha256"], testigo["uso"], testigo["tipo"]) == (sha, "silver", "def")
    assert ingerir_cen.ruta_testigo(tmp_path / "bronze", zip_.name).stat().st_size < 1000
    assert silver(con, tmp_path) == [(date(2026, 6, 15), esquema.ORIGEN_PAGINA_DEF, 50.0, 192)]
    # el manifiesto no cambia: la descarga sigue sabiendo que ese archivo ya se bajo
    assert zip_.name in cen.leer_manifiesto(tmp_path / "bronze")


def test_con_testigos_la_segunda_corrida_no_hace_nada(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 6, 15))
    liviano(con, tmp_path)
    reporte, _ = liviano(con, tmp_path)
    assert reporte == []


def test_una_revision_de_otro_dia_reusa_el_silver_de_los_dias_con_testigo(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    """El caso diario en un equipo con poco disco: llega el def de un dia del mes."""
    d15, d16 = date(2026, 6, 15), date(2026, 6, 16)
    bronze(d15, definitivo=50.0)
    bronze(d16, "pre", pre=40.0)
    liviano(con, tmp_path)
    antes = ingerido_en(con, tmp_path, d15)
    assert archivos(tmp_path) == []  # solo testigos

    bronze(d16, "def", definitivo=55.0)
    reporte, _ = liviano(con, tmp_path)

    assert reporte[0]["dias"] == 2
    assert reporte[0]["desde_silver"] == 1
    assert not ingerir_cen.hay_problemas(reporte)
    assert silver(con, tmp_path) == [
        (d15, esquema.ORIGEN_PAGINA_DEF, 50.0, 192),
        (d16, esquema.ORIGEN_PAGINA_DEF, 55.0, 192),
    ]
    assert ingerido_en(con, tmp_path, d15) == antes  # el dia copiado conserva su linaje
    assert archivos(tmp_path) == []


def test_la_version_superada_tambien_queda_como_testigo(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    d = date(2026, 6, 15)
    pre = bronze(d, "pre")
    defi = bronze(d, "def")
    liviano(con, tmp_path)

    usos = {
        z.name: t["uso"]
        for z in (pre, defi)
        if (t := ingerir_cen.leer_testigo(tmp_path / "bronze", z.name)) is not None
    }
    assert usos == {pre.name: "superado", defi.name: "silver"}
    assert archivos(tmp_path) == []


def test_un_dia_omitido_conserva_su_zip(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    """Hace falta para reintentar cuando el catalogo este corregido."""
    bueno = bronze(date(2026, 6, 15))
    malo = bronze(date(2026, 6, 16), horas=list(range(1, 25)))
    liviano(con, tmp_path)
    assert archivos(tmp_path) == [malo.name]
    assert not bueno.exists()


def test_un_dia_sin_clasificar_conserva_sus_zip(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 6, 15))
    del_16 = bronze(date(2026, 6, 16))
    m = cen.leer_manifiesto(tmp_path / "bronze")
    raro = entrada(date(2026, 6, 16), "desconocido")
    m[raro["nombre"]] = raro
    cen.guardar_manifiesto(tmp_path / "bronze", m)

    liviano(con, tmp_path)

    assert archivos(tmp_path) == [del_16.name]


def test_forzar_con_testigos_rehace_el_mes_desde_silver(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2026, 6, 15))
    bronze(date(2026, 6, 16), "pre")
    liviano(con, tmp_path)
    antes = silver(con, tmp_path)

    reporte, _ = liviano(con, tmp_path, forzar=True)

    assert reporte[0]["desde_silver"] == 2
    assert silver(con, tmp_path) == antes
    assert not ingerir_cen.hay_problemas(reporte)


def test_testigo_sin_el_dia_en_silver_se_omite_y_no_borra_nada(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    """Si Silver se perdio, el testigo no basta: se avisa que hay que volver a bajarlo."""
    d15, d16 = date(2026, 6, 15), date(2026, 6, 16)
    bronze(d15)
    liviano(con, tmp_path)
    archivo = escribir.ruta_particion(tmp_path / "silver", 2026, 6) / escribir.NOMBRE_ARCHIVO
    archivo.unlink()
    bronze(d16)

    reporte, _ = liviano(con, tmp_path)

    assert any(str(d15) in o and "volver a descargarlo" in o for o in reporte[0]["omitidos"])
    assert ingerir_cen.hay_problemas(reporte)
    assert [x[0] for x in silver(con, tmp_path)] == [d16]


def test_un_zip_distinto_del_manifiesto_no_se_borra(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    zip_ = bronze(date(2026, 6, 15))
    m = cen.leer_manifiesto(tmp_path / "bronze")
    e = m[zip_.name]
    e["sha256"] = "0" * 64

    assert ingerir_cen.dejar_testigo(tmp_path / "bronze", e, "silver") is False
    assert zip_.exists()
    assert not ingerir_cen.ruta_testigo(tmp_path / "bronze", zip_.name).exists()


def test_un_testigo_ilegible_no_cuenta(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    zip_ = bronze(date(2026, 6, 15))
    liviano(con, tmp_path)
    ingerir_cen.ruta_testigo(tmp_path / "bronze", zip_.name).write_text("{roto", encoding="utf-8")

    reporte, _ = liviano(con, tmp_path, forzar=True)

    assert "no en disco" in reporte[0]["omitidos"][0]


# ============================================ formato 2024-08/09 (error A18)


def test_def_de_2024_lee_cmgr(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    filas = leer_dia(
        con, tmp_path, zip_cen, date(2024, 8, 14), "def", pre=40.0, definitivo=50.0, formato="2024"
    )
    assert set(columnas(filas, "cmg_usd_mwh")) == {50.0}
    assert len(filas) == 2 * 96


def test_pre_de_2024_lee_cmgp(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    filas = leer_dia(
        con, tmp_path, zip_cen, date(2024, 8, 14), "pre", pre=40.0, definitivo=0.0, formato="2024"
    )
    assert set(columnas(filas, "cmg_usd_mwh")) == {40.0}


def test_un_csv_sin_ninguna_columna_conocida_falla_claro(tmp_path: Path) -> None:
    csv = tmp_path / "x.csv"
    csv.write_text("FECHA;HORA;MINUTO;BARRA;OTRA\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CMgR"):
        pagina_cen.columna_valor(csv, "def")


def test_un_mes_con_los_dos_formatos_entra_completo(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    """Septiembre 2024: hasta el 29 llegan ZIP viejos y desde el 30 nuevos."""
    bronze(date(2024, 9, 29), definitivo=50.0, formato="2024")
    bronze(date(2024, 9, 30), definitivo=55.0)
    reporte, hallazgos = correr(con, tmp_path)
    assert not ingerir_cen.hay_problemas(reporte)
    assert [h for h in hallazgos if h["severidad"] == "critico"] == []
    assert silver(con, tmp_path) == [
        (date(2024, 9, 29), esquema.ORIGEN_PAGINA_DEF, 50.0, 192),
        (date(2024, 9, 30), esquema.ORIGEN_PAGINA_DEF, 55.0, 192),
    ]


def test_la_ingesta_avisa_cada_dia_que_lee(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    """Un mes tarda ~30 s: sin avisos por dia, parece pegado."""
    bronze(date(2026, 6, 15))
    bronze(date(2026, 6, 16), horas=list(range(1, 25)))
    avisos: list[str] = []
    ingerir_cen.ingerir_pagina(
        con, tmp_path / "bronze", tmp_path / "silver", tmp_path / "staging", avisar=avisos.append
    )
    assert "2026-06: 2 dia(s) por leer" in avisos
    assert "  [1/2] 2026-06-15 def" in avisos
    assert "  [2/2] 2026-06-16 def  [!] hallazgo critico, no entra" in avisos
    assert avisos.index("2026-06: escribiendo Silver...") > avisos.index("  [1/2] 2026-06-15 def")


def test_un_zip_trabado_por_windows_se_queda_y_no_corta_la_ingesta(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    bronze: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El antivirus puede tener abierto el ZIP recien leido: no se borra, y no es error."""
    zip_ = bronze(date(2026, 6, 15))

    def trabado(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("en uso por otro proceso")

    monkeypatch.setattr(Path, "unlink", trabado)
    monkeypatch.setattr("cmg_ingesta.extract.ingerir_cen.time.sleep", lambda _s: None)
    reporte, _ = liviano(con, tmp_path)
    monkeypatch.undo()

    assert zip_.exists()
    assert not ingerir_cen.hay_problemas(reporte)
    assert silver(con, tmp_path) == [(date(2026, 6, 15), esquema.ORIGEN_PAGINA_DEF, 50.0, 192)]


# ======================================= variantes reales de 2025-02..2026-09 (error A19)


def test_un_zip_con_carpeta_adentro_se_lee_igual(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2025, 3, 1), definitivo=50.0, carpeta="Antecedentes_CMG_Real_def")
    reporte, hallazgos = correr(con, tmp_path)
    assert not ingerir_cen.hay_problemas(reporte)
    assert [h for h in hallazgos if h["severidad"] != "info"] == []
    assert silver(con, tmp_path) == [(date(2025, 3, 1), esquema.ORIGEN_PAGINA_DEF, 50.0, 192)]


def test_el_comparativo_nuevo_se_lee_igual(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    bronze(date(2025, 2, 9), "pre", pre=40.0, sufijo="_NUEVO")
    reporte, _ = correr(con, tmp_path)
    assert not ingerir_cen.hay_problemas(reporte)
    assert silver(con, tmp_path) == [(date(2025, 2, 9), esquema.ORIGEN_PAGINA_PRE, 40.0, 192)]


@pytest.mark.parametrize("tipo", ["def", "pre"])
def test_una_revision_con_columnas_v2_usa_la_revisada(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen, tipo: str
) -> None:
    """def_v2_250201: CMG_REAL_DEF repite el v1; CMG_REAL_DEF v2 trae lo revisado."""
    filas = leer_dia(
        con, tmp_path, zip_cen, date(2025, 2, 1), tipo, pre=40.0, definitivo=50.0, v2=(41.0, 51.0)
    )
    assert set(columnas(filas, "cmg_usd_mwh")) == {51.0 if tipo == "def" else 41.0}


def test_una_revision_con_las_columnas_al_reves_usa_la_ultima(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, zip_cen: ArmarZipCen
) -> None:
    """pre-v2_250228: `PRE v2, DEF v2, PRE, DEF`; lo revisado es la ultima, SIN "v2"."""
    filas = leer_dia(
        con,
        tmp_path,
        zip_cen,
        date(2025, 2, 8),
        "pre",
        pre=40.0,
        definitivo=50.0,
        v2=(41.0, 51.0),
        v2_al_reves=True,
    )
    assert set(columnas(filas, "cmg_usd_mwh")) == {41.0}


def test_las_columnas_v2_no_se_avisan_como_nuevas(tmp_path: Path, zip_cen: ArmarZipCen) -> None:
    d = date(2025, 2, 1)
    ruta = zip_cen(tmp_path / "a.zip", d, v2=(41.0, 51.0))
    assert deriva.revisar_zip(ruta, d) == []


def test_cada_hallazgo_de_un_zip_dice_cual_es(tmp_path: Path, zip_cen: ArmarZipCen) -> None:
    """Pedido del usuario: para poder ir a buscar el ZIP con el problema."""
    d = date(2026, 6, 16)
    ruta = zip_cen(tmp_path / "Antecedentes_CMG_Real_def_260616.zip", d, horas=list(range(1, 25)))
    hallazgos = deriva.revisar_zip(ruta, d)
    assert hallazgos
    assert {h.get("archivo") for h in hallazgos} == {"Antecedentes_CMG_Real_def_260616.zip"}


def test_un_dia_omitido_dice_que_zip_fue(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bronze: Callable[..., Path]
) -> None:
    malo = bronze(date(2026, 6, 16), horas=list(range(1, 25)))
    reporte, _ = correr(con, tmp_path)
    assert malo.name in reporte[0]["omitidos"][0]


def test_cambiar_las_reglas_reingiere_los_meses(
    con: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    bronze: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silver depende del Bronze Y de como se lee: al corregir una regla, se rehace."""
    bronze(date(2026, 6, 15))
    correr(con, tmp_path)
    assert correr(con, tmp_path)[0] == []
    monkeypatch.setattr(ingerir_cen, "REGLAS", "otra")
    assert len(correr(con, tmp_path)[0]) == 1
