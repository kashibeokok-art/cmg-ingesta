"""Riesgo nodal: la diferencia de CMg entre dos barras.

    riesgo = CMg(comparada) - CMg(referencia)

Positivo significa que la barra comparada es MAS cara que la referencia.

El porcentaje tiene un problema real: **el 24,7% de los CMg de la base son
exactamente 0** (horas de sol). Cuando la referencia vale 0, el porcentaje no
existe (division por cero) y queda NULL en el detalle. Por eso el porcentaje
confiable se calcula sobre los PROMEDIOS del mes, donde el denominador
practicamente nunca es cero.

Ojo tambien con los intervalos donde la referencia es muy cercana a cero: ahi el
porcentaje se dispara y no es representativo. Es un limite del indicador, no un
bug.
"""

from pathlib import Path

import duckdb
import pandas as pd

from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.silver import leer


def serie_riesgo(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    referencia: str,
    comparada: str,
    desde: Mes,
    hasta: Mes,
) -> pd.DataFrame:
    """La serie de 15 minutos del riesgo entre dos barras.

    El cruce es por la clave natural sin la barra: (fecha, hora, minuto). Se usa
    `INNER JOIN` a proposito: solo se comparan los intervalos donde AMBAS barras
    tienen dato. Un `LEFT JOIN` daria filas con una mitad nula que se verian como
    riesgo cero.
    """
    dataset = leer.sql_dataset(base)
    filtro = leer.sql_filtro_periodo(desde, hasta)
    return con.execute(
        f"""
        WITH ref AS (
            SELECT fecha, hora, minuto, bloque, es_hora_extra, fecha_hora,
                   cmg_usd_mwh AS cmg_referencia
            FROM {dataset} WHERE barra = ? AND {filtro}
        ),
        comp AS (
            SELECT fecha, hora, minuto, cmg_usd_mwh AS cmg_comparada
            FROM {dataset} WHERE barra = ? AND {filtro}
        )
        SELECT
            r.fecha, r.hora, r.minuto, r.bloque, r.es_hora_extra, r.fecha_hora,
            r.cmg_referencia,
            c.cmg_comparada,
            c.cmg_comparada - r.cmg_referencia AS riesgo_usd_mwh,
            CASE WHEN r.cmg_referencia <> 0
                 THEN (c.cmg_comparada - r.cmg_referencia) / r.cmg_referencia
                 ELSE NULL END AS riesgo_pct
        FROM ref r
        JOIN comp c USING (fecha, hora, minuto)
        ORDER BY r.fecha, r.hora, r.minuto
        """,
        [referencia, comparada],
    ).df()


def resumen_riesgo(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    referencia: str,
    comparada: str,
    desde: Mes,
    hasta: Mes,
) -> pd.DataFrame:
    """Riesgo mensual por bloque, con el porcentaje calculado sobre los promedios.

    Es la tabla que sirve para analisis: el `riesgo_pct` de aqui es robusto, a
    diferencia del porcentaje intervalo a intervalo.
    """
    dataset = leer.sql_dataset(base)
    filtro = leer.sql_filtro_periodo(desde, hasta)
    return con.execute(
        f"""
        WITH par AS (
            SELECT r.anio, r.mes, r.bloque,
                   r.cmg_usd_mwh AS ref, c.cmg_usd_mwh AS comp
            FROM (SELECT * FROM {dataset} WHERE barra = ? AND {filtro}) r
            JOIN (SELECT * FROM {dataset} WHERE barra = ? AND {filtro}) c
              USING (fecha, hora, minuto)
        )
        SELECT anio, mes, make_date(anio, mes, 1) AS fecha, bloque,
               count(*) AS cuartos,
               avg(ref) AS cmg_referencia,
               avg(comp) AS cmg_comparada,
               avg(comp) - avg(ref) AS riesgo_usd_mwh,
               CASE WHEN avg(ref) <> 0
                    THEN (avg(comp) - avg(ref)) / avg(ref)
                    ELSE NULL END AS riesgo_pct,
               min(comp - ref) AS riesgo_minimo,
               max(comp - ref) AS riesgo_maximo,
               count(*) FILTER (WHERE ref = 0) AS ref_en_cero
        FROM par
        GROUP BY anio, mes, bloque
        ORDER BY anio, mes, bloque
        """,
        [referencia, comparada],
    ).df()


def intervalos_sin_porcentaje(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    referencia: str,
    desde: Mes,
    hasta: Mes,
) -> tuple[int, int]:
    """(intervalos con referencia en cero, intervalos totales).

    Se informa siempre: quien lea el reporte tiene que saber sobre cuantas filas
    el porcentaje no existe.
    """
    fila = con.execute(
        f"""
        SELECT count(*) FILTER (WHERE cmg_usd_mwh = 0), count(*)
        FROM {leer.sql_dataset(base)}
        WHERE barra = ? AND {leer.sql_filtro_periodo(desde, hasta)}
        """,
        [referencia],
    ).fetchone()
    return (int(fila[0]), int(fila[1])) if fila else (0, 0)
