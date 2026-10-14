"""Fuente (b): mapear los ZIP de la pagina del Coordinador al esquema canonico.

Solo **2024-08 en adelante** (`coordinador_cmg.INICIO_FUENTE`, CLAUDE.md 0.1).
Lo anterior viene del Maestro, y esta capa se niega a tocarlo aunque el Bronze tenga ZIP de 2024.

De cada ZIP se usa un solo archivo, `CmgBarrasComparativo_AAAAMMDD_AAAAMMDD_15.csv`,
con estas reglas verificadas sobre archivos reales (CLAUDE.md 4.5):

1. **La columna depende del tipo de ZIP.** En un ZIP `def` el valor esta en
   `CMG_REAL_DEF`; en un ZIP `pre` esta en `CMG_REAL_PRE`, y `CMG_REAL_DEF` viene
   en **0** (no vacia). Tomar la columna equivocada carga ceros sin ningun error.
   Medido el 2026-09-28: el PRE del ZIP pre y el DEF del ZIP def coinciden en las
   158.016 filas. Los ZIP publicados hasta el 2024-10-08 (operacion 2024-08 y
   2024-09) traen las mismas columnas con otro nombre y en mills/kWh (= USD/MWh):
   `CMgP` es el PRE y `CMgR` el DEF (verificado, ver `deriva.ENCABEZADO_COMPARATIVO_2024`).
   La columna se elige leyendo el encabezado de cada CSV.
2. **HORA en base 0**, de 0 a 23; el dia largo de abril trae ademas la 24 (la
   segunda 23:00), que es justo la convencion del esquema.
3. **La hora fantasma** (hora 0 del dia corto de septiembre, publicada en 0,00) se
   descarta: esa hora no existio.
4. **El estado queda en el linaje**: `origen` es `pagina_cen_def` o
   `pagina_cen_pre`, asi un exporte dice si trae datos preliminares.
5. **Mismos nombres de barra que el historico** (`domain/barras.py`). Hoy la
   pagina ya publica el nombre oficial; se aplica igual, por si vuelve una Ñ.
"""

import re
import zipfile
from datetime import date
from pathlib import Path

from cmg_ingesta.domain import barras, bloques, calendario, esquema
from cmg_ingesta.extract import coordinador_cmg as cen

#: Desde aqui manda la pagina. Antes, el Maestro.
PRIMER_DIA = cen.INICIO_FUENTE

RE_COMPARATIVO = re.compile(r"CmgBarrasComparativo_\d{8}_\d{8}_15(_NUEVO)?\.csv$", re.I)

#: Que columna leer segun el tipo de ZIP (regla 1).
COLUMNA_VALOR = {
    "def": "CMG_REAL_DEF[USD/MWh]",
    "pre": "CMG_REAL_PRE[USD/MWh]",
}

#: Lo mismo en el formato de 2024-08/09 (mills/kWh = USD/MWh, sin conversion).
COLUMNA_VALOR_2024 = {
    "def": "CMgR[mills/kWh]",
    "pre": "CMgP[mills/kWh]",
}


#: Todas las formas en que aparece la columna del valor, por tipo de ZIP.
COLUMNAS_POSIBLES = {
    "def": re.compile(r"^(CMG_REAL_DEF([ _]v2)?\[USD/MWh\]|CMgR\[mills/kWh\])$"),
    "pre": re.compile(r"^(CMG_REAL_PRE([ _]v2)?\[USD/MWh\]|CMgP\[mills/kWh\])$"),
}


def columna_valor(csv: Path, tipo: str) -> str:
    """La columna del CMg en ESTE csv, mirando su encabezado (regla 1).

    Si hay mas de una (las revisiones v2 de 2025-02/03 traen `CMG_REAL_DEF` y
    `CMG_REAL_DEF v2`), se usa la ULTIMA del encabezado: es la revisada. Verificado
    el 2026-10-14 en los 49 ZIP con dos columnas (error A19): la primera repite
    el v1 (100 %) y la ultima coincide con el documento publicado despues. Por
    posicion y no por etiqueta: `pre-v2_250228` trae el orden al reves
    (`PRE v2, DEF v2, PRE, DEF`) y ahi lo revisado es la columna SIN "v2".
    """
    with csv.open("rb") as f:
        cab = f.readline().decode("latin-1").strip().split(";")
    candidatas = [c for c in cab if COLUMNAS_POSIBLES[tipo].match(c)]
    if not candidatas:
        raise ValueError(f"{csv.name}: no trae {COLUMNA_VALOR[tipo]} ni {COLUMNA_VALOR_2024[tipo]}")
    return candidatas[-1]


ORIGEN_POR_TIPO = {
    "def": esquema.ORIGEN_PAGINA_DEF,
    "pre": esquema.ORIGEN_PAGINA_PRE,
}


