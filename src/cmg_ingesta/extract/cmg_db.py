"""Fuente (a): migrar el historico desde la base antigua `CMG_DB`.

Solo **2021-01 a 2024-07** (CLAUDE.md 0.1). Desde 2024-08 manda la pagina del
Coordinador (decision del usuario, 2026-10-08): el Maestro de esos meses no se usa.

Que hace la migracion, ademas de copiar:

1. **Recalcula `bloque`** con la regla propia en vez de confiar en la columna de
   origen. Si las dos difieren, el programa nuevo manda.
2. **Agrega `es_hora_extra`** y pone `fecha_hora` en NULL ahi, en vez del dia
   siguiente a las 00:00 que calculaba la base vieja.
3. **Agrega linaje** (`origen`, `ingerido_en`), que `CMG_DB` no tiene.
4. **Unifica las barras que cambiaron de nombre** (`domain/barras.py`): las 20
   que hasta 2023-05 se escribian con Ñ quedan con su nombre actual, sin Ñ.
5. **Valida contra el calendario** y reporta lo que no calza, sin corregirlo.

Limitacion heredada que hay que tener presente: `CMG_DB` guardo el CMg en FLOAT de
32 bits, asi que trae valores como 49.10531997680664 donde la fuente decia
49.10532. Migrar no recupera esa precision; el esquema nuevo usa DOUBLE para no
volver a perderla en los datos que vengan de la pagina.
"""

import calendar as calendario_py
from datetime import date
from pathlib import Path

from cmg_ingesta.domain import barras, bloques, calendario, esquema, periodo
from cmg_ingesta.domain.periodo import Mes

#: El tramo del Maestro. Mas alla de esto, CMG_DB mezcla fuentes descartadas.
PRIMER_MES: Mes = (2021, 1)
ULTIMO_MES: Mes = (2024, 7)  # el mes anterior a coordinador_cmg.INICIO_FUENTE


def _lit(ruta: Path) -> str:
    return "'" + str(ruta).replace("'", "''") + "'"


def ruta_mes(base: Path, anio: int, mes: int) -> Path:
    """La carpeta de un mes en la base ANTIGUA (mismo formato Hive)."""
    return base / f"anio={anio}" / f"mes={mes}"


def mes_disponible(base: Path, anio: int, mes: int) -> bool:
    carpeta = ruta_mes(base, anio, mes)
    return carpeta.is_dir() and any(carpeta.glob("*.parquet"))


def sql_desde_cmg_db(base: Path, anio: int, mes: int) -> str:
    """El SELECT que mapea un mes de `CMG_DB` al esquema canonico."""
    patron = _lit(ruta_mes(base, anio, mes) / "*.parquet")
    return f"""
        SELECT
            {barras.sql_canonico()} AS barra,
            fecha,
            CAST(hora AS UTINYINT) AS hora,
            CAST(minuto AS UTINYINT) AS minuto,
            CAST({bloques.sql_bloque()} AS VARCHAR) AS bloque,
            CAST(cmg_usd_mwh AS DOUBLE) AS cmg_usd_mwh,
            (hora = {calendario.HORA_EXTRA}) AS es_hora_extra,
            CASE WHEN hora < {calendario.HORA_EXTRA}
                 THEN CAST(fecha AS TIMESTAMP)
                      + to_hours(CAST(hora AS BIGINT))
                      + to_minutes(CAST(minuto AS BIGINT))
                 ELSE NULL END AS fecha_hora,
            '{esquema.ORIGEN_MAESTRO}' AS origen,
            now()::TIMESTAMP AS ingerido_en
        FROM read_parquet({patron})
    """


def meses_a_migrar(base: Path) -> list[Mes]:
    """Los meses del tramo del Maestro que existen en la base antigua."""
    return [
        (anio, mes)
        for anio, mes in periodo.meses_entre(PRIMER_MES, ULTIMO_MES)
        if mes_disponible(base, anio, mes)
    ]


def rango_de_fechas(meses: list[Mes]) -> tuple[date, date]:
    """La primera y la ultima fecha que cubren esos meses, para el calendario."""
    if not meses:
        raise ValueError("no hay meses que migrar")
    primero, ultimo = meses[0], meses[-1]
    dias_del_mes = calendario_py.monthrange(ultimo[0], ultimo[1])[1]
    return date(primero[0], primero[1], 1), date(ultimo[0], ultimo[1], dias_del_mes)
