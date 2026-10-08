"""Orquesta la migracion del historico: leer CMG_DB, validar, escribir Silver.

Esta es la primera funcion del programa que hace I/O de verdad. Fijate como las
piezas de M1 y M2 se usan sin modificarse:

    domain/calendario  ->  cuantos cuartos debe tener cada dia
    domain/bloques     ->  la tabla hora -> bloque
    domain/esquema     ->  el contrato de columnas
    silver/escribir    ->  la escritura atomica
    quality/checks     ->  las validaciones

El reporte se devuelve como datos. Quien llame decide si lo imprime, lo guarda o
lo ignora: esta funcion no imprime nada.
"""

from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

import duckdb
import pandas as pd

from cmg_ingesta.extract import cmg_db
from cmg_ingesta.quality import checks
from cmg_ingesta.silver import escribir

#: Firma del callback de progreso. Se inyecta para no acoplar esto a `print`.
Avisar = Callable[[str], None]


class FilaReporte(TypedDict):
    """Una linea del reporte de migracion, por mes."""

    anio: int
    mes: int
    filas: int
    dias_mal: int
    horas_mal: int
    duplicados: int
    hora_fantasma: int
    nulos: int
    detalle_largo: pd.DataFrame
    detalle_fantasma: pd.DataFrame


def _nada(_mensaje: str) -> None:
    """Callback por defecto: no avisa nada."""


def migrar_historico(
    con: duckdb.DuckDBPyConnection,
    origen: Path,
    destino: Path,
    avisar: Avisar = _nada,
) -> list[FilaReporte]:
    """Migra 2021-01 a 2024-07 (`cmg_db.PRIMER_MES`..`ULTIMO_MES`) de `origen`
    (CMG_DB) a `destino` (Silver nueva).

    Devuelve una fila de reporte por mes migrado, con las filas escritas y lo que
    encontraron las validaciones.

    Es idempotente: correrla dos veces deja el mismo resultado, porque cada mes se
    escribe reemplazando la particion completa.
    """
    meses = cmg_db.meses_a_migrar(origen)
    if not meses:
        raise ValueError(
            f"no se encontro ningun mes de {cmg_db.PRIMER_MES} a {cmg_db.ULTIMO_MES} en {origen}"
        )

    huerfanas = escribir.limpiar_temporales(destino)
    if huerfanas:
        avisar(f"se limpiaron {huerfanas} carpeta(s) temporal(es) de una corrida previa")

    desde, hasta = cmg_db.rango_de_fechas(meses)
    dias = checks.crear_tabla_calendario(con, desde, hasta)
    avisar(f"calendario: {dias:,} dias entre {desde} y {hasta}")

    reporte: list[FilaReporte] = []
    for anio, mes in meses:
        consulta = cmg_db.sql_desde_cmg_db(origen, anio, mes)

        # se valida ANTES de escribir, sobre una vista de lo que entraria
        con.execute(f"CREATE OR REPLACE TEMP VIEW _entrante AS {consulta}")
        res = checks.resumen(con, "_entrante")

        escritas = escribir.escribir_particion(con, consulta, destino, anio, mes)

        reporte.append(
            {
                "anio": anio,
                "mes": mes,
                "filas": escritas,
                "dias_mal": res["dias_con_largo_incorrecto"],
                "horas_mal": res["horas_incompletas"],
                "duplicados": res["claves_duplicadas"],
                "hora_fantasma": res["filas_hora_fantasma"],
                "nulos": res["valores_nulos"],
                "detalle_largo": res["detalle_largo"],
                "detalle_fantasma": res["detalle_fantasma"],
            }
        )
        alertas = ""
        if res["dias_con_largo_incorrecto"]:
            alertas += f"  [!] {res['dias_con_largo_incorrecto']} dia(s) con largo incorrecto"
        if res["filas_hora_fantasma"]:
            alertas += f"  [!] {res['filas_hora_fantasma']:,} filas en hora fantasma"
        avisar(f"{anio}-{mes:02d}: {escritas:>9,} filas{alertas}")
    return reporte


def totales(reporte: list[FilaReporte]) -> dict[str, int]:
    """Suma el reporte por mes en un total."""
    return {
        "filas": sum(f["filas"] for f in reporte),
        "dias_mal": sum(f["dias_mal"] for f in reporte),
        "horas_mal": sum(f["horas_mal"] for f in reporte),
        "duplicados": sum(f["duplicados"] for f in reporte),
        "hora_fantasma": sum(f["hora_fantasma"] for f in reporte),
        "nulos": sum(f["nulos"] for f in reporte),
    }
