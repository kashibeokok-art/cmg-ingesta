"""Validaciones de calidad de datos (data quality) contra el calendario real.

La idea central: **el largo esperado de un dia no se asume, se calcula**. Las
funciones de `domain/calendario.py` dicen cuantos cuartos debe tener cada fecha, y
aqui se compara con lo que realmente hay.

Esto es lo que ninguna version anterior tenia, y por eso el defecto de fechas de
2021, 2022 y 2024 (CLAUDE.md 4.5.1) llevaba años sin detectarse.

Ninguna funcion corrige nada: todas REPORTAN. Corregir en silencio es como no
tener la validacion.
"""

from datetime import date, timedelta
from typing import TypedDict

import duckdb
import pandas as pd

from cmg_ingesta.domain import calendario, esquema

TABLA_CALENDARIO = "calendario_esperado"


class Resumen(TypedDict):
    """El resultado de las validaciones, con sus claves y tipos declarados.

    Es un `dict` normal en tiempo de ejecucion: `TypedDict` solo le dice a mypy
    que claves tiene y de que tipo es cada una. No es una clase — no se instancia,
    no tiene metodos, no hay herencia. Se escribe `{"filas": 10, ...}` igual.

    Sin esto, el tipo seria `dict[str, object]` y cada lectura necesitaria un
    `int(...)` que mypy rechaza, o un `# type: ignore`. Un `ignore` casi siempre
    es la señal de que el tipo esta mal elegido, no de que mypy se equivoque.
    """

    filas: int
    dias_con_largo_incorrecto: int
    detalle_largo: pd.DataFrame
    horas_incompletas: int
    detalle_horas: pd.DataFrame
    claves_duplicadas: int
    detalle_duplicados: pd.DataFrame
    filas_hora_fantasma: int
    detalle_fantasma: pd.DataFrame
    minutos_invalidos: int
    horas_invalidas: int
    valores_nulos: int


def crear_tabla_calendario(con: duckdb.DuckDBPyConnection, desde: date, hasta: date) -> int:
    """Materializa el calendario esperado como tabla temporal, para poder hacer JOIN.

    El calendario vive en Python (`domain/calendario.py`), pero las validaciones
    son consultas SQL sobre millones de filas. En vez de duplicar la regla en SQL
    — que seria una segunda fuente de verdad —, se genera la tabla una vez y se
    cruza contra ella.
    """
    filas = []
    dia = desde
    while dia <= hasta:
        filas.append(
            {
                "fecha": dia,
                "cuartos_esperados": calendario.cuartos_esperados(dia),
                "horas_esperadas": calendario.horas_del_dia(dia),
                "es_dia_largo": calendario.es_dia_largo(dia),
                "es_dia_corto": calendario.es_dia_corto(dia),
            }
        )
        dia += timedelta(days=1)

    df = pd.DataFrame(filas)
    con.register("_cal_df", df)
    con.execute(f"CREATE OR REPLACE TEMP TABLE {TABLA_CALENDARIO} AS SELECT * FROM _cal_df")
    con.unregister("_cal_df")
    return len(filas)


def dias_con_largo_incorrecto(con: duckdb.DuckDBPyConnection, fuente: str) -> pd.DataFrame:
    """Dias cuyo numero de cuartos no coincide con el calendario.

    `fuente` es una expresion SQL: un nombre de tabla o un `read_parquet(...)`.

    Devuelve fecha, cuantos cuartos tiene, cuantos deberia, y la diferencia.
    Una diferencia de -4 en abril significa que falta la hora extra; de +4 en
    septiembre, que esta la hora fantasma.
    """
    return con.execute(f"""
        WITH real AS (
            SELECT fecha, count(*) / count(DISTINCT barra) AS cuartos_por_barra,
                   count(DISTINCT barra) AS barras
            FROM {fuente} GROUP BY fecha
        )
        SELECT c.fecha, r.cuartos_por_barra, c.cuartos_esperados,
               r.cuartos_por_barra - c.cuartos_esperados AS diferencia,
               r.barras, c.es_dia_largo, c.es_dia_corto
        FROM real r JOIN {TABLA_CALENDARIO} c USING (fecha)
        WHERE r.cuartos_por_barra <> c.cuartos_esperados
        ORDER BY c.fecha
    """).df()


