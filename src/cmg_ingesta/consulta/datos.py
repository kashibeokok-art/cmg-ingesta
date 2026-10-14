"""Las consultas de la app contra cmg_cen. Todo el calculo se hace en PostgreSQL.

Las mismas reglas que el Gold del programa (`gold/bloques_mes.py`,
`gold/riesgo_nodal.py`), y un test exige que den lo mismo:

- **Todo sale de los cuartos de hora**, nunca de promediar promedios. Como cada
  cuarto dura 15 minutos, `avg` sobre los cuartos de un bloque ya es el promedio
  ponderado por tiempo. NoSolar promedia los cuartos de A y C JUNTOS.
- **Riesgo = CMg(comparada) - CMg(referencia)**: positivo = la comparada es mas
  cara. Solo se comparan los cuartos donde ambas barras tienen dato (`JOIN`).
  El porcentaje por cuarto queda vacio si la referencia vale 0; el del resumen
  se calcula sobre los promedios, que casi nunca son 0.

La diferencia con el Gold: aqui el rango es por DIA (desde-hasta, inclusive), no
por mes. Por eso cada resumen trae una fila por mes con los dias que cubre, y una
fila final con el rango completo.
"""

from datetime import date

import pandas as pd
import psycopg
from psycopg import sql

Conexion = psycopg.Connection[tuple[object, ...]]

#: Rotulo de la fila del rango completo en los resumenes.
RANGO = "Rango completo"


def _df(conn: Conexion, consulta: str | sql.Composed, params: tuple[object, ...]) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(consulta, params)
        columnas = [d.name for d in cur.description or []]
        return pd.DataFrame(cur.fetchall(), columns=columnas)


# ---------------------------------------------------------------- catalogo


def barras(conn: Conexion) -> list[str]:
    """Todas las barras con datos, en orden alfabetico."""
    with conn.cursor() as cur:
        cur.execute("SELECT nombre FROM cmg.barra ORDER BY nombre")
        return [str(n) for (n,) in cur.fetchall()]


def rango_disponible(conn: Conexion) -> tuple[date, date] | None:
    """(primer dia, ultimo dia) con datos en la base, o None si esta vacia."""
    with conn.cursor() as cur:
        cur.execute("SELECT min(fecha), max(fecha) FROM cmg.dia")
        fila = cur.fetchone()
    if fila is None or fila[0] is None or fila[1] is None:
        return None
    d1, d2 = fila
    assert isinstance(d1, date) and isinstance(d2, date)
    return d1, d2


def dias_preliminares(conn: Conexion, desde: date, hasta: date) -> list[date]:
    """Los dias del rango que todavia tienen el preliminar: pueden cambiar."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.fecha FROM cmg.dia d JOIN cmg.origen o USING (origen_id)
            WHERE o.nombre = 'pagina_cen_pre' AND d.fecha BETWEEN %s AND %s
            ORDER BY d.fecha
            """,
            (desde, hasta),
        )
        return [f for (f,) in cur.fetchall() if isinstance(f, date)]


def revisiones(conn: Conexion, desde: date, hasta: date) -> pd.DataFrame:
    """Los dias del rango que el Coordinador reviso despues de cargados, y cuanto cambio."""
    return _df(
        conn,
        """
        SELECT r.fecha, oa.nombre AS antes, od.nombre AS despues, r.documento_despues AS documento,
               r.detectado_en::date AS detectado, r.cuartos_distintos,
               r.dif_max_usd, r.dif_prom_usd
        FROM cmg.revision r
        JOIN cmg.origen od ON od.origen_id = r.origen_despues
        LEFT JOIN cmg.origen oa ON oa.origen_id = r.origen_antes
        WHERE r.fecha BETWEEN %s AND %s
        ORDER BY r.fecha, r.detectado_en
        """,
        (desde, hasta),
    )


# ---------------------------------------------------------------- versiones