def dias_dudosos(
    manifiesto: dict[str, cen.EntradaManifiesto],
) -> dict[date, list[str]]:
    """Dias con algun archivo que no se pudo clasificar (tipo "desconocido").

    En ese dia NO se elige nada automaticamente: el archivo raro podria ser la
    version vigente (un v2) y usar otra seria cargar un dato viejo EN SILENCIO.
    Paso de verdad: con el lector anterior, en 34 dias se habria elegido una
    version superada. La ingesta los omite y los reporta para revision manual.
    """
    dudosos: dict[date, list[str]] = {}
    for e in manifiesto.values():
        if e["tipo"] not in cen.RANGO_TIPO:
            dudosos.setdefault(date.fromisoformat(e["fecha_operacion"]), []).append(e["nombre"])
    return dudosos


def elegir_por_dia(
    manifiesto: dict[str, cen.EntradaManifiesto],
) -> dict[date, cen.EntradaManifiesto]:
    """El archivo que manda para cada dia, desde el inicio de la fuente.

    La regla es `cen.clave_version`: `def` gana a `pre`; despues mayor version,
    mayor reemision y, si empatan, el publicado despues. Los dias de
    `dias_dudosos` se dejan fuera.
    """
    dudosos = dias_dudosos(manifiesto)
    elegidos: dict[date, cen.EntradaManifiesto] = {}
    for e in manifiesto.values():
        dia = date.fromisoformat(e["fecha_operacion"])
        if dia < PRIMER_DIA or dia in dudosos:
            continue
        actual = elegidos.get(dia)
        if actual is None or cen.clave_version(e) > cen.clave_version(actual):
            elegidos[dia] = e
    return dict(sorted(elegidos.items()))


def extraer_comparativo(ruta_zip: Path, destino: Path) -> Path:
    """Copia el CSV comparativo del ZIP a `destino` y devuelve su ruta.

    DuckDB no lee dentro de un ZIP, asi que se extrae ese UN archivo (16 MB) y no
    los otros cinco (casi 10 MB de Excel que no se usan).
    """
    with zipfile.ZipFile(ruta_zip) as zf:
        miembro = next((n for n in zf.namelist() if RE_COMPARATIVO.search(n)), None)
        if miembro is None:
            raise ValueError(f"{ruta_zip.name} no tiene CmgBarrasComparativo_*_15.csv")
        salida = destino / f"{ruta_zip.stem}.csv"
        salida.write_bytes(zf.read(miembro))
    return salida


def _lit(texto: str | Path) -> str:
    return "'" + str(texto).replace("'", "''") + "'"


def sql_crudo_un_dia(csv: Path, tipo: str) -> str:
    """Las 5 columnas que importan del CSV, renombradas y con su tipo.

    Se lee todo como texto (`all_varchar`) y se convierte a mano: si DuckDB
    adivina el tipo, la columna DEF de un ZIP pre (todo ceros) sale BIGINT y la
    del def sale DOUBLE. Leer texto y castear deja un solo comportamiento.

    Las lineas VACIAS (`;;;;;;;;;;`: sin barra, fecha ni hora) se descartan. Cinco
    definitivos de 2025-01 (10, 13, 14, 15 y 16) traen el CSV rellenado hasta 200.000
    lineas asi, como un export de Excel: 52.351 lineas de relleno cada uno, que
    entraban a Silver como filas nulas (2026-10-14). Una fila con datos pero sin
    barra NO se descarta: eso si es un problema, y la validacion lo reporta.
    """
    return f"""
        SELECT BARRA AS barra,
               CAST(strptime(FECHA, '%Y%m%d') AS DATE) AS fecha,
               CAST(HORA AS UTINYINT) AS hora,
               CAST(MINUTO AS UTINYINT) AS minuto,
               CAST("{columna_valor(csv, tipo)}" AS DOUBLE) AS cmg_usd_mwh
        FROM read_csv({_lit(csv)}, delim=';', header=true, all_varchar=true)
        WHERE NOT (BARRA IS NULL AND FECHA IS NULL AND HORA IS NULL)
    """


def sql_un_dia(csv: Path, dia: date, tipo: str) -> str:
    """El SELECT de un dia, ya en el esquema canonico y sin la hora fantasma."""
    # regla 3: la hora que el reloj no tuvo se descarta aqui, no despues
    fantasma = calendario.horas_inexistentes(dia)
    filtro = f"WHERE hora NOT IN ({', '.join(map(str, fantasma))})" if fantasma else ""
    return f"""
        SELECT {barras.sql_canonico()} AS barra, fecha, hora, minuto,
               CAST({bloques.sql_bloque()} AS VARCHAR) AS bloque,
               cmg_usd_mwh,
               (hora = {calendario.HORA_EXTRA}) AS es_hora_extra,
               CASE WHEN hora < {calendario.HORA_EXTRA}
                    THEN CAST(fecha AS TIMESTAMP)
                         + to_hours(CAST(hora AS BIGINT))
                         + to_minutes(CAST(minuto AS BIGINT))
                    ELSE NULL END AS fecha_hora,
               '{ORIGEN_POR_TIPO[tipo]}' AS origen,
               now()::TIMESTAMP AS ingerido_en
        FROM ({sql_crudo_un_dia(csv, tipo)})
        {filtro}
    """


def unir(consultas: list[str]) -> str:
    """Varias consultas del mismo esquema en una sola."""
    if not consultas:
        raise ValueError("no hay dias que unir")
    return "\nUNION ALL\n".join(f"({c})" for c in consultas)
