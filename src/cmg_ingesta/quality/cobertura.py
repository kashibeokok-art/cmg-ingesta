"""Cobertura de la base: que dias DEBERIA tener Silver, cuales tiene y por que faltan.

La regla central: **lo esperado es fijo, no sale de los datos.** La base debe
cubrir desde 2021-01-01 (inicio del Maestro) hasta hace `DIAS_GRACIA` dias. Si lo
esperado se calculara desde lo que ya hay ("del primer al ultimo dia cargado"),
un hueco al principio o un salto hacia dias recientes lo esconderia: eso es
exactamente lo que pasaba (CLAUDE.md, bitacora 2026-10-08).

Funciona sin red: lee solo la columna `fecha` de Silver (0,35 s con 202 M filas)
y el manifiesto del Bronze.

Cada dia faltante se clasifica por lo que hay que hacer para recuperarlo:

    maestro        antes de 2025: viene de CMG_DB  -> cmg migrar-historico
    sin_descargar  desde 2025, sin ZIP en el Bronze -> cmg descargar-cen
    sin_ingerir    hay ZIP pero no esta en Silver  -> cmg ingerir-pagina
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Literal, TypedDict

import duckdb

from cmg_ingesta.domain import periodo
from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.extract import cmg_db
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.quality import deriva
from cmg_ingesta.silver import leer

#: Primer dia que la base debe tener: el inicio del Maestro.
INICIO_BASE = date(cmg_db.PRIMER_MES[0], cmg_db.PRIMER_MES[1], 1)

Motivo = Literal["maestro", "sin_descargar", "sin_ingerir"]

#: Que hacer con cada motivo. Se usa en los hallazgos y en `cmg estado`.
ACCION: dict[Motivo, str] = {
    "maestro": "viene del Maestro: revisar CMG_DB y correr `cmg migrar-historico <carpeta>`",
    "sin_descargar": "correr `cmg descargar-cen --desde {inicio} --hasta {fin}`",
    "sin_ingerir": "hay ZIP en el Bronze: correr `cmg ingerir-pagina`; si sigue, el dia "
    "tuvo un hallazgo critico (ver data/alertas/)",
}


class MesIncompleto(TypedDict):
    mes: Mes
    con_datos: int
    esperados: int


class Cobertura(TypedDict):
    """Lo que la base deberia tener contra lo que tiene."""

    desde: date
    hasta: date  # ultimo dia esperado (hoy - gracia)
    esperados: int
    con_datos: int
    faltantes: dict[Motivo, list[date]]
    meses_vacios: list[Mes]
    meses_incompletos: list[MesIncompleto]


def dias_en_base(con: duckdb.DuckDBPyConnection, base: Path) -> set[date]:
    """Los dias que tienen al menos una fila en Silver. Vacio si no hay base."""
    if not leer.base_existe(base):
        return set()
    filas = con.execute(f"SELECT DISTINCT fecha FROM {leer.sql_dataset(base)}").fetchall()
    return {f[0] for f in filas}


def ultimo_dia_esperado(hoy: date, dias_gracia: int = deriva.DIAS_GRACIA) -> date:
    """Antes de `dias_gracia` dias es normal que un dia no este publicado todavia."""
    return hoy - timedelta(days=dias_gracia)


def clasificar(dia: date, descargados: set[str]) -> Motivo:
    """Por que falta un dia, segun de donde deberia venir y que hay en el Bronze.

    `descargados` son las `fecha_operacion` del manifiesto (texto ISO).
    """
    if dia < cen.INICIO_FUENTE:
        return "maestro"
    return "sin_ingerir" if dia.isoformat() in descargados else "sin_descargar"


def calcular(
    presentes: set[date],
    manifiesto: dict[str, cen.EntradaManifiesto],
    hoy: date,
    desde: date = INICIO_BASE,
    dias_gracia: int = deriva.DIAS_GRACIA,
) -> Cobertura:
    """Compara los dias presentes contra el rango esperado. Funcion pura.

    `hoy` se inyecta (como en `deriva.revisar_completitud`) para que el resultado
    no dependa del reloj.
    """
    hasta = ultimo_dia_esperado(hoy, dias_gracia)
    esperados = list(cen.dias_entre(desde, hasta)) if desde <= hasta else []

    descargados = {e["fecha_operacion"] for e in manifiesto.values()}
    faltantes: dict[Motivo, list[date]] = {"maestro": [], "sin_descargar": [], "sin_ingerir": []}
    for dia in esperados:
        if dia not in presentes:
            faltantes[clasificar(dia, descargados)].append(dia)

    # por mes: cuantos dias esperados tiene y cuantos con datos
    por_mes: dict[Mes, list[int]] = {}
    for dia in esperados:
        cuenta = por_mes.setdefault((dia.year, dia.month), [0, 0])
        cuenta[0] += dia in presentes
        cuenta[1] += 1

    vacios = [mes for mes, (hay, _) in por_mes.items() if hay == 0]
    incompletos: list[MesIncompleto] = [
        {"mes": mes, "con_datos": hay, "esperados": esp}
        for mes, (hay, esp) in por_mes.items()
        if 0 < hay < esp
    ]
    return {
        "desde": desde,
        "hasta": hasta,
        "esperados": len(esperados),
        "con_datos": sum(1 for d in esperados if d in presentes),
        "faltantes": faltantes,
        "meses_vacios": vacios,
        "meses_incompletos": incompletos,
    }


def total_faltantes(cob: Cobertura) -> int:
    return sum(len(dias) for dias in cob["faltantes"].values())


def rangos_de_meses(meses: list[Mes]) -> list[tuple[Mes, Mes]]:
    """Agrupa meses consecutivos: [2025-01 .. 2026-09] es UN rango, no 21 lineas."""
    grupos: list[tuple[Mes, Mes]] = []
    for mes in sorted(meses):
        if grupos and periodo.indice(mes) - periodo.indice(grupos[-1][1]) == 1:
            grupos[-1] = (grupos[-1][0], mes)
        else:
            grupos.append((mes, mes))
    return grupos


def _texto_meses(inicio: Mes, fin: Mes) -> str:
    a, b = periodo.formatear(inicio), periodo.formatear(fin)
    return a if inicio == fin else f"{a} a {b}"


def revisar(cob: Cobertura) -> list[deriva.Hallazgo]:
    """Hallazgos para revision manual, agrupados en rangos de dias consecutivos.

    Los dias `sin_descargar` NO se reportan aqui: ya los reporta
    `deriva.revisar_completitud` como `dia_sin_registro` (mira el Bronze), y dos
    avisos por el mismo hueco son ruido. Aqui va lo que solo Silver puede ver.
    """
    hallazgos: list[deriva.Hallazgo] = []
    tipos: dict[Motivo, str] = {
        "maestro": "dias_sin_datos_maestro",
        "sin_ingerir": "dias_descargados_sin_ingerir",
    }
    for motivo, tipo in tipos.items():
        for inicio, fin in deriva.rangos(cob["faltantes"][motivo]):
            n = (fin - inicio).days + 1
            hallazgos.append(
                deriva._h(
                    "aviso",
                    tipo,
                    f"{n} dia(s) que la base deberia tener y no tiene.",
                    deriva.texto_rango(inicio, fin),
                    ACCION[motivo].format(inicio=inicio, fin=fin).capitalize() + ".",
                )
            )
    return hallazgos


def lineas_estado(cob: Cobertura, max_rangos: int = 8) -> list[str]:
    """El resumen de cobertura para `cmg estado` y la cabecera del menu."""
    faltan = total_faltantes(cob)
    lineas = [
        f"  cobertura : {cob['con_datos']:,} de {cob['esperados']:,} dias esperados "
        f"({cob['desde']} a {cob['hasta']})"
    ]
    if not faltan:
        lineas.append("  faltantes : ninguno")
        return lineas

    lineas.append(f"  faltantes : {faltan:,} dia(s)")
    etiquetas: dict[Motivo, str] = {
        "maestro": "del Maestro",
        "sin_descargar": "sin descargar",
        "sin_ingerir": "descargados, sin ingerir",
    }
    mostrados = 0
    for motivo, etiqueta in etiquetas.items():
        for inicio, fin in deriva.rangos(cob["faltantes"][motivo]):
            if mostrados == max_rangos:
                lineas.append("     ... (mas tramos: ver `cmg vigilar-fuente`)")
                break
            n = (fin - inicio).days + 1
            lineas.append(f"     {deriva.texto_rango(inicio, fin):<24} {n:>5,} dia(s)  {etiqueta}")
            mostrados += 1
    if cob["meses_vacios"]:
        tramos = ", ".join(_texto_meses(a, b) for a, b in rangos_de_meses(cob["meses_vacios"]))
        lineas.append(f"  meses sin ningun dato : {len(cob['meses_vacios'])}  ({tramos})")
    if cob["meses_incompletos"]:
        partes = ", ".join(
            f"{periodo.formatear(m['mes'])} ({m['con_datos']}/{m['esperados']} dias)"
            for m in cob["meses_incompletos"]
        )
        lineas.append(f"  meses incompletos     : {partes}")
    return lineas
