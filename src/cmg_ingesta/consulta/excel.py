"""Los libros de Excel que entrega la app.

- **Fechas como fechas**, no texto: en Excel funcionan `=MES()`, `=AÑO()` y los
  filtros por fecha (pedido del usuario en el programa anterior).
- **Encabezados con su unidad** ("CMg [USD/MWh]") y el origen en palabras
  ("Definitivo", "Preliminar"): el archivo lo abre gente que no conoce la base.
- **Una hoja "Info"** con barra, rango, formulas y avisos (dias preliminares):
  el archivo tiene que explicarse solo meses despues.
- Si una serie no cabe en una hoja (1.048.575 filas), se parte por año.
"""

from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

MAX_FILAS = 1_048_575

ORIGEN = {
    "pagina_cen_def": "Definitivo",
    "pagina_cen_pre": "Preliminar",
    "maestro_cmg_db": "Maestro (historico)",
}

#: Nombre interno -> encabezado en el Excel.
ENCABEZADOS = {
    "fecha": "Fecha",
    "hora": "Hora",
    "minuto": "Minuto",
    "fecha_hora": "Fecha y hora",
    "bloque": "Bloque",
    "cmg_usd_mwh": "CMg [USD/MWh]",
    "es_hora_extra": "Hora extra (abril)",
    "origen": "Origen",
    "version": "Version",
    "publicado": "Publicado",
    "cargado": "Cargado en la base",
    "periodo": "Periodo",
    "desde": "Desde",
    "hasta": "Hasta",
    "cuartos": "Cuartos de hora",
    "A": "A [USD/MWh]",
    "B": "B [USD/MWh]",
    "C": "C [USD/MWh]",
    "Solar": "Solar [USD/MWh]",
    "NoSolar": "NoSolar [USD/MWh]",
    "Total": "Total [USD/MWh]",
    "promedio": "Promedio [USD/MWh]",
    "desv_estandar": "Desv. estandar [USD/MWh]",
    "minimo": "Minimo [USD/MWh]",
    "maximo": "Maximo [USD/MWh]",
    "en_cero": "Cuartos en cero",
    "cmg_referencia": "CMg referencia [USD/MWh]",
    "cmg_comparada": "CMg comparada [USD/MWh]",
    "riesgo_usd_mwh": "Riesgo [USD/MWh]",
    "riesgo_pct": "Riesgo [%]",
    "riesgo_minimo": "Riesgo minimo [USD/MWh]",
    "riesgo_maximo": "Riesgo maximo [USD/MWh]",
    "referencia_en_cero": "Cuartos con referencia en cero",
    "antes": "Antes",
    "despues": "Despues",
    "documento": "Documento del Coordinador",
    "detectado": "Detectado",
    "cuartos_distintos": "Cuartos que cambiaron",
    "dif_max_usd": "Cambio maximo [USD/MWh]",
    "dif_prom_usd": "Cambio promedio [USD/MWh]",
}

_FECHA = "dd-mm-yyyy"
_FECHA_HORA = "dd-mm-yyyy hh:mm"
_USD = "#,##0.00"
_ENTERO = "#,##0"
_PCT = "0.00%"


def _formato(encabezado: str) -> str | None:
    if encabezado in ("Fecha", "Desde", "Hasta", "Detectado", "Publicado", "Cargado en la base"):
        return _FECHA
    if encabezado == "Fecha y hora":
        return _FECHA_HORA
    if encabezado == "Riesgo [%]":
        return _PCT
    if "[USD/MWh]" in encabezado:
        return _USD
    if encabezado.startswith("Cuartos"):
        return _ENTERO
    return None


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Encabezados legibles y el origen en palabras. No cambia los valores."""
    salida = df.copy()
    if "origen" in salida:
        salida["origen"] = salida["origen"].map(lambda o: ORIGEN.get(str(o), str(o)))
    for col in ("antes", "despues"):
        if col in salida:
            salida[col] = salida[col].map(lambda o: ORIGEN.get(str(o), "" if o is None else str(o)))
    return salida.rename(columns=ENCABEZADOS)


def _dar_formato(hoja: Worksheet) -> None:
    """Encabezado en negrita y fijo, anchos de columna y formato por columna."""
    hoja.freeze_panes = "A2"
    relleno = PatternFill("solid", fgColor="DDE6F0")
    for celda in hoja[1]:
        celda.font = Font(bold=True)
        celda.fill = relleno
    for i, celda in enumerate(hoja[1], start=1):
        encabezado = str(celda.value or "")
        letra = get_column_letter(i)
        hoja.column_dimensions[letra].width = max(10, min(40, len(encabezado) + 2))
        formato = _formato(encabezado)
        if formato:
            for (c,) in hoja.iter_rows(min_row=2, min_col=i, max_col=i):
                c.number_format = formato


def _hojas_de_serie(nombre: str, serie: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if len(serie) <= MAX_FILAS:
        return {nombre: serie}
    anios = pd.to_datetime(serie["Fecha"]).dt.year
    return {f"{nombre} {a}": serie[anios == a] for a in sorted(anios.unique())}


def hoja_info(datos: Mapping[str, object]) -> pd.DataFrame:
    def texto(v: object) -> str:
        if isinstance(v, datetime):
            return f"{v:%d-%m-%Y %H:%M}"
        if isinstance(v, date):
            return f"{v:%d-%m-%Y}"
        return str(v)

    return pd.DataFrame({"Concepto": list(datos), "Valor": [texto(v) for v in datos.values()]})


def escribir(ruta: Path, hojas: Mapping[str, pd.DataFrame]) -> Path:
    """Escribe el libro y le da formato. Las hojas vacias no se escriben."""
    with pd.ExcelWriter(ruta, engine="openpyxl") as libro:
        for nombre, df in hojas.items():
            if df.empty:
                continue
            df.to_excel(libro, sheet_name=nombre[:31], index=False)
            _dar_formato(libro.sheets[nombre[:31]])
    return ruta


def libro_cmg(
    ruta: Path,
    serie: pd.DataFrame,
    bloques: pd.DataFrame,
    estadisticas: pd.DataFrame,
    versiones: pd.DataFrame,
    revisiones: pd.DataFrame,
    info: Mapping[str, object],
) -> Path:
    """CMg de una barra: la serie de 15 minutos, los bloques por mes y la Info."""
    hojas = {
        **_hojas_de_serie("CMg 15 min", preparar(serie)),
        "Bloques por mes": preparar(bloques),
        "Estadisticas por bloque": preparar(estadisticas),
        "Version por dia": preparar(versiones),
        "Revisiones": preparar(revisiones),
        "Info": hoja_info(info),
    }
    return escribir(ruta, hojas)


def libro_riesgo(
    ruta: Path,
    serie: pd.DataFrame,
    resumen: pd.DataFrame,
    versiones: pd.DataFrame,
    revisiones: pd.DataFrame,
    info: Mapping[str, object],
) -> Path:
    """Riesgo nodal: la serie de 15 minutos, el resumen por mes y bloque, y la Info."""
    hojas = {
        **_hojas_de_serie("Riesgo 15 min", preparar(serie)),
        "Resumen por mes y bloque": preparar(resumen),
        "Version por dia": preparar(versiones),
        "Revisiones": preparar(revisiones),
        "Info": hoja_info(info),
    }
    return escribir(ruta, hojas)
