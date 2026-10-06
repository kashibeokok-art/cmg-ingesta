"""Capa Gold: promedios mensuales por bloque, listos para el negocio.

**Todo se calcula desde los valores quinceminutales**, nunca promediando
promedios. Esa fue una correccion explicita del usuario en el programa anterior,
y la razon es el sesgo de agregacion: promediar los promedios de A y C da un
numero distinto del promedio real de sus intervalos, porque los bloques tienen
distinta cantidad de horas.

Como todos los intervalos duran 15 minutos, un `AVG` sobre los cuartos de un
bloque YA es el promedio ponderado por tiempo. No hace falta ponderar a mano.

Eso tiene una consecuencia fina en abril: el dia de 25 horas agrega 4 cuartos al
bloque A, asi que ese mes A pesa 10 horas y no 9, y `NoSolar` queda levemente
distinto de la formula (A x 9 + C x 5) / 14. El valor calculado desde los cuartos
es el correcto: la formula es un atajo que supone un dia normal.
"""

from pathlib import Path

import duckdb
import pandas as pd

from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.silver import leer


def resumen_mensual(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    barra: str,
    desde: Mes,
    hasta: Mes,
) -> pd.DataFrame:
    """Promedio de CMg por bloque, por mes, para una barra.

    Columnas: anio, mes, fecha (primer dia, para que Excel la reconozca),
    cuartos, A, B, C, Solar, NoSolar, Total.

    `fecha` existe para que en Excel funcionen `=MES()` y `=AÑO()`: una cadena
    "2025-03" no sirve para eso. Fue un pedido del usuario en el programa anterior.
    """
    return con.execute(
        f"""
        WITH datos AS (
            SELECT anio, mes, bloque, cmg_usd_mwh
            FROM {leer.sql_dataset(base)}
            WHERE barra = ? AND {leer.sql_filtro_periodo(desde, hasta)}
        )
        SELECT
            anio,
            mes,
            make_date(anio, mes, 1) AS fecha,
            count(*) AS cuartos,
            avg(CASE WHEN bloque = 'A' THEN cmg_usd_mwh END) AS "A",
            avg(CASE WHEN bloque = 'B' THEN cmg_usd_mwh END) AS "B",
            avg(CASE WHEN bloque = 'C' THEN cmg_usd_mwh END) AS "C",
            -- Solar es el bloque B
            avg(CASE WHEN bloque = 'B' THEN cmg_usd_mwh END) AS "Solar",
            -- NoSolar: promedio de los cuartos de A y C juntos, no de sus promedios
            avg(CASE WHEN bloque IN ('A', 'C') THEN cmg_usd_mwh END) AS "NoSolar",
            avg(cmg_usd_mwh) AS "Total"
        FROM datos
        GROUP BY anio, mes
        ORDER BY anio, mes
        """,
        [barra],
    ).df()


def resumen_por_bloque_detallado(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    barra: str,
    desde: Mes,
    hasta: Mes,
) -> pd.DataFrame:
    """Una fila por (mes, bloque) con promedio, minimo, maximo y desvio estandar.

    Las cuatro estadisticas son las que pide RN-18 del proyecto original.
    """
    return con.execute(
        f"""
        SELECT anio, mes, make_date(anio, mes, 1) AS fecha, bloque,
               count(*) AS cuartos,
               avg(cmg_usd_mwh) AS promedio,
               stddev_samp(cmg_usd_mwh) AS desv_estandar,
               min(cmg_usd_mwh) AS minimo,
               max(cmg_usd_mwh) AS maximo,
               count(*) FILTER (WHERE cmg_usd_mwh = 0) AS en_cero
        FROM {leer.sql_dataset(base)}
        WHERE barra = ? AND {leer.sql_filtro_periodo(desde, hasta)}
        GROUP BY anio, mes, bloque
        ORDER BY anio, mes, bloque
        """,
        [barra],
    ).df()


def _lit(texto: str) -> str:
    """Un literal de cadena SQL, con las comillas simples escapadas."""
    return "'" + texto.replace("'", "''") + "'"


def _ident(texto: str) -> str:
    """Un identificador SQL entre comillas dobles, escapadas."""
    return '"' + texto.replace('"', '""') + '"'


def comparar_barras(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    barras: list[str],
    desde: Mes,
    hasta: Mes,
) -> pd.DataFrame:
    """Promedio mensual de varias barras, una columna por barra.

    Sirve para ver de un vistazo cual barra es mas cara.

    Se arma con agregacion condicional y no con `PIVOT`, porque `PIVOT` de DuckDB
    no admite parametros cuando extrae los valores de los datos. Al generar el SQL
    hay que escapar a mano, asi que se usan `_lit` y `_ident`.
    """
    if not barras:
        raise ValueError("hay que indicar al menos una barra")

    columnas = ",\n               ".join(
        f"avg(CASE WHEN barra = {_lit(b)} THEN cmg_usd_mwh END) AS {_ident(b)}" for b in barras
    )
    lista = ", ".join(_lit(b) for b in barras)
    return con.execute(
        f"""
        SELECT anio, mes, make_date(anio, mes, 1) AS fecha,
               {columnas}
        FROM {leer.sql_dataset(base)}
        WHERE barra IN ({lista}) AND {leer.sql_filtro_periodo(desde, hasta)}
        GROUP BY anio, mes
        ORDER BY anio, mes
        """
    ).df()
