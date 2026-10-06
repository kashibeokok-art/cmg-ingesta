"""Tests de las validaciones.

La estrategia: construir datos sintÃ©ticos con un defecto CONOCIDO y verificar que
la validaciÃ³n lo encuentra. Son los defectos reales que el estudio E0 midiÃ³, en
miniatura.
"""

from datetime import date

import duckdb
import pytest

from cmg_ingesta.quality import checks

BARRA = "BARRA_X"

#: (fecha, hora, minuto, cmg). Alias para no repetir la firma larga.
Filas = list[tuple[date, int, int, float]]


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def tabla(
    con: duckdb.DuckDBPyConnection,
    filas: Filas,
    nombre: str = "datos",
) -> str:
    """Crea una tabla con (fecha, hora, minuto, cmg) y devuelve su nombre."""
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE {nombre} (
            barra VARCHAR, fecha DATE, hora UTINYINT,
            minuto UTINYINT, cmg_usd_mwh DOUBLE
        )
    """)
    con.executemany(
        f"INSERT INTO {nombre} VALUES (?, ?, ?, ?, ?)",
        [(BARRA, f, h, m, v) for f, h, m, v in filas],
    )
    return nombre


def dia_completo(dia: date, horas: list[int], valor: float = 50.0) -> Filas:
    """Un dia con 4 cuartos en cada hora indicada."""
    return [(dia, h, m, valor) for h in horas for m in (0, 15, 30, 45)]


# ---------------------------------------------------------------- largo del dia


def test_dia_normal_completo_no_tiene_hallazgos(con: duckdb.DuckDBPyConnection) -> None:
    dia = date(2025, 6, 15)
    checks.crear_tabla_calendario(con, dia, dia)
    t = tabla(con, dia_completo(dia, list(range(24))))
    assert len(checks.dias_con_largo_incorrecto(con, t)) == 0


def test_detecta_dia_largo_al_que_le_falta_la_hora_extra(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """El bug de la API: 96 cuartos donde deberia haber 100."""
    dia = date(2025, 4, 5)
    checks.crear_tabla_calendario(con, dia, dia)
    t = tabla(con, dia_completo(dia, list(range(24))))  # sin la hora 24

    hallazgos = checks.dias_con_largo_incorrecto(con, t)
    assert len(hallazgos) == 1
    assert hallazgos.iloc[0]["cuartos_por_barra"] == 96
    assert hallazgos.iloc[0]["cuartos_esperados"] == 100
    assert hallazgos.iloc[0]["diferencia"] == -4
    assert bool(hallazgos.iloc[0]["es_dia_largo"])


def test_dia_largo_completo_pasa(con: duckdb.DuckDBPyConnection) -> None:
    dia = date(2025, 4, 5)
    checks.crear_tabla_calendario(con, dia, dia)
    t = tabla(con, dia_completo(dia, [*range(24), 24]))  # con la hora 24
    assert len(checks.dias_con_largo_incorrecto(con, t)) == 0


def test_detecta_el_corrimiento_de_fecha_como_un_par(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """El defecto de 2021/2022/2024: la hora extra en el dia anterior.

    Aparece como DOS hallazgos: el viernes con +4 y el sabado con -4.
    """
    viernes, sabado = date(2024, 4, 5), date(2024, 4, 6)
    checks.crear_tabla_calendario(con, viernes, sabado)
    filas = dia_completo(viernes, [*range(24), 24]) + dia_completo(sabado, list(range(24)))
    t = tabla(con, filas)

    hallazgos = checks.dias_con_largo_incorrecto(con, t)
    assert len(hallazgos) == 2
    assert list(hallazgos["diferencia"]) == [4, -4]


def test_detecta_dia_corto_con_la_hora_fantasma(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Septiembre: 96 cuartos donde deberia haber 92."""
    dia = date(2025, 9, 7)
    checks.crear_tabla_calendario(con, dia, dia)
    t = tabla(con, dia_completo(dia, list(range(24))))  # incluye la hora 0, que no existio

    hallazgos = checks.dias_con_largo_incorrecto(con, t)
    assert len(hallazgos) == 1
    assert hallazgos.iloc[0]["diferencia"] == 4
    assert bool(hallazgos.iloc[0]["es_dia_corto"])


def test_dia_corto_sin_la_hora_cero_pasa(con: duckdb.DuckDBPyConnection) -> None:
    dia = date(2025, 9, 7)
    checks.crear_tabla_calendario(con, dia, dia)
    t = tabla(con, dia_completo(dia, list(range(1, 24))))
    assert len(checks.dias_con_largo_incorrecto(con, t)) == 0


# ------------------------------------------------------------ hora fantasma


