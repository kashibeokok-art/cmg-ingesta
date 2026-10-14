"""Las consultas de la app contra un PostgreSQL de pruebas (ver conftest.py).

Lo mas importante: los promedios por bloque y el riesgo nodal tienen que dar lo
MISMO que el Gold del programa (`gold/bloques_mes.py`, `gold/riesgo_nodal.py`),
que se calcula en DuckDB sobre Silver. Son dos implementaciones de la misma
regla; si alguna vez divergen, uno de los dos numeros que ve el usuario esta mal.
"""

from datetime import date, datetime
from pathlib import Path

import duckdb
import psycopg
import pytest

from cmg_ingesta.consulta import datos
from cmg_ingesta.gold import bloques_mes, riesgo_nodal

Conn = psycopg.Connection[tuple[object, ...]]
REF, COMP = "A.BLANCAS_____013", "STA.ELVIRA____013"
MARZO = (date(2025, 3, 1), date(2025, 3, 31))


def test_barras_y_rango(lectura: Conn) -> None:
    assert datos.barras(lectura) == ["A.BLANCAS_____013", "QUELLON_______013", "STA.ELVIRA____013"]
    assert datos.rango_disponible(lectura) == (date(2025, 3, 1), date(2025, 4, 10))


def test_la_conexion_de_la_app_no_puede_escribir(lectura: Conn) -> None:
    """Dos protecciones: la sesion es de solo lectura, y el usuario tampoco tiene permiso."""
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        lectura.execute("DELETE FROM cmg.dia")
    assert lectura.execute("SHOW default_transaction_read_only").fetchone() == ("on",)
    puede = lectura.execute(
        "SELECT has_table_privilege('cmg.dia', 'DELETE'), has_table_privilege('cmg.dia', 'SELECT')"
    ).fetchone()
    assert puede == (False, True)


def test_serie_del_rango_pedido(lectura: Conn) -> None:
    serie = datos.serie_cmg(lectura, REF, date(2025, 3, 10), date(2025, 3, 12))
    assert len(serie) == 3 * 96
    assert serie["fecha"].min() == date(2025, 3, 10) and serie["fecha"].max() == date(2025, 3, 12)
    assert set(serie["version"]) == {"Definitivo"}  # sin documento: el origen
    primera = serie.iloc[0]
    assert (primera["hora"], primera["minuto"], primera["fecha_hora"]) == (
        0,
        0,
        datetime(2025, 3, 10, 0, 0),
    )


def test_el_dia_de_25_horas_trae_la_hora_24_despues_de_la_23(lectura: Conn) -> None:
    serie = datos.serie_cmg(lectura, REF, date(2025, 4, 5), date(2025, 4, 5))
    assert len(serie) == 100
    ultimas = serie.tail(5)
    assert list(ultimas["hora"]) == [23, 24, 24, 24, 24]
    assert ultimas["fecha_hora"].iloc[1:].isna().all()  # la segunda 23:00 es ambigua
    assert ultimas["es_hora_extra"].iloc[1:].all()


def _gold_marzo(con: duckdb.DuckDBPyConnection, silver: Path, barra: str) -> dict[str, float]:
    df = bloques_mes.resumen_mensual(con, silver, barra, (2025, 3), (2025, 3))
    return {k: float(df.iloc[0][k]) for k in ("A", "B", "C", "Solar", "NoSolar", "Total")}


@pytest.mark.parametrize("barra", [REF, COMP])
def test_bloques_de_un_mes_completo_igual_al_gold(lectura: Conn, silver: Path, barra: str) -> None:
    con = duckdb.connect()
    gold = _gold_marzo(con, silver, barra)
    app = datos.bloques(lectura, barra, *MARZO)
    fila = app[app["periodo"] == "2025-03"].iloc[0]
    for k, v in gold.items():
        assert float(fila[k]) == pytest.approx(v, rel=1e-12), k


def test_bloques_por_mes_y_rango_completo(lectura: Conn) -> None:
    """Un rango que corta dos meses: cada fila dice que dias cubre."""
    df = datos.bloques(lectura, REF, date(2025, 3, 20), date(2025, 4, 3))
    assert list(df["periodo"]) == ["2025-03", "2025-04", datos.RANGO]
    assert list(df["desde"]) == [date(2025, 3, 20), date(2025, 4, 1), date(2025, 3, 20)]
    assert list(df["hasta"]) == [date(2025, 3, 31), date(2025, 4, 3), date(2025, 4, 3)]
    assert list(df["cuartos"]) == [12 * 96, 3 * 96, 15 * 96]
    serie = datos.serie_cmg(lectura, REF, date(2025, 3, 20), date(2025, 4, 3))
    rango = df.iloc[-1]
    assert float(rango["Total"]) == pytest.approx(float(serie["cmg_usd_mwh"].mean()), rel=1e-12)


