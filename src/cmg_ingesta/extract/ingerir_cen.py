"""Orquesta la ingesta de la pagina: Bronze (ZIP) -> validar -> Silver (Parquet).

Es el mismo patron que `migrar.py`, con tres diferencias que vienen de la fuente:

1. **Un mes = todos sus ZIP.** El mes se reescribe completo desde el Bronze cada
   vez, asi que no hay que "combinar" con lo que ya estaba: Silver es una funcion
   del Bronze. Correrlo dos veces deja lo mismo (idempotencia).
2. **Incremental por huella.** Se guarda, por mes, el sha256 de los ZIP usados.
   Si no cambio ninguno, el mes no se toca. Cuando llega el `def` de un dia que
   estaba en `pre`, la huella cambia y el mes se reescribe solo.
3. **Un dia con hallazgo critico no entra.** `quality/deriva.py` revisa cada ZIP
   antes; si encuentra algo critico (columna faltante, hora en base 1, fechas de
   otro dia) ese dia se omite y se reporta. Ese mes no guarda huella, asi que se
   reintenta en la proxima corrida, cuando el catalogo ya este corregido.

Nada se imprime: el progreso va por el callback `avisar` y el resultado se
devuelve como datos.
"""

import json
import os
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TypedDict

import duckdb
import pandas as pd

from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import pagina_cen
from cmg_ingesta.quality import checks, deriva
from cmg_ingesta.silver import escribir

Avisar = Callable[[str], None]

#: Huella por mes de lo ya ingerido. Vive junto al manifiesto, en el Bronze: es
#: informacion sobre los archivos crudos, no un dato de Silver.
ESTADO = "ingesta_silver.json"


class FilaReporteCen(TypedDict):
    """Una linea del reporte de ingesta, por mes."""

    anio: int
    mes: int
    dias: int  # dias que entraron
    dias_pre: int  # de esos, cuantos son preliminares
    omitidos: list[str]  # dias que NO entraron, y por que
    filas: int
    hora_fantasma: int  # filas descartadas por ser de la hora que no existio
    dias_mal: int
    horas_mal: int
    duplicados: int
    nulos: int
    detalle_largo: pd.DataFrame


def _nada(_mensaje: str) -> None:
    """Callback por defecto: no avisa nada."""


# ------------------------------------------------------------------ estado


def leer_estado(bronze: Path) -> dict[str, list[str]]:
    """La huella por mes ('2026-09' -> [sha256, ...]). Vacia si no existe."""
    ruta = bronze / ESTADO
    if not ruta.exists():
        return {}
    try:
        datos: dict[str, list[str]] = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}  # sin huella todo se reingiere: mas lento, nunca incorrecto
    return datos


def guardar_estado(bronze: Path, estado: dict[str, list[str]]) -> None:
    """Escritura atomica, igual que el manifiesto."""
    ruta = bronze / ESTADO
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, ruta)


def _clave(mes: Mes) -> str:
    return f"{mes[0]}-{mes[1]:02d}"


# ----------------------------------------------------------------- agrupar


def por_mes(
    elegidos: dict[date, cen.EntradaManifiesto],
) -> dict[Mes, list[tuple[date, cen.EntradaManifiesto]]]:
    """Los archivos elegidos, agrupados por mes calendario."""
    meses: dict[Mes, list[tuple[date, cen.EntradaManifiesto]]] = {}
    for dia, entrada in elegidos.items():
        meses.setdefault((dia.year, dia.month), []).append((dia, entrada))
    return meses


def huella(dias: list[tuple[date, cen.EntradaManifiesto]]) -> list[str]:
    """Lo que identifica el contenido de un mes: el sha256 de cada ZIP, ordenado."""
    return sorted(e["sha256"] for _, e in dias)


# --------------------------------------------------------------- orquestar


def ingerir_pagina(
    con: duckdb.DuckDBPyConnection,
    bronze: Path,
    destino: Path,
    staging: Path,
    avisar: Avisar = _nada,
    forzar: bool = False,
) -> tuple[list[FilaReporteCen], list[deriva.Hallazgo]]:
    """Ingiere a Silver los meses de 2025 en adelante que tengan ZIP en `bronze`.

    `staging` es donde se extraen temporalmente los CSV (se borran al terminar
    cada mes). Con `forzar=True` se reescriben todos los meses aunque su huella
    no haya cambiado.

    Devuelve una fila de reporte por mes procesado y los hallazgos de deriva.
    """
    elegidos = pagina_cen.elegir_por_dia(cen.leer_manifiesto(bronze))
    if not elegidos:
        raise ValueError(
            f"no hay archivos desde {pagina_cen.PRIMER_DIA} en {bronze}. "
            "Corre primero:  cmg descargar-cen --desde AAAA-MM-DD"
        )

    huerfanas = escribir.limpiar_temporales(destino)
    if huerfanas:
        avisar(f"se limpiaron {huerfanas} carpeta(s) temporal(es) de una corrida previa")

    dias_todos = list(elegidos)
    checks.crear_tabla_calendario(con, dias_todos[0], dias_todos[-1])

    estado = leer_estado(bronze)
    reporte: list[FilaReporteCen] = []
    hallazgos: list[deriva.Hallazgo] = []
    staging.mkdir(parents=True, exist_ok=True)

    for mes, dias in por_mes(elegidos).items():
        firma = huella(dias)
        if not forzar and estado.get(_clave(mes)) == firma:
            avisar(f"{_clave(mes)}: sin cambios, se omite")
            continue

        with tempfile.TemporaryDirectory(dir=staging) as tmp:
            fila, h = _ingerir_mes(con, bronze, destino, Path(tmp), mes, dias)
        hallazgos += h
        reporte.append(fila)

        # sin omitidos se guarda la huella; con omitidos, se reintenta la proxima vez
        if not fila["omitidos"]:
            estado[_clave(mes)] = firma
            guardar_estado(bronze, estado)

        alertas = ""
        if fila["dias_pre"]:
            alertas += f"  ({fila['dias_pre']} dia(s) preliminar)"
        if fila["omitidos"]:
            alertas += f"  [!] {len(fila['omitidos'])} dia(s) omitido(s)"
        if fila["dias_mal"]:
            alertas += f"  [!] {fila['dias_mal']} dia(s) con largo incorrecto"
        avisar(f"{_clave(mes)}: {fila['filas']:>9,} filas, {fila['dias']} dias{alertas}")
    return reporte, hallazgos