#: Que version del Coordinador hay cargada en un dia, en palabras: "Definitivo v2",
#: "Preliminar v1", "Definitivo v2-1" (reemision 1). Sale del documento registrado
#: en cmg.dia; sin documento (el Maestro, o una carga sin Bronze), del origen.
_VERSION = """
    CASE
        WHEN doc.nombre IS NOT NULL THEN
            CASE WHEN doc.tipo = 'def' THEN 'Definitivo' ELSE 'Preliminar' END
            || ' v' || doc.version
            || CASE WHEN doc.reemision > 0 THEN '-' || doc.reemision ELSE '' END
        WHEN o.nombre = 'pagina_cen_def' THEN 'Definitivo'
        WHEN o.nombre = 'pagina_cen_pre' THEN 'Preliminar'
        ELSE 'Maestro (historico)'
    END
"""

_DIA = """
    cmg.dia d
    JOIN cmg.origen o USING (origen_id)
    LEFT JOIN cmg.documento doc ON doc.nombre = d.documento
"""

#: Una tabla `v(fecha, version)` con la version de cada dia del rango.
_V = f"v AS (SELECT d.fecha, {_VERSION} AS version FROM {_DIA} WHERE d.fecha BETWEEN %s AND %s)"


def versiones_por_dia(conn: Conexion, desde: date, hasta: date) -> pd.DataFrame:
    """Por dia: la version cargada, el documento del Coordinador y cuando se publico."""
    return _df(
        conn,
        f"""
        SELECT d.fecha, {_VERSION} AS version, d.documento,
               doc.publicado, d.cargado_en::date AS cargado
        FROM {_DIA}
        WHERE d.fecha BETWEEN %s AND %s
        ORDER BY d.fecha
        """,
        (desde, hasta),
    )


# -------------------------------------------------------- CMg de una barra


def serie_cmg(conn: Conexion, barra: str, desde: date, hasta: date) -> pd.DataFrame:
    """El CMg cada 15 minutos de una barra, con la version de cada dia.

    La hora 24 es la segunda 23:00 del dia de 25 horas de abril.
    """
    return _df(
        conn,
        f"""
        WITH {_V}
        SELECT c.fecha, c.hora, c.minuto, c.fecha_hora, c.bloque, c.cmg_usd_mwh,
               c.es_hora_extra, v.version
        FROM cmg.cmg_15min c LEFT JOIN v USING (fecha)
        WHERE c.barra = %s AND c.fecha BETWEEN %s AND %s
        ORDER BY c.fecha, c.hora, c.minuto
        """,
        (desde, hasta, barra, desde, hasta),
    )


_COLUMNAS_BLOQUES = """
        count(*) AS cuartos,
        avg(cmg_usd_mwh) FILTER (WHERE bloque = 'A') AS "A",
        avg(cmg_usd_mwh) FILTER (WHERE bloque = 'B') AS "B",
        avg(cmg_usd_mwh) FILTER (WHERE bloque = 'C') AS "C",
        avg(cmg_usd_mwh) FILTER (WHERE bloque = 'B') AS "Solar",
        avg(cmg_usd_mwh) FILTER (WHERE bloque IN ('A', 'C')) AS "NoSolar",
        avg(cmg_usd_mwh) AS "Total"
"""


def bloques(conn: Conexion, barra: str, desde: date, hasta: date) -> pd.DataFrame:
    """Promedio por bloque: una fila por mes y la ultima con el rango completo.

    Columnas: periodo, desde, hasta (los dias que cubre esa fila), cuartos, A, B,
    C, Solar, NoSolar, Total. Un mes cortado por el rango trae solo sus dias.
    """
    return _df(
        conn,
        f"""
        WITH datos AS (
            SELECT fecha, bloque, cmg_usd_mwh FROM cmg.cmg_15min
            WHERE barra = %s AND fecha BETWEEN %s AND %s
        )
        SELECT * FROM (
            SELECT to_char(date_trunc('month', fecha), 'YYYY-MM') AS periodo,
                   min(fecha) AS desde, max(fecha) AS hasta, {_COLUMNAS_BLOQUES}
            FROM datos GROUP BY 1
            UNION ALL
            SELECT %s::text, min(fecha), max(fecha), {_COLUMNAS_BLOQUES}
            FROM datos HAVING count(*) > 0
        ) t
        ORDER BY periodo = %s, periodo
        """,
        (barra, desde, hasta, RANGO, RANGO),
    )


