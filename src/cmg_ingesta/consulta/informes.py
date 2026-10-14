"""Lo que hace cada boton de la app: validar, consultar y escribir el Excel.

Separado de la ventana para poder probarlo sin abrir ninguna: la ventana solo
junta lo que eligio el usuario y llama a estas funciones. Los errores que el
usuario puede corregir (fechas al reves, una barra sin datos) son `ValueError`
con un mensaje en castellano, listo para mostrar.
"""

import re
from datetime import date, datetime
from pathlib import Path

from cmg_ingesta.consulta import datos, excel
from cmg_ingesta.consulta.datos import Conexion


def validar_rango(desde: date, hasta: date, disponible: tuple[date, date] | None) -> None:
    if desde > hasta:
        raise ValueError(
            f"La fecha desde ({desde:%d-%m-%Y}) es posterior a hasta ({hasta:%d-%m-%Y})."
        )
    if disponible is None:
        raise ValueError("La base no tiene datos todavia.")
    d1, d2 = disponible
    if hasta < d1 or desde > d2:
        raise ValueError(
            f"La base tiene datos del {d1:%d-%m-%Y} al {d2:%d-%m-%Y}; el rango pedido queda fuera."
        )


def nombre_sugerido(*partes: str) -> str:
    """Un nombre de archivo valido en Windows."""
    texto = "_".join(partes)
    return re.sub(r'[<>:"/\\|?*]', "_", texto).strip().rstrip(".") + ".xlsx"


def _avisos_preliminares(dias: list[date]) -> str:
    if not dias:
        return "Ninguno: todo el rango tiene datos definitivos."
    return (
        f"{len(dias)} dia(s) con datos preliminares que todavia pueden cambiar "
        f"(del {dias[0]:%d-%m-%Y} al {dias[-1]:%d-%m-%Y})."
    )


def cmg_barra(conn: Conexion, barra: str, desde: date, hasta: date, ruta: Path) -> Path:
    """Excel del CMg de una barra: la serie de 15 minutos y los promedios por bloque."""
    validar_rango(desde, hasta, datos.rango_disponible(conn))
    serie = datos.serie_cmg(conn, barra, desde, hasta)
    if serie.empty:
        raise ValueError(f"{barra} no tiene datos entre el {desde:%d-%m-%Y} y el {hasta:%d-%m-%Y}.")
    preliminares = datos.dias_preliminares(conn, desde, hasta)
    cmg = serie["cmg_usd_mwh"]
    info = {
        "Barra": barra,
        "Desde": desde,
        "Hasta": hasta,
        "Dias con datos": serie["fecha"].nunique(),
        "Cuartos de hora": f"{len(serie):,}".replace(",", "."),
        "CMg promedio [USD/MWh]": round(float(cmg.mean()), 2),
        "CMg minimo [USD/MWh]": round(float(cmg.min()), 2),
        "CMg maximo [USD/MWh]": round(float(cmg.max()), 2),
        "Cuartos en cero": int((cmg == 0).sum()),
        "Datos preliminares": _avisos_preliminares(preliminares),
        "Version": "cada cuarto dice de que documento del Coordinador sale "
        "(Definitivo v2 = segunda version del definitivo); detalle en 'Version por dia'",
        "Bloques": "A: 23:00 a 07:59 | B: 08:00 a 17:59 | C: 18:00 a 22:59",
        "Solar": "promedio del bloque B",
        "NoSolar": "promedio de los cuartos de A y C juntos (no de sus promedios)",
        "Hora extra (abril)": "el dia de 25 horas trae la segunda 23:00 como hora 24",
        "Fuente": "Coordinador Electrico Nacional, CMg real; base cmg_cen",
        "Generado": datetime.now(),
    }
    return excel.libro_cmg(
        ruta,
        serie,
        datos.bloques(conn, barra, desde, hasta),
        datos.estadisticas_bloque(conn, barra, desde, hasta),
        datos.versiones_por_dia(conn, desde, hasta),
        datos.revisiones(conn, desde, hasta),
        info,
    )


def riesgo_nodal(
    conn: Conexion, referencia: str, comparada: str, desde: date, hasta: date, ruta: Path
) -> Path:
    """Excel del riesgo nodal: CMg(comparada) - CMg(referencia), cada 15 minutos y por bloque."""
    if referencia == comparada:
        raise ValueError("La barra de referencia y la comparada son la misma.")
    validar_rango(desde, hasta, datos.rango_disponible(conn))
    serie = datos.serie_riesgo(conn, referencia, comparada, desde, hasta)
    if serie.empty:
        raise ValueError(
            f"{referencia} y {comparada} no tienen cuartos de hora en comun entre el "
            f"{desde:%d-%m-%Y} y el {hasta:%d-%m-%Y}."
        )
    riesgo = serie["riesgo_usd_mwh"]
    info = {
        "Referencia": referencia,
        "Comparada": comparada,
        "Formula": "riesgo = CMg(comparada) - CMg(referencia)",
        "Signo": "positivo = la comparada es mas cara que la referencia",
        "Desde": desde,
        "Hasta": hasta,
        "Cuartos en comun": f"{len(serie):,}".replace(",", "."),
        "Riesgo promedio [USD/MWh]": round(float(riesgo.mean()), 2),
        "Riesgo minimo [USD/MWh]": round(float(riesgo.min()), 2),
        "Riesgo maximo [USD/MWh]": round(float(riesgo.max()), 2),
        "Riesgo [%]": "por cuarto: vacio si la referencia vale 0. En el resumen se calcula "
        "sobre los promedios, que casi nunca son 0",
        "Cuartos con referencia en cero": int((serie["cmg_referencia"] == 0).sum()),
        "Datos preliminares": _avisos_preliminares(datos.dias_preliminares(conn, desde, hasta)),
        "Fuente": "Coordinador Electrico Nacional, CMg real; base cmg_cen",
        "Generado": datetime.now(),
    }
    return excel.libro_riesgo(
        ruta,
        serie,
        datos.resumen_riesgo(conn, referencia, comparada, desde, hasta),
        datos.versiones_por_dia(conn, desde, hasta),
        datos.revisiones(conn, desde, hasta),
        info,
    )
