"""Lectura de la capa Silver: catalogo de barras, rango disponible y series.

Todas las consultas filtran por `anio`/`mes` cuando pueden, para que DuckDB use el
*partition pruning* y no abra los archivos de los meses que no se piden.
"""

from pathlib import Path

import duckdb
import pandas as pd

from cmg_ingesta.domain import periodo
from cmg_ingesta.domain.periodo import Mes


def sql_dataset(base: Path) -> str:
    """La expresion SQL que representa toda la base.

    `hive_partitioning=true` hace que `anio` y `mes` existan como columnas aunque
    vivan en el nombre de las carpetas.
    """
    patron = str(base / "**" / "*.parquet").replace("'", "''")
    return f"read_parquet('{patron}', hive_partitioning=true)"


def base_existe(base: Path) -> bool:
    return base.is_dir() and any(base.rglob("*.parquet"))


def sql_filtro_periodo(desde: Mes, hasta: Mes) -> str:
    """Filtro de meses que DuckDB puede usar para descartar archivos enteros.

    Se escribe como comparacion de (anio, mes) y no sobre `fecha`, porque solo el
    filtro sobre las columnas de PARTICION permite el pruning.
    """
    if desde == hasta:
        return f"(anio = {desde[0]} AND mes = {desde[1]})"
    return (
        f"((anio * 100 + mes) BETWEEN {desde[0] * 100 + desde[1]} AND {hasta[0] * 100 + hasta[1]})"
    )


def rango_disponible(con: duckdb.DuckDBPyConnection, base: Path) -> tuple[Mes, Mes]:
    """El primer y el ultimo mes que tiene la base.

    Lee solo los nombres de las carpetas, no los datos: es instantaneo.
    """
    from cmg_ingesta.silver import escribir

    meses = escribir.meses_escritos(base)
    if not meses:
        raise ValueError(f"la base esta vacia: {base}")
    return meses[0], meses[-1]


def barras(con: duckdb.DuckDBPyConnection, base: Path) -> list[str]:
    """Todas las barras de la base, ordenadas."""
    filas = con.execute(f"SELECT DISTINCT barra FROM {sql_dataset(base)} ORDER BY barra").fetchall()
    return [str(f[0]) for f in filas]


def resumen_base(con: duckdb.DuckDBPyConnection, base: Path) -> dict[str, object]:
    """Un resumen para mostrar al arrancar: periodo, barras y filas."""
    desde, hasta = rango_disponible(con, base)
    fila = con.execute(
        f"SELECT count(*), count(DISTINCT barra) FROM {sql_dataset(base)}"
    ).fetchone()
    return {
        "desde": periodo.formatear(desde),
        "hasta": periodo.formatear(hasta),
        "filas": int(fila[0]) if fila else 0,
        "barras": int(fila[1]) if fila else 0,
        "meses": len(periodo.meses_entre(desde, hasta)),
    }


def serie_quinceminutal(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    barra: str,
    desde: Mes,
    hasta: Mes,
) -> pd.DataFrame:
    """La serie de 15 minutos de una barra en un rango de meses."""
    return con.execute(
        f"""
        SELECT fecha, hora, minuto, bloque, cmg_usd_mwh, es_hora_extra,
               fecha_hora, anio, mes, origen
        FROM {sql_dataset(base)}
        WHERE barra = ? AND {sql_filtro_periodo(desde, hasta)}
        ORDER BY fecha, hora, minuto
        """,
        [barra],
    ).df()


def existe_barra(con: duckdb.DuckDBPyConnection, base: Path, barra: str) -> bool:
    fila = con.execute(
        f"SELECT 1 FROM {sql_dataset(base)} WHERE barra = ? LIMIT 1", [barra]
    ).fetchone()
    return fila is not None


def resumen_barras(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    barras: list[str],
    desde: Mes,
    hasta: Mes,
) -> pd.DataFrame:
    """Una fila por barra con lo que hay en el periodo, para previsualizar un exporte.

    Columnas: barra, intervalos, desde, hasta, cmg_promedio, hora_extra.
    Una barra sin datos en el periodo simplemente no aparece.
    """
    if not barras:
        return pd.DataFrame(
            columns=["barra", "intervalos", "desde", "hasta", "cmg_promedio", "hora_extra"]
        )
    marcas = ", ".join("?" * len(barras))
    return con.execute(
        f"""
        SELECT barra,
               count(*) AS intervalos,
               min(fecha) AS desde,
               max(fecha) AS hasta,
               avg(cmg_usd_mwh) AS cmg_promedio,
               count(*) FILTER (WHERE es_hora_extra) AS hora_extra
        FROM {sql_dataset(base)}
        WHERE barra IN ({marcas}) AND {sql_filtro_periodo(desde, hasta)}
        GROUP BY barra
        ORDER BY barra
        """,
        barras,
    ).df()
