"""Exportes a Excel, CSV y Parquet.

Decisiones que vienen de como se usan los archivos de verdad:

- **La fecha es una FECHA, no texto.** Si se exporta "2025-03" como cadena, en
  Excel no funcionan `=MES()` ni `=AÑO()`. Fue un reclamo concreto del usuario en
  el programa anterior.
- **El CSV usa `;` y coma decimal**, para que Excel en configuracion regional
  chilena lo abra en columnas con doble clic, sin el asistente de importacion.
- **Nunca se sobrescribe un archivo.** Si existe, se guarda `_v2`, `_v3`... Perder
  un exporte anterior por repetir una consulta es barato de evitar.
- **Una hoja por año** en el Excel. Un año de datos de 15 minutos son 35.040 filas;
  el limite de Excel es 1.048.576, asi que cabe, pero separar por año hace el
  archivo navegable.
"""

from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd

from cmg_ingesta.domain import periodo
from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.gold import bloques_mes, riesgo_nodal
from cmg_ingesta.silver import leer

Formato = Literal["excel", "csv", "parquet"]
FORMATOS: tuple[Formato, ...] = ("excel", "csv", "parquet")

EXTENSION: dict[Formato, str] = {"excel": ".xlsx", "csv": ".csv", "parquet": ".parquet"}

#: Limite real de filas de una hoja de Excel, menos el encabezado.
MAX_FILAS_EXCEL = 1_048_575


def nombre_unico(carpeta: Path, base: str, extension: str) -> Path:
    """Una ruta que no existe: agrega `_v2`, `_v3`... si hace falta."""
    carpeta.mkdir(parents=True, exist_ok=True)
    candidato = carpeta / f"{base}{extension}"
    version = 1
    while candidato.exists():
        version += 1
        candidato = carpeta / f"{base}_v{version}{extension}"
    return candidato


def _nombre_seguro(texto: str) -> str:
    """Un nombre de archivo sin caracteres que Windows rechaza."""
    prohibidos = '<>:"/\\|?*'
    limpio = "".join("_" if c in prohibidos else c for c in texto)
    return limpio.strip().rstrip(".")


def escribir_csv(df: pd.DataFrame, ruta: Path) -> Path:
    """CSV con `;` y coma decimal, para abrir directo en Excel chileno."""
    df.to_csv(ruta, sep=";", decimal=",", index=False, encoding="utf-8-sig")
    return ruta


def escribir_parquet(df: pd.DataFrame, ruta: Path) -> Path:
    """Parquet comprimido, para consumir desde otros programas."""
    df.to_parquet(ruta, index=False, compression="zstd")
    return ruta


def escribir_excel(hojas: dict[str, pd.DataFrame], ruta: Path) -> tuple[Path, list[str]]:
    """Un libro con una hoja por cada entrada del diccionario.

    Devuelve la ruta y los nombres de las hojas OMITIDAS por no caber en Excel.
    Se devuelven en vez de ignorarlas: quien llama tiene que poder avisar.
    """
    omitidas: list[str] = []
    with pd.ExcelWriter(ruta, engine="openpyxl") as libro:
        for nombre, df in hojas.items():
            if len(df) > MAX_FILAS_EXCEL:
                omitidas.append(nombre)
                continue
            df.to_excel(libro, sheet_name=nombre[:31], index=False)
    return ruta, omitidas