def estadisticas_bloque(conn: Conexion, barra: str, desde: date, hasta: date) -> pd.DataFrame:
    """Por bloque, en todo el rango: promedio, desvio, minimo, maximo y cuartos en cero."""
    return _df(
        conn,
        """
        SELECT bloque, count(*) AS cuartos, avg(cmg_usd_mwh) AS promedio,
               stddev_samp(cmg_usd_mwh) AS desv_estandar,
               min(cmg_usd_mwh) AS minimo, max(cmg_usd_mwh) AS maximo,
               count(*) FILTER (WHERE cmg_usd_mwh = 0) AS en_cero
        FROM cmg.cmg_15min
        WHERE barra = %s AND fecha BETWEEN %s AND %s
        GROUP BY bloque ORDER BY bloque
        """,
        (barra, desde, hasta),
    )


# ------------------------------------------------------------ riesgo nodal

_PAR = f"""
        WITH {_V},
        r AS (
            SELECT fecha, hora, minuto, fecha_hora, bloque, cmg_usd_mwh AS ref
            FROM cmg.cmg_15min WHERE barra = %s AND fecha BETWEEN %s AND %s
        ),
        c AS (
            SELECT fecha, hora, minuto, cmg_usd_mwh AS comp
            FROM cmg.cmg_15min WHERE barra = %s AND fecha BETWEEN %s AND %s
        ),
        par AS (SELECT * FROM r JOIN c USING (fecha, hora, minuto))
"""


def serie_riesgo(
    conn: Conexion, referencia: str, comparada: str, desde: date, hasta: date
) -> pd.DataFrame:
    """Cada 15 minutos: el CMg de las dos barras y la diferencia, en USD/MWh y en %."""
    return _df(
        conn,
        _PAR
        + """
        SELECT fecha, hora, minuto, fecha_hora, bloque,
               ref AS cmg_referencia, comp AS cmg_comparada,
               comp - ref AS riesgo_usd_mwh,
               CASE WHEN ref <> 0 THEN (comp - ref) / ref END AS riesgo_pct,
               v.version
        FROM par LEFT JOIN v USING (fecha) ORDER BY fecha, hora, minuto
        """,
        (desde, hasta, referencia, desde, hasta, comparada, desde, hasta),
    )


def resumen_riesgo(
    conn: Conexion, referencia: str, comparada: str, desde: date, hasta: date
) -> pd.DataFrame:
    """Por mes y bloque (y el rango completo por bloque), con el % sobre los promedios."""
    agregados = """
               count(*) AS cuartos,
               avg(ref) AS cmg_referencia, avg(comp) AS cmg_comparada,
               avg(comp) - avg(ref) AS riesgo_usd_mwh,
               CASE WHEN avg(ref) <> 0 THEN (avg(comp) - avg(ref)) / avg(ref) END AS riesgo_pct,
               min(comp - ref) AS riesgo_minimo, max(comp - ref) AS riesgo_maximo,
               count(*) FILTER (WHERE ref = 0) AS referencia_en_cero
    """
    return _df(
        conn,
        _PAR
        + f"""
        SELECT * FROM (
            SELECT to_char(date_trunc('month', fecha), 'YYYY-MM') AS periodo, bloque,
                   min(fecha) AS desde, max(fecha) AS hasta, {agregados}
            FROM par GROUP BY 1, 2
            UNION ALL
            SELECT %s::text, bloque, min(fecha), max(fecha), {agregados}
            FROM par GROUP BY bloque
        ) t
        ORDER BY periodo = %s, periodo, bloque
        """,
        (desde, hasta, referencia, desde, hasta, comparada, desde, hasta, RANGO, RANGO),
    )
