"""Fuente (b): mapear los ZIP de la pagina del Coordinador al esquema canonico.

Solo **2025-01 en adelante** (CLAUDE.md 0.1). Lo anterior viene del Maestro, y
esta capa se niega a tocarlo aunque el Bronze tenga ZIP de 2024.

De cada ZIP se usa un solo archivo, `CmgBarrasComparativo_AAAAMMDD_AAAAMMDD_15.csv`,
con estas reglas verificadas sobre archivos reales (CLAUDE.md 4.5):

1. **La columna depende del tipo de ZIP.** En un ZIP `def` el valor esta en
   `CMG_REAL_DEF`; en un ZIP `pre` esta en `CMG_REAL_PRE`, y `CMG_REAL_DEF` viene
   en **0** (no vacia). Tomar la columna equivocada carga ceros sin ningun error.
   Medido el 2026-09-28: el PRE del ZIP pre y el DEF del ZIP def coinciden en las
   158.016 filas.
2. **HORA en base 0**, de 0 a 23; el dia largo de abril trae ademas la 24 (la
   segunda 23:00), que es justo la convencion del esquema.
3. **La hora fantasma** (hora 0 del dia corto de septiembre, publicada en 0,00) se
   descarta: esa hora no existio.
4. **El estado queda en el linaje**: `origen` es `pagina_cen_def` o
   `pagina_cen_pre`, asi un exporte dice si trae datos preliminares.
"""

import re
import zipfile
from datetime import date
from pathlib import Path

from cmg_ingesta.domain import bloques, calendario, esquema
from cmg_ingesta.extract import coordinador_cmg as cen

#: Desde aqui manda la pagina. Antes, el Maestro.
PRIMER_DIA = date(2025, 1, 1)

RE_COMPARATIVO = re.compile(r"CmgBarrasComparativo_\d{8}_\d{8}_15\.csv$", re.I)

#: Que columna leer segun el tipo de ZIP (regla 1).
COLUMNA_VALOR = {
    "def": "CMG_REAL_DEF[USD/MWh]",
    "pre": "CMG_REAL_PRE[USD/MWh]",
}

ORIGEN_POR_TIPO = {
    "def": esquema.ORIGEN_PAGINA_DEF,
    "pre": esquema.ORIGEN_PAGINA_PRE,
}

#: Para elegir entre varios archivos del mismo dia. Igual que `cen.mejor_version`.
_RANGO_TIPO = {"def": 2, "pre": 1}


def elegir_por_dia(
    manifiesto: dict[str, cen.EntradaManifiesto],
) -> dict[date, cen.EntradaManifiesto]:
    """El archivo que manda para cada dia, desde 2025.

    `def` gana a `pre`; entre iguales, mayor version y luego mayor reemision.
    Los archivos de tipo desconocido no se usan: primero hay que catalogarlos
    (los reporta `quality/deriva.py`).
    """
    elegidos: dict[date, cen.EntradaManifiesto] = {}
    for e in manifiesto.values():
        if e["tipo"] not in _RANGO_TIPO:
            continue
        dia = date.fromisoformat(e["fecha_operacion"])
        if dia < PRIMER_DIA:
            continue
        actual = elegidos.get(dia)
        if actual is None or _rango(e) > _rango(actual):
            elegidos[dia] = e
    return dict(sorted(elegidos.items()))


def _rango(e: cen.EntradaManifiesto) -> tuple[int, int, int]:
    return (_RANGO_TIPO[e["tipo"]], e["version"], e["reemision"])


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
    """Las 5 columnas que importan del CSV, renombradas y con su tipo. Sin filtrar.

    Se lee todo como texto (`all_varchar`) y se convierte a mano: si DuckDB
    adivina el tipo, la columna DEF de un ZIP pre (todo ceros) sale BIGINT y la
    del def sale DOUBLE. Leer texto y castear deja un solo comportamiento.
    """
    return f"""
        SELECT BARRA AS barra,
               CAST(strptime(FECHA, '%Y%m%d') AS DATE) AS fecha,
               CAST(HORA AS UTINYINT) AS hora,
               CAST(MINUTO AS UTINYINT) AS minuto,
               CAST("{COLUMNA_VALOR[tipo]}" AS DOUBLE) AS cmg_usd_mwh
        FROM read_csv({_lit(csv)}, delim=';', header=true, all_varchar=true)
    """


def sql_un_dia(csv: Path, dia: date, tipo: str) -> str:
    """El SELECT de un dia, ya en el esquema canonico y sin la hora fantasma."""
    # regla 3: la hora que el reloj no tuvo se descarta aqui, no despues
    fantasma = calendario.horas_inexistentes(dia)
    filtro = f"WHERE hora NOT IN ({', '.join(map(str, fantasma))})" if fantasma else ""
    return f"""
        SELECT barra, fecha, hora, minuto,
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
