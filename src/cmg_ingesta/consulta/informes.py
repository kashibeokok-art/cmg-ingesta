"""Lo que hace cada boton de la app: validar, consultar y escribir el Excel.

Separado de la ventana para poder probarlo sin abrir ninguna: la ventana solo
junta lo que eligio el usuario y llama a estas funciones. Los errores que el
usuario puede corregir (fechas al reves, una barra sin datos) son `ValueError`
con un mensaje en castellano, listo para mostrar.
"""

import re
from collections.abc import Callable
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


def _miles(n: int) -> str:
    """1234567 -> '1.234.567', como se escribe en Chile."""
    return f"{n:,}".replace(",", ".")


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
        "Tipo de cambio": _TIPO_CAMBIO,
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


#: Tope de celdas de la hoja ancha en Excel. Mas, y el archivo tarda minutos en
#: escribirse y en abrir: ~50 barras x 1 año. Para todas las barras esta el CSV.
MAX_CELDAS_EXCEL = 2_000_000

_TIPO_CAMBIO = (
    "CLP por dolar, uno por dia: la columna USD de los archivos del Coordinador. "
    "Vacio antes del 01-08-2024 (el historico del Maestro no lo trae)"
)

_INFO_COMUN = {
    "Tipo de cambio": _TIPO_CAMBIO,
    "Bloques": "A: 23:00 a 07:59 | B: 08:00 a 17:59 | C: 18:00 a 22:59",
    "Solar": "promedio del bloque B",
    "NoSolar": "promedio de los cuartos de A y C juntos (no de sus promedios)",
    "Hora extra (abril)": "el dia de 25 horas trae la segunda 23:00 como hora 24",
    "Celda vacia": "esa barra no tiene dato ese cuarto (por ejemplo, aun no existia). No es cero",
    "Fuente": "Coordinador Electrico Nacional, CMg real; base cmg_cen",
}


def cmg_varias(conn: Conexion, barras: list[str], desde: date, hasta: date, ruta: Path) -> Path:
    """Excel de varias barras: el CMg cada 15 minutos con una columna por barra, y los bloques."""
    if len(barras) < 2:
        raise ValueError("Elige al menos dos barras (para una sola, se usa el Excel de una barra).")
    validar_rango(desde, hasta, datos.rango_disponible(conn))
    cuartos = ((hasta - desde).days + 1) * 96
    if cuartos * len(barras) > MAX_CELDAS_EXCEL:
        raise ValueError(
            f"{len(barras)} barras en {(hasta - desde).days + 1} dias son demasiados datos para "
            "un Excel. Elige menos barras o un rango mas corto, o usa 'Todas las barras', que "
            "entrega el dato cada 15 minutos en CSV."
        )
    ancha = datos.serie_ancha(conn, barras, desde, hasta, sorted(barras))
    if ancha.empty:
        raise ValueError(
            f"Ninguna de las barras tiene datos entre el {desde:%d-%m-%Y} y el {hasta:%d-%m-%Y}."
        )
    sin_datos = [b for b in barras if ancha[b].isna().all()]
    info = {
        "Barras": ", ".join(sorted(barras)),
        "Desde": desde,
        "Hasta": hasta,
        "Cuartos de hora": _miles(len(ancha)),
        "Barras sin datos en el rango": ", ".join(sin_datos) or "ninguna",
        "Datos preliminares": _avisos_preliminares(datos.dias_preliminares(conn, desde, hasta)),
        "Formato": "hoja 'CMg 15 min': una fila por cuarto de hora y una columna por barra, "
        "en USD/MWh",
        **_INFO_COMUN,
        "Generado": datetime.now(),
    }
    return excel.libro_varias(
        ruta,
        ancha,
        sorted(barras),
        datos.bloques_varias(conn, barras, desde, hasta),
        datos.versiones_por_dia(conn, desde, hasta),
        datos.revisiones(conn, desde, hasta),
        info,
    )


def cmg_todas(
    conn: Conexion,
    desde: date,
    hasta: date,
    ruta_csv: Path,
    avisar: Callable[[str], None] = lambda _texto: None,
) -> tuple[Path, Path]:
    """Todas las barras: CSV ancho (una columna por barra) + Excel con los bloques por mes.

    El CSV se escribe mes a mes, para no tener todo en memoria: un año de todas las
    barras son ~54 millones de valores. Devuelve (ruta del CSV, ruta del Excel).
    """
    validar_rango(desde, hasta, datos.rango_disponible(conn))
    columnas = datos.barras_con_datos(conn, desde, hasta)
    if not columnas:
        raise ValueError(f"No hay datos entre el {desde:%d-%m-%Y} y el {hasta:%d-%m-%Y}.")
    tramos = datos.meses(desde, hasta)
    filas = 0
    for i, (d1, d2) in enumerate(tramos, start=1):
        avisar(f"Bajando {d1:%m-%Y} ({i} de {len(tramos)}), {len(columnas)} barras...")
        ancha = datos.serie_ancha(conn, None, d1, d2, columnas)
        excel.agregar_csv_ancho(ruta_csv, ancha, con_encabezado=i == 1)
        filas += len(ancha)
    avisar("Calculando los bloques por mes de todas las barras...")
    ruta_excel = ruta_csv.with_name(ruta_csv.stem + "_bloques.xlsx")
    info = {
        "Barras": f"todas ({len(columnas)})",
        "Desde": desde,
        "Hasta": hasta,
        "CMg cada 15 minutos": f"en {ruta_csv.name}: una fila por cuarto ({_miles(filas)} "
        "filas) y una columna por barra, en USD/MWh; separador ';' y coma decimal",
        "Datos preliminares": _avisos_preliminares(datos.dias_preliminares(conn, desde, hasta)),
        **_INFO_COMUN,
        "Generado": datetime.now(),
    }
    excel.libro_bloques_todas(
        ruta_excel,
        datos.bloques_varias(conn, None, desde, hasta),
        datos.versiones_por_dia(conn, desde, hasta),
        datos.revisiones(conn, desde, hasta),
        info,
    )
    return ruta_csv, ruta_excel


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
        "Tipo de cambio": _TIPO_CAMBIO,
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