def horas_incompletas(con: duckdb.DuckDBPyConnection, fuente: str) -> pd.DataFrame:
    """Horas que no tienen exactamente 4 cuartos.

    El total del dia puede cuadrar y los datos estar mal repartidos: una hora con
    3 cuartos y otra con 5. Esta validacion es mas fuerte que contar el dia.
    """
    minutos = ", ".join(str(m) for m in calendario.MINUTOS_VALIDOS)
    return con.execute(f"""
        SELECT fecha, hora, barra, count(*) AS cuartos,
               list_sort(list(minuto)) AS minutos
        FROM {fuente}
        GROUP BY fecha, hora, barra
        HAVING count(*) <> {calendario.CUARTOS_POR_HORA}
            OR NOT list_has_all([{minutos}], list(minuto))
        ORDER BY fecha, hora, barra
        LIMIT 500
    """).df()


def claves_duplicadas(con: duckdb.DuckDBPyConnection, fuente: str) -> pd.DataFrame:
    """Filas que repiten la clave natural (barra, fecha, hora, minuto)."""
    clave = ", ".join(esquema.CLAVE_NATURAL)
    return con.execute(f"""
        SELECT {clave}, count(*) AS veces
        FROM {fuente} GROUP BY {clave}
        HAVING count(*) > 1
        ORDER BY veces DESC LIMIT 500
    """).df()


def minutos_invalidos(con: duckdb.DuckDBPyConnection, fuente: str) -> int:
    """Cuantas filas tienen un minuto que no es 0, 15, 30 ni 45."""
    minutos = ", ".join(str(m) for m in calendario.MINUTOS_VALIDOS)
    fila = con.execute(f"SELECT count(*) FROM {fuente} WHERE minuto NOT IN ({minutos})").fetchone()
    return int(fila[0]) if fila else 0


def horas_invalidas(con: duckdb.DuckDBPyConnection, fuente: str) -> int:
    """Cuantas filas tienen una hora fuera de 0..24."""
    fila = con.execute(f"SELECT count(*) FROM {fuente} WHERE hora NOT BETWEEN 0 AND 24").fetchone()
    return int(fila[0]) if fila else 0


def valores_nulos(con: duckdb.DuckDBPyConnection, fuente: str) -> int:
    """Filas sin valor de CMg. No se imputan: se reportan."""
    fila = con.execute(f"SELECT count(*) FROM {fuente} WHERE cmg_usd_mwh IS NULL").fetchone()
    return int(fila[0]) if fila else 0


def hora_fantasma_presente(con: duckdb.DuckDBPyConnection, fuente: str) -> pd.DataFrame:
    """Filas en una hora que el reloj NO tuvo (la hora 0 del dia corto).

    El CEN las publica con valor 0,00 (CLAUDE.md 4.5.4) y diluyen el promedio del
    bloque A exactamente un 0,370%. Hay que descartarlas.
    """
    return con.execute(f"""
        SELECT f.fecha, f.hora, count(*) AS filas,
               count(*) FILTER (WHERE f.cmg_usd_mwh = 0) AS en_cero
        FROM {fuente} f JOIN {TABLA_CALENDARIO} c ON f.fecha = c.fecha
        WHERE c.es_dia_corto AND f.hora = 0
        GROUP BY f.fecha, f.hora
        ORDER BY f.fecha
    """).df()


def resumen(con: duckdb.DuckDBPyConnection, fuente: str) -> Resumen:
    """Corre todas las validaciones y devuelve un resumen.

    Requiere que `crear_tabla_calendario` ya se haya llamado con un rango que
    cubra los datos.
    """
    largo = dias_con_largo_incorrecto(con, fuente)
    horas = horas_incompletas(con, fuente)
    dups = claves_duplicadas(con, fuente)
    fantasma = hora_fantasma_presente(con, fuente)
    fila = con.execute(f"SELECT count(*) FROM {fuente}").fetchone()
    return {
        "filas": int(fila[0]) if fila else 0,
        "dias_con_largo_incorrecto": len(largo),
        "detalle_largo": largo,
        "horas_incompletas": len(horas),
        "detalle_horas": horas,
        "claves_duplicadas": len(dups),
        "detalle_duplicados": dups,
        "filas_hora_fantasma": int(fantasma["filas"].sum()) if len(fantasma) else 0,
        "detalle_fantasma": fantasma,
        "minutos_invalidos": minutos_invalidos(con, fuente),
        "horas_invalidas": horas_invalidas(con, fuente),
        "valores_nulos": valores_nulos(con, fuente),
    }


def hay_problemas(res: Resumen) -> bool:
    """True si alguna validacion encontro algo."""
    return (
        res["dias_con_largo_incorrecto"] > 0
        or res["horas_incompletas"] > 0
        or res["claves_duplicadas"] > 0
        or res["filas_hora_fantasma"] > 0
        or res["minutos_invalidos"] > 0
        or res["horas_invalidas"] > 0
        or res["valores_nulos"] > 0
    )