def test_hora_fantasma_reporta_las_filas_y_los_ceros(
    con: duckdb.DuckDBPyConnection,
) -> None:
    dia = date(2025, 9, 7)
    checks.crear_tabla_calendario(con, dia, dia)
    filas = dia_completo(dia, list(range(1, 24)))
    filas += [(dia, 0, m, 0.0) for m in (0, 15, 30, 45)]  # la fantasma, en cero
    t = tabla(con, filas)

    hallazgos = checks.hora_fantasma_presente(con, t)
    assert len(hallazgos) == 1
    assert hallazgos.iloc[0]["filas"] == 4
    assert hallazgos.iloc[0]["en_cero"] == 4


def test_no_hay_hora_fantasma_en_un_dia_normal(
    con: duckdb.DuckDBPyConnection,
) -> None:
    dia = date(2025, 6, 15)
    checks.crear_tabla_calendario(con, dia, dia)
    t = tabla(con, dia_completo(dia, list(range(24))))
    assert len(checks.hora_fantasma_presente(con, t)) == 0


# ------------------------------------------------------- horas incompletas


def test_detecta_una_hora_con_tres_cuartos(con: duckdb.DuckDBPyConnection) -> None:
    """El caso que el total del dia NO atrapa."""
    dia = date(2025, 6, 15)
    checks.crear_tabla_calendario(con, dia, dia)
    filas = dia_completo(dia, list(range(24)))
    filas.remove((dia, 5, 30, 50.0))  # a la hora 5 le quedan 3
    filas.append((dia, 6, 7, 50.0))  # y la 6 queda con 5 y un minuto invalido
    t = tabla(con, filas)

    # el total del dia sigue siendo 96: esta validacion no ve nada
    assert len(checks.dias_con_largo_incorrecto(con, t)) == 0
    # pero la de horas si
    hallazgos = checks.horas_incompletas(con, t)
    assert set(hallazgos["hora"]) == {5, 6}


def test_minutos_invalidos(con: duckdb.DuckDBPyConnection) -> None:
    dia = date(2025, 6, 15)
    t = tabla(con, [(dia, 0, 0, 50.0), (dia, 0, 7, 50.0), (dia, 0, 13, 50.0)])
    assert checks.minutos_invalidos(con, t) == 2


def test_horas_invalidas(con: duckdb.DuckDBPyConnection) -> None:
    dia = date(2025, 6, 15)
    t = tabla(con, [(dia, 0, 0, 50.0), (dia, 25, 0, 50.0)])
    assert checks.horas_invalidas(con, t) == 1


# ------------------------------------------------------------- duplicados


def test_detecta_clave_duplicada(con: duckdb.DuckDBPyConnection) -> None:
    dia = date(2025, 6, 15)
    t = tabla(con, [(dia, 0, 0, 50.0), (dia, 0, 0, 99.0)])
    hallazgos = checks.claves_duplicadas(con, t)
    assert len(hallazgos) == 1
    assert hallazgos.iloc[0]["veces"] == 2


def test_la_hora_24_no_es_duplicado_de_la_23(con: duckdb.DuckDBPyConnection) -> None:
    """La clave natural incluye `hora`, asi que las dos 23:00 NO colisionan."""
    dia = date(2025, 4, 5)
    t = tabla(con, [(dia, 23, 0, 204.6), (dia, 24, 0, 206.2)])
    assert len(checks.claves_duplicadas(con, t)) == 0


# ------------------------------------------------------------------ resumen


def test_resumen_de_datos_limpios(con: duckdb.DuckDBPyConnection) -> None:
    dia = date(2025, 6, 15)
    checks.crear_tabla_calendario(con, dia, dia)
    t = tabla(con, dia_completo(dia, list(range(24))))

    res = checks.resumen(con, t)
    assert res["filas"] == 96
    assert not checks.hay_problemas(res)


def test_resumen_detecta_problemas(con: duckdb.DuckDBPyConnection) -> None:
    dia = date(2025, 9, 7)
    checks.crear_tabla_calendario(con, dia, dia)
    t = tabla(con, dia_completo(dia, list(range(24))))

    res = checks.resumen(con, t)
    assert checks.hay_problemas(res)
    assert res["filas_hora_fantasma"] == 4


def test_valores_nulos(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE TEMP TABLE conulos AS
        SELECT 'B' AS barra, DATE '2025-06-15' AS fecha,
               CAST(0 AS UTINYINT) AS hora, CAST(0 AS UTINYINT) AS minuto,
               CAST(NULL AS DOUBLE) AS cmg_usd_mwh
    """)
    assert checks.valores_nulos(con, "conulos") == 1


def test_crear_tabla_calendario_cuenta_los_dias(
    con: duckdb.DuckDBPyConnection,
) -> None:
    n = checks.crear_tabla_calendario(con, date(2025, 1, 1), date(2025, 1, 31))
    assert n == 31