def hojas_por_año(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Parte la serie en una hoja por año, con el año fuera de las columnas."""
    salida: dict[str, pd.DataFrame] = {}
    for anio in sorted(df["anio"].unique()):
        trozo = df[df["anio"] == anio].drop(columns=["anio", "mes"])
        salida[str(anio)] = trozo.reset_index(drop=True)
    return salida


def hoja_info(datos: dict[str, object]) -> pd.DataFrame:
    """La hoja "Info": dos columnas, concepto y valor.

    Existe para que el archivo se explique solo tres meses despues, cuando nadie
    recuerde con que parametros se genero.
    """
    return pd.DataFrame({"concepto": list(datos.keys()), "valor": [str(v) for v in datos.values()]})


def exportar_cmg(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    barra: str,
    desde: Mes,
    hasta: Mes,
    carpeta: Path,
    formatos: tuple[Formato, ...] = ("excel",),
) -> list[Path]:
    """Exporta el CMg de 15 minutos de una barra, mas el resumen por bloques.

    Devuelve las rutas escritas. No imprime nada.
    """
    if not formatos:
        raise ValueError("hay que indicar al menos un formato")
    desconocidos = set(formatos) - set(FORMATOS)
    if desconocidos:
        raise ValueError(f"formato no soportado: {sorted(desconocidos)}")

    serie = leer.serie_quinceminutal(con, base, barra, desde, hasta)
    if serie.empty:
        raise ValueError(
            f"no hay datos de {barra} entre {periodo.formatear(desde)} y {periodo.formatear(hasta)}"
        )
    resumen = bloques_mes.resumen_mensual(con, base, barra, desde, hasta)

    nombre = _nombre_seguro(f"CMg_{barra}_{periodo.formatear(desde)}_a_{periodo.formatear(hasta)}")
    escritas: list[Path] = []

    if "excel" in formatos:
        info = hoja_info(
            {
                "barra": barra,
                "periodo": f"{periodo.formatear(desde)} a {periodo.formatear(hasta)}",
                "intervalos": f"{len(serie):,}",
                "meses": len(resumen),
                "cmg_promedio_usd_mwh": round(float(serie["cmg_usd_mwh"].mean()), 4),
                "cmg_minimo": round(float(serie["cmg_usd_mwh"].min()), 4),
                "cmg_maximo": round(float(serie["cmg_usd_mwh"].max()), 4),
                "intervalos_en_cero": int((serie["cmg_usd_mwh"] == 0).sum()),
                "intervalos_hora_extra_dst": int(serie["es_hora_extra"].sum()),
                "origen_de_los_datos": ", ".join(sorted(serie["origen"].unique())),
                "bloques": "A: 23-07:59 | B: 08-17:59 | C: 18-22:59",
                "solar": "promedio del bloque B",
                "no_solar": "promedio de los cuartos de A y C (no de sus promedios)",
            }
        )
        hojas = {**hojas_por_año(serie), "Bloques": resumen, "Info": info}
        ruta, omitidas = escribir_excel(hojas, nombre_unico(carpeta, nombre, ".xlsx"))
        if omitidas:
            # no se pierde el dato: queda en el CSV o el parquet
            info.loc[len(info)] = ["hojas_omitidas_por_tamaño", ", ".join(omitidas)]
        escritas.append(ruta)

    if "csv" in formatos:
        escritas.append(escribir_csv(serie, nombre_unico(carpeta, nombre, ".csv")))
        escritas.append(escribir_csv(resumen, nombre_unico(carpeta, nombre + "_bloques", ".csv")))

    if "parquet" in formatos:
        escritas.append(escribir_parquet(serie, nombre_unico(carpeta, nombre, ".parquet")))

    return escritas


def exportar_riesgo(
    con: duckdb.DuckDBPyConnection,
    base: Path,
    referencia: str,
    comparada: str,
    desde: Mes,
    hasta: Mes,
    carpeta: Path,
    formatos: tuple[Formato, ...] = ("excel",),
) -> list[Path]:
    """Exporta el riesgo nodal entre dos barras: la serie de 15 minutos y el resumen.

    riesgo = CMg(comparada) - CMg(referencia); positivo = la comparada es mas cara.
    Devuelve las rutas escritas. No imprime nada.
    """
    if not formatos:
        raise ValueError("hay que indicar al menos un formato")
    desconocidos = set(formatos) - set(FORMATOS)
    if desconocidos:
        raise ValueError(f"formato no soportado: {sorted(desconocidos)}")

    serie = riesgo_nodal.serie_riesgo(con, base, referencia, comparada, desde, hasta)
    if serie.empty:
        raise ValueError(
            f"no hay intervalos comunes entre {referencia} y {comparada} "
            f"de {periodo.formatear(desde)} a {periodo.formatear(hasta)}"
        )
    resumen = riesgo_nodal.resumen_riesgo(con, base, referencia, comparada, desde, hasta)
    ceros, total = riesgo_nodal.intervalos_sin_porcentaje(con, base, referencia, desde, hasta)

    rango = f"{periodo.formatear(desde)}_a_{periodo.formatear(hasta)}"
    nombre = _nombre_seguro(f"Riesgo_{referencia}_vs_{comparada}_{rango}")
    escritas: list[Path] = []

    if "excel" in formatos:
        anios = pd.to_datetime(serie["fecha"]).dt.year
        por_anio = {
            str(a): serie[anios == a].reset_index(drop=True) for a in sorted(anios.unique())
        }
        info = hoja_info(
            {
                "referencia": referencia,
                "comparada": comparada,
                "formula": "riesgo = CMg(comparada) - CMg(referencia)",
                "signo": "positivo = la comparada es mas cara",
                "periodo": f"{periodo.formatear(desde)} a {periodo.formatear(hasta)}",
                "intervalos_comunes": f"{len(serie):,}",
                "riesgo_promedio_usd_mwh": round(float(serie["riesgo_usd_mwh"].mean()), 4),
                "referencia_en_cero": f"{ceros:,} de {total:,} intervalos",
                "riesgo_pct": "en 'Resumen' se calcula sobre los promedios del mes; "
                "en las hojas por año, intervalo a intervalo (vacio si la referencia es 0)",
            }
        )
        hojas = {**por_anio, "Resumen": resumen, "Info": info}
        ruta, _ = escribir_excel(hojas, nombre_unico(carpeta, nombre, ".xlsx"))
        escritas.append(ruta)

    if "csv" in formatos:
        escritas.append(escribir_csv(serie, nombre_unico(carpeta, nombre, ".csv")))
        escritas.append(escribir_csv(resumen, nombre_unico(carpeta, nombre + "_resumen", ".csv")))

    if "parquet" in formatos:
        escritas.append(escribir_parquet(serie, nombre_unico(carpeta, nombre, ".parquet")))

    return escritas
