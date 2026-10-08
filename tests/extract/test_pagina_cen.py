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
    con: duckdb.DuckDBPyConnection, tmp_path: Path, forzar: bool = False
) -> tuple[list[ingerir_cen.FilaReporteCen], list[deriva.Hallazgo]]:
    reporte, hallazgos = ingerir_cen.ingerir_pagina(
        con, tmp_path / "bronze", tmp_path / "silver", tmp_path / "staging", forzar=forzar
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
