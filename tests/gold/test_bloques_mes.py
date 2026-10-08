"""Tests de la capa Gold, con una base Silver sintetica.

Lo que mas importa verificar: que los promedios salgan de los valores
QUINCEMINUTALES y no de promediar promedios.
"""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from cmg_ingesta.gold import bloques_mes, riesgo_nodal
from cmg_ingesta.silver import escribir, leer

# `con`, `sembrar` y `valores` vienen de tests/conftest.py: pytest los inyecta
# por nombre, sin imports ni tocar sys.path.

Sembrar = Callable[..., None]
VALORES = {"A": 100.0, "B": 10.0, "C": 200.0}


# ------------------------------------------------------------------- lectura


def test_resumen_base(con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar) -> None:
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", VALORES)
    sembrar(con, tmp_path, 2024, 7, "BARRA_1", VALORES)
    res = leer.resumen_base(con, tmp_path)
    assert res["desde"] == "2024-06"
    assert res["hasta"] == "2024-07"
    assert res["filas"] == 2 * 96
    assert res["barras"] == 1
    assert res["meses"] == res["meses_con_datos"] == 2


def test_resumen_base_distingue_los_meses_vacios(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    """REGRESION: con un hueco en el medio decia '70 meses' teniendo 49."""
    sembrar(con, tmp_path, 2024, 1, "BARRA_1", VALORES)
    sembrar(con, tmp_path, 2024, 12, "BARRA_1", VALORES)
    res = leer.resumen_base(con, tmp_path)
    assert res["meses"] == 12
    assert res["meses_con_datos"] == 2


def test_base_vacia_falla(con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar) -> None:
    with pytest.raises(ValueError, match="vacia"):
        leer.rango_disponible(con, tmp_path)


def test_barras_ordenadas(con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar) -> None:
    sembrar(con, tmp_path, 2024, 6, "ZETA", VALORES)
    base2 = tmp_path  # misma base, otra barra en otro mes
    sembrar(con, base2, 2024, 7, "ALFA", VALORES)
    assert leer.barras(con, tmp_path) == ["ALFA", "ZETA"]


def test_existe_barra(con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar) -> None:
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", VALORES)
    assert leer.existe_barra(con, tmp_path, "BARRA_1")
    assert not leer.existe_barra(con, tmp_path, "NO_EXISTE")


# ------------------------------------------------------------ resumen mensual


def test_solar_es_exactamente_el_bloque_b(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", VALORES)
    df = bloques_mes.resumen_mensual(con, tmp_path, "BARRA_1", (2024, 6), (2024, 6))
    fila = df.iloc[0]
    assert fila["B"] == pytest.approx(10.0)
    assert fila["Solar"] == pytest.approx(fila["B"])


def test_no_solar_sale_de_los_cuartos_no_de_promediar_promedios(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    """Con A=100 y C=200, el promedio simple seria 150. Lo correcto es 135,71."""
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", VALORES)
    df = bloques_mes.resumen_mensual(con, tmp_path, "BARRA_1", (2024, 6), (2024, 6))
    no_solar = df.iloc[0]["NoSolar"]

    esperado = (100.0 * 9 + 200.0 * 5) / 14
    assert no_solar == pytest.approx(esperado)
    assert no_solar != pytest.approx(150.0)


def test_total_es_el_promedio_de_todos_los_cuartos(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", VALORES)
    df = bloques_mes.resumen_mensual(con, tmp_path, "BARRA_1", (2024, 6), (2024, 6))
    esperado = (100.0 * 9 + 10.0 * 10 + 200.0 * 5) / 24
    assert df.iloc[0]["Total"] == pytest.approx(esperado)


def test_la_hora_extra_de_abril_entra_al_bloque_a(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    """El dia largo suma 4 cuartos a A, asi que A pesa 10 horas y no 9.

    Por eso NoSolar calculado desde los cuartos NO coincide con la formula
    (A x 9 + C x 5) / 14: el valor de los cuartos es el correcto.
    """
    sembrar(con, tmp_path, 2024, 4, "BARRA_1", VALORES, horas=[*range(24), 24])
    df = bloques_mes.resumen_mensual(con, tmp_path, "BARRA_1", (2024, 4), (2024, 4))
    fila = df.iloc[0]

    assert fila["cuartos"] == 100
    # A tiene 10 horas ese dia: 40 cuartos
    real = (100.0 * 10 + 200.0 * 5) / 15
    assert fila["NoSolar"] == pytest.approx(real)
    assert fila["NoSolar"] != pytest.approx((100.0 * 9 + 200.0 * 5) / 14)


def test_fecha_es_una_fecha_para_que_excel_la_entienda(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", VALORES)
    df = bloques_mes.resumen_mensual(con, tmp_path, "BARRA_1", (2024, 6), (2024, 6))
    assert str(df["fecha"].dtype).startswith("datetime")


def test_detalle_por_bloque_cuenta_los_ceros(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", {"A": 100.0, "B": 0.0, "C": 200.0})
    df = bloques_mes.resumen_por_bloque_detallado(con, tmp_path, "BARRA_1", (2024, 6), (2024, 6))
    fila_b = df[df["bloque"] == "B"].iloc[0]
    assert fila_b["en_cero"] == 40  # 10 horas x 4 cuartos
    assert fila_b["promedio"] == pytest.approx(0.0)


# ------------------------------------------------------------ comparar barras


def test_comparar_barras(con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar) -> None:
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", VALORES)
    # segunda barra en el mismo mes: hay que reescribir el mes con las dos
    sembrar(con, tmp_path, 2024, 7, "BARRA_2", {"A": 1.0, "B": 2.0, "C": 3.0})

    df = bloques_mes.comparar_barras(con, tmp_path, ["BARRA_1", "BARRA_2"], (2024, 6), (2024, 7))
    assert list(df.columns) == ["anio", "mes", "fecha", "BARRA_1", "BARRA_2"]
    assert len(df) == 2


def test_comparar_barras_sin_barras_falla(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", VALORES)
    with pytest.raises(ValueError, match="al menos una barra"):
        bloques_mes.comparar_barras(con, tmp_path, [], (2024, 6), (2024, 6))


def test_comparar_barras_con_comilla_en_el_nombre(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    """Un nombre con apostrofo no debe romper el SQL generado."""
    sembrar(con, tmp_path, 2024, 6, "O'HIGGINS_013", VALORES)
    df = bloques_mes.comparar_barras(con, tmp_path, ["O'HIGGINS_013"], (2024, 6), (2024, 6))
    assert "O'HIGGINS_013" in df.columns


# ------------------------------------------------------------- riesgo nodal


def sembrar_dos_barras(con: duckdb.DuckDBPyConnection, base: Path, ref: float, comp: float) -> None:
    """Un mes con dos barras de valor constante, para un riesgo conocido."""
    consulta = f"""
        SELECT b.barra, make_date(2024, 6, 1) AS fecha,
               CAST(h AS UTINYINT) AS hora, CAST(m AS UTINYINT) AS minuto,
               'A' AS bloque, CAST(b.valor AS DOUBLE) AS cmg_usd_mwh,
               false AS es_hora_extra, CAST(NULL AS TIMESTAMP) AS fecha_hora,
               'maestro_cmg_db' AS origen, CAST('2026-10-06' AS TIMESTAMP) AS ingerido_en
        FROM (SELECT unnest(range(24)) AS h) a,
             (SELECT unnest([0, 15, 30, 45]) AS m) c,
             (SELECT * FROM (VALUES ('REF', {ref}), ('COMP', {comp})) t(barra, valor)) b
    """
    escribir.escribir_particion(con, consulta, base, 2024, 6)


def test_riesgo_es_comparada_menos_referencia(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar_dos_barras(con, tmp_path, ref=50.0, comp=60.0)
    df = riesgo_nodal.serie_riesgo(con, tmp_path, "REF", "COMP", (2024, 6), (2024, 6))
    assert len(df) == 96
    assert df["riesgo_usd_mwh"].iloc[0] == pytest.approx(10.0)
    assert df["riesgo_pct"].iloc[0] == pytest.approx(0.2)


def test_riesgo_negativo_cuando_la_comparada_es_mas_barata(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar_dos_barras(con, tmp_path, ref=60.0, comp=50.0)
    df = riesgo_nodal.serie_riesgo(con, tmp_path, "REF", "COMP", (2024, 6), (2024, 6))
    assert df["riesgo_usd_mwh"].iloc[0] == pytest.approx(-10.0)


def test_porcentaje_es_nulo_cuando_la_referencia_es_cero(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    """El 24,7% de los CMg son 0: el porcentaje no se puede calcular ahi."""
    sembrar_dos_barras(con, tmp_path, ref=0.0, comp=60.0)
    df = riesgo_nodal.serie_riesgo(con, tmp_path, "REF", "COMP", (2024, 6), (2024, 6))
    assert df["riesgo_usd_mwh"].iloc[0] == pytest.approx(60.0)
    assert df["riesgo_pct"].isna().all()


def test_intervalos_sin_porcentaje_se_informan(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar_dos_barras(con, tmp_path, ref=0.0, comp=60.0)
    ceros, total = riesgo_nodal.intervalos_sin_porcentaje(
        con, tmp_path, "REF", (2024, 6), (2024, 6)
    )
    assert (ceros, total) == (96, 96)


def test_resumen_riesgo_calcula_el_pct_sobre_promedios(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    sembrar_dos_barras(con, tmp_path, ref=50.0, comp=60.0)
    df = riesgo_nodal.resumen_riesgo(con, tmp_path, "REF", "COMP", (2024, 6), (2024, 6))
    fila = df.iloc[0]
    assert fila["cuartos"] == 96
    assert fila["riesgo_usd_mwh"] == pytest.approx(10.0)
    assert fila["riesgo_pct"] == pytest.approx(0.2)
    assert fila["ref_en_cero"] == 0


def test_riesgo_solo_usa_intervalos_con_dato_en_ambas(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar
) -> None:
    """INNER JOIN: si una barra le falta un intervalo, ese par no se compara."""
    consulta = """
        SELECT 'REF' AS barra, DATE '2024-06-01' AS fecha,
               CAST(h AS UTINYINT) AS hora, CAST(0 AS UTINYINT) AS minuto,
               'A' AS bloque, 50.0 AS cmg_usd_mwh, false AS es_hora_extra,
               CAST(NULL AS TIMESTAMP) AS fecha_hora, 'maestro_cmg_db' AS origen,
               CAST('2026-10-06' AS TIMESTAMP) AS ingerido_en
        FROM (SELECT unnest([0, 1, 2]) AS h)
        UNION ALL
        SELECT 'COMP', DATE '2024-06-01',
               CAST(h AS UTINYINT), CAST(0 AS UTINYINT),
               'A', 60.0, false, CAST(NULL AS TIMESTAMP), 'maestro_cmg_db',
               CAST('2026-10-06' AS TIMESTAMP)
        FROM (SELECT unnest([0, 1]) AS h)
    """
    escribir.escribir_particion(con, consulta, tmp_path, 2024, 6)
    df = riesgo_nodal.serie_riesgo(con, tmp_path, "REF", "COMP", (2024, 6), (2024, 6))
    assert len(df) == 2  # la hora 2 no tiene par