def test_no_solar_es_el_promedio_de_los_cuartos_no_de_los_promedios(lectura: Conn) -> None:
    fila = datos.bloques(lectura, REF, *MARZO).iloc[0]
    promedio_de_promedios = (float(fila["A"]) + float(fila["C"])) / 2
    assert float(fila["NoSolar"]) != pytest.approx(promedio_de_promedios, rel=1e-6)
    serie = datos.serie_cmg(lectura, REF, *MARZO)
    ac = serie[serie["bloque"].isin(["A", "C"])]["cmg_usd_mwh"]
    assert float(fila["NoSolar"]) == pytest.approx(float(ac.mean()), rel=1e-12)


def test_estadisticas_por_bloque(lectura: Conn) -> None:
    df = datos.estadisticas_bloque(lectura, REF, *MARZO)
    assert list(df["bloque"]) == ["A", "B", "C"]
    en_cero = dict(zip(df["bloque"], df["en_cero"], strict=True))
    assert en_cero["B"] == 31 * 4 * 4  # 11 a 14 h en cero, 31 dias
    assert en_cero["A"] == 0


def test_riesgo_igual_al_gold(lectura: Conn, silver: Path) -> None:
    con = duckdb.connect()
    gold = riesgo_nodal.resumen_riesgo(con, silver, REF, COMP, (2025, 3), (2025, 3))
    app = datos.resumen_riesgo(lectura, REF, COMP, *MARZO)
    app_mes = app[app["periodo"] == "2025-03"].set_index("bloque")
    for _, g in gold.iterrows():
        a = app_mes.loc[g["bloque"]]
        for k in ("cmg_referencia", "cmg_comparada", "riesgo_usd_mwh", "riesgo_pct"):
            assert float(a[k]) == pytest.approx(float(g[k]), rel=1e-12), (g["bloque"], k)
        assert int(a["cuartos"]) == int(g["cuartos"])


def test_riesgo_por_cuarto_sin_porcentaje_si_la_referencia_es_cero(lectura: Conn) -> None:
    serie = datos.serie_riesgo(lectura, REF, COMP, date(2025, 3, 1), date(2025, 3, 1))
    assert len(serie) == 96
    en_cero = serie[serie["cmg_referencia"] == 0]
    assert len(en_cero) == 16 and en_cero["riesgo_pct"].isna().all()
    otro = serie[serie["cmg_referencia"] != 0].iloc[0]
    esperado = (otro["cmg_comparada"] - otro["cmg_referencia"]) / otro["cmg_referencia"]
    assert float(otro["riesgo_pct"]) == pytest.approx(float(esperado))
    assert (serie["riesgo_usd_mwh"] == serie["cmg_comparada"] - serie["cmg_referencia"]).all()


def test_resumen_de_riesgo_trae_el_rango_completo_por_bloque(lectura: Conn) -> None:
    df = datos.resumen_riesgo(lectura, REF, COMP, date(2025, 3, 25), date(2025, 4, 2))
    assert list(df["periodo"].unique()) == ["2025-03", "2025-04", datos.RANGO]
    assert list(df[df["periodo"] == datos.RANGO]["bloque"]) == ["A", "B", "C"]


def test_dias_preliminares(lectura: Conn) -> None:
    assert datos.dias_preliminares(lectura, *MARZO) == []
    dias = datos.dias_preliminares(lectura, date(2025, 3, 30), date(2025, 4, 3))
    assert dias == [date(2025, 4, d) for d in (1, 2, 3)]


def test_revisiones_vacias_sin_cambios(lectura: Conn) -> None:
    assert datos.revisiones(lectura, *MARZO).empty


def test_la_version_de_cada_dia(lectura: Conn) -> None:
    """El dia con def v1 y v2 dice "Definitivo v2"; sin documento, el origen."""
    df = datos.versiones_por_dia(lectura, date(2025, 3, 1), date(2025, 4, 2))
    version = dict(zip(df["fecha"], df["version"], strict=True))
    documento = dict(zip(df["fecha"], df["documento"], strict=True))
    assert version[date(2025, 3, 2)] == "Definitivo v2"
    assert documento[date(2025, 3, 2)] == "Antecedentes_CMG_Real_def_v2_250302.zip"
    assert version[date(2025, 3, 1)] == "Definitivo"
    assert version[date(2025, 4, 1)] == "Preliminar v1"
    assert version[date(2025, 4, 2)] == "Preliminar"


def test_la_serie_trae_la_version_en_cada_cuarto(lectura: Conn) -> None:
    serie = datos.serie_cmg(lectura, REF, date(2025, 3, 1), date(2025, 3, 2))
    por_dia = serie.groupby("fecha")["version"].unique()
    assert list(por_dia[date(2025, 3, 2)]) == ["Definitivo v2"]
    riesgo = datos.serie_riesgo(lectura, REF, COMP, date(2025, 3, 2), date(2025, 3, 2))
    assert set(riesgo["version"]) == {"Definitivo v2"}