def _ingerir_mes(
    con: duckdb.DuckDBPyConnection,
    bronze: Path,
    destino: Path,
    tmp: Path,
    mes: Mes,
    dias: list[tuple[date, cen.EntradaManifiesto]],
) -> tuple[FilaReporteCen, list[deriva.Hallazgo]]:
    """Valida, mapea y escribe UN mes. Separado para que el bucle se lea solo."""
    hallazgos: list[deriva.Hallazgo] = []
    consultas: list[str] = []
    crudas: list[str] = []
    omitidos: list[str] = []
    dias_pre = 0

    for dia, entrada in dias:
        ruta = bronze / entrada["nombre"]
        if not ruta.exists():
            omitidos.append(f"{dia}: {entrada['nombre']} esta en el manifiesto pero no en disco")
            continue
        h = deriva.revisar_zip(ruta, dia)
        hallazgos += h
        criticos = [x["tipo"] for x in h if x["severidad"] == "critico"]
        if criticos:
            omitidos.append(f"{dia}: hallazgo critico ({', '.join(criticos)})")
            continue
        csv = pagina_cen.extraer_comparativo(ruta, tmp)
        consultas.append(pagina_cen.sql_un_dia(csv, dia, entrada["tipo"]))
        crudas.append(pagina_cen.sql_crudo_un_dia(csv, entrada["tipo"]))
        dias_pre += entrada["tipo"] == "pre"

    if not consultas:
        # no se escribe nada: un mes vacio borraria lo que hubiera antes
        return _fila(mes, 0, 0, omitidos, 0, 0, None), hallazgos

    # se cuenta la hora fantasma en el CRUDO; en lo que entra ya no debe estar
    con.execute(f"CREATE OR REPLACE TEMP VIEW _crudo AS {pagina_cen.unir(crudas)}")
    fantasma = checks.hora_fantasma_presente(con, "_crudo")
    filas_fantasma = int(fantasma["filas"].sum()) if len(fantasma) else 0

    # TABLA y no vista: una vista se recalcula en cada validacion, y eso es leer
    # y parsear los ~30 CSV del mes seis veces. Materializado, se leen una.
    con.execute(f"CREATE OR REPLACE TEMP TABLE _entrante AS {pagina_cen.unir(consultas)}")
    res = checks.resumen(con, "_entrante")

    escritas = escribir.escribir_particion(con, "SELECT * FROM _entrante", destino, *mes)
    con.execute("DROP TABLE _entrante")
    fila = _fila(mes, len(consultas), dias_pre, omitidos, escritas, filas_fantasma, res)
    return fila, hallazgos


def _fila(
    mes: Mes,
    dias: int,
    dias_pre: int,
    omitidos: list[str],
    filas: int,
    hora_fantasma: int,
    res: checks.Resumen | None,
) -> FilaReporteCen:
    return {
        "anio": mes[0],
        "mes": mes[1],
        "dias": dias,
        "dias_pre": dias_pre,
        "omitidos": omitidos,
        "filas": filas,
        "hora_fantasma": hora_fantasma,
        "dias_mal": res["dias_con_largo_incorrecto"] if res else 0,
        "horas_mal": res["horas_incompletas"] if res else 0,
        "duplicados": res["claves_duplicadas"] if res else 0,
        "nulos": res["valores_nulos"] if res else 0,
        "detalle_largo": res["detalle_largo"] if res else pd.DataFrame(),
    }


def hay_problemas(reporte: list[FilaReporteCen]) -> bool:
    """True si algun mes tuvo dias omitidos o validaciones que no pasaron.

    La hora fantasma NO cuenta: es un defecto conocido de la fuente y se descarta.
    """
    return any(
        f["omitidos"] or f["dias_mal"] or f["horas_mal"] or f["duplicados"] or f["nulos"]
        for f in reporte
    )
