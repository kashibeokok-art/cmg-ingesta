"""El esquema canonico de la capa Silver: una fila = una barra en un cuarto de hora.

Este modulo es el CONTRATO de datos. Todo lo que entra a Silver, venga de donde
venga, tiene estas columnas con estos tipos. Las fuentes nuevas se adaptan a este
esquema, no al revés.

Granularidad (grain): barra x fecha x hora x minuto. Una fila por combinacion.
"""

from typing import Final

#: Clave natural (natural key): identifica una fila de forma unica.
#: Es COMPUESTA: ninguna columna sola alcanza. La ingesta deduplica por esta tupla.
CLAVE_NATURAL: Final = ("barra", "fecha", "hora", "minuto")

#: Columnas de particion. Se guardan en la RUTA (anio=2025/mes=3/), no dentro del
#: archivo, asi que al filtrar por mes DuckDB ni abre los archivos de los otros.
COLUMNAS_PARTICION: Final = ("anio", "mes")

#: Las columnas de datos, en orden, con su tipo DuckDB.
#:
#: cmg_usd_mwh es DOUBLE y no FLOAT a proposito: en FLOAT (32 bits) hay ~7 digitos
#: significativos, y un valor como 208.03955 necesita 8. La base antigua usaba
#: FLOAT y guardaba 49.10531997680664 donde la fuente decia 49.10532. Eso ya nos
#: hizo perder tiempo dos veces persiguiendo una "diferencia" que era el tipo.
COLUMNAS: Final[dict[str, str]] = {
    "barra": "VARCHAR",
    "fecha": "DATE",
    "hora": "UTINYINT",  # 0 a 24; la 24 es la hora extra de abril
    "minuto": "UTINYINT",  # 0, 15, 30, 45
    "bloque": "VARCHAR",  # A, B o C. Derivado de hora
    "cmg_usd_mwh": "DOUBLE",
    "es_hora_extra": "BOOLEAN",  # True solo en la segunda 23:00 del dia largo
    "fecha_hora": "TIMESTAMP",  # NULL cuando es_hora_extra, porque seria ambiguo
}

#: Metadatos de linaje (lineage). Permiten responder "de donde salio esta fila".
#: Van en el archivo, no en la ruta, porque cambian fila a fila cuando un mes se
#: completa desde dos fuentes distintas.
COLUMNAS_LINAJE: Final[dict[str, str]] = {
    "origen": "VARCHAR",  # ver ORIGENES
    "ingerido_en": "TIMESTAMP",  # cuando entro a la base
}

#: Las dos unicas fuentes del programa (CLAUDE.md 0.1). La pagina se separa en
#: definitivo y preliminar: el preliminar puede cambiar cuando llegue el
#: definitivo, y quien use el dato tiene que poder saberlo.
ORIGEN_MAESTRO: Final = "maestro_cmg_db"
ORIGEN_PAGINA_DEF: Final = "pagina_cen_def"
ORIGEN_PAGINA_PRE: Final = "pagina_cen_pre"
ORIGENES: Final = (ORIGEN_MAESTRO, ORIGEN_PAGINA_DEF, ORIGEN_PAGINA_PRE)


def columnas_archivo() -> list[str]:
    """Las columnas que se escriben en el parquet, en orden."""
    return [*COLUMNAS, *COLUMNAS_LINAJE]


def columnas_totales() -> list[str]:
    """Todas las columnas, incluidas las de particion (que vienen de la ruta)."""
    return [*columnas_archivo(), *COLUMNAS_PARTICION]


def sql_lista_columnas() -> str:
    """Las columnas separadas por coma, para interpolar en un SELECT."""
    return ", ".join(columnas_archivo())


def sql_clave_natural(alias_a: str, alias_b: str) -> str:
    """Condicion de igualdad por clave natural entre dos tablas.

    Se usa para deduplicar y para el modo combinar: `a.barra = b.barra AND ...`.
    """
    return " AND ".join(f"{alias_a}.{col} = {alias_b}.{col}" for col in CLAVE_NATURAL)
