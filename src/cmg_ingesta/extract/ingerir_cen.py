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

4. **Testigo en lugar del ZIP (opcional, `conservar_zip=False`).** Para equipos
   con poco disco: cuando un dia ya quedo en Silver, su ZIP (~11 MB) se borra y
   queda un testigo (`<nombre>.zip.testigo.json`, unos 300 bytes) con su sha256.
   Si despues hay que reescribir ese mes (llego el def de otro dia), los dias con
   testigo se toman del Silver que ya estaba, no del ZIP. Lo que se pierde: no se
   puede volver a leer ESE ZIP con otras reglas (un `--forzar` los toma de Silver
   tal cual). Los ZIP con problemas (dia omitido, sin clasificar) nunca se borran.

Nada se imprime: el progreso va por el callback `avisar` y el resultado se
devuelve como datos.
"""

import json
import tempfile
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import TypedDict

import duckdb
import pandas as pd

from cmg_ingesta.domain import esquema
from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import pagina_cen
from cmg_ingesta.quality import checks, deriva
from cmg_ingesta.silver import escribir

Avisar = Callable[[str], None]

#: Huella por mes de lo ya ingerido. Vive junto al manifiesto, en el Bronze: es
#: informacion sobre los archivos crudos, no un dato de Silver.
ESTADO = "ingesta_silver.json"

#: Lo que queda en lugar de un ZIP ya pasado a Silver: `<nombre>.zip.testigo.json`.
SUFIJO_TESTIGO = ".testigo.json"


class FilaReporteCen(TypedDict):
    """Una linea del reporte de ingesta, por mes."""

    anio: int
    mes: int
    dias: int  # dias que entraron
    dias_pre: int  # de esos, cuantos son preliminares
    desde_silver: int  # de esos, cuantos se tomaron del Silver anterior (su ZIP ya no esta)
    omitidos: list[str]  # dias que NO entraron, y por que
    filas: int
    hora_fantasma: int  # filas descartadas por ser de la hora que no existio
    dias_mal: int
    horas_mal: int
    duplicados: int
    nulos: int
    detalle_largo: pd.DataFrame


class Testigo(TypedDict):
    """Lo que se sabe de un ZIP que se borro despues de pasarlo a Silver."""

    nombre: str
    sha256: str
    bytes: int
    fecha_operacion: str
    tipo: str
    uso: str  # "silver": sus datos estan en Silver | "superado": habia una version mas nueva
    procesado_en: str


def _nada(_mensaje: str) -> None:
    """Callback por defecto: no avisa nada."""


# ---------------------------------------------------------------- testigos


def ruta_testigo(bronze: Path, nombre: str) -> Path:
    return bronze / (nombre + SUFIJO_TESTIGO)


def leer_testigo(bronze: Path, nombre: str) -> Testigo | None:
    """El testigo de un ZIP, o None si no hay (o esta ilegible: entonces no cuenta)."""
    ruta = ruta_testigo(bronze, nombre)
    if not ruta.exists():
        return None
    try:
        datos: Testigo = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return datos


def dejar_testigo(bronze: Path, entrada: cen.EntradaManifiesto, uso: str) -> bool:
    """Reemplaza el ZIP por su testigo. Devuelve True si borro el ZIP.

    Primero se escribe el testigo (de forma atomica) y recien despues se borra el
    ZIP: si se corta entre medio, queda el ZIP y un testigo de mas, nunca nada.
    Si el ZIP del disco no es el del manifiesto (otro sha256), NO se borra.
    """
    zip_ = bronze / entrada["nombre"]
    if not zip_.exists():
        return False
    if cen.sha256_bytes(zip_.read_bytes()) != entrada["sha256"]:
        return False
    testigo: Testigo = {
        "nombre": entrada["nombre"],
        "sha256": entrada["sha256"],
        "bytes": entrada["bytes"],
        "fecha_operacion": entrada["fecha_operacion"],
        "tipo": entrada["tipo"],
        "uso": uso,
        "procesado_en": datetime.now().isoformat(timespec="seconds"),
    }
    ruta = ruta_testigo(bronze, entrada["nombre"])
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(testigo, indent=1), encoding="utf-8")
    cen.reemplazar_atomico(tmp, ruta)
    # Windows no deja borrar un archivo que otro proceso tiene abierto (el antivirus
    # revisando el ZIP recien leido, por ejemplo): se reintenta un rato y, si sigue
    # trabado, el ZIP se queda. No es un error: la proxima vez se lee del ZIP.
    for intento in range(10):
        try:
            zip_.unlink()
            return True
        except PermissionError:
            time.sleep(0.3 * (intento + 1))
    return False


def _en_silver(bronze: Path, entrada: cen.EntradaManifiesto) -> bool:
    """True si el ZIP ya no esta pero su testigo dice que sus datos quedaron en Silver."""
    t = leer_testigo(bronze, entrada["nombre"])
    return t is not None and t["uso"] == "silver" and t["sha256"] == entrada["sha256"]


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
    cen.reemplazar_atomico(tmp, ruta)


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


#: Cambia cuando cambia COMO se leen los ZIP (columnas, nombres). Va en la huella:
#: asi, al corregir una regla, todos los meses se vuelven a ingerir una vez, solos.
#: Historial: "2026-10-14" = columnas v2, subcarpetas y _NUEVO (error A19).
#: "2026-10-15" = se conserva la columna USD (tipo de cambio) en `usd_clp`.
REGLAS = "2026-10-15"


def huella(dias: list[tuple[date, cen.EntradaManifiesto]]) -> list[str]:
    """Lo que identifica el contenido de un mes: las reglas y el sha256 de cada ZIP.

    Silver es una funcion del Bronze Y de las reglas de lectura: si cambia
    cualquiera de los dos, el mes se reescribe.
    """
    return [f"reglas:{REGLAS}", *sorted(e["sha256"] for _, e in dias)]


# --------------------------------------------------------------- orquestar


def ingerir_pagina(
    con: duckdb.DuckDBPyConnection,
    bronze: Path,
    destino: Path,
    staging: Path,
    avisar: Avisar = _nada,
    forzar: bool = False,
    conservar_zip: bool = True,
    meses: list[Mes] | None = None,
) -> tuple[list[FilaReporteCen], list[deriva.Hallazgo]]:
    """Ingiere a Silver los meses desde el inicio de la fuente (2024-08) con ZIP en `bronze`.

    `staging` es donde se extraen temporalmente los CSV (se borran al terminar
    cada mes). Con `forzar=True` se reescriben todos los meses aunque su huella
    no haya cambiado. Con `conservar_zip=False`, cada ZIP que ya quedo en Silver
    (o que fue superado por una version mas nueva) se reemplaza por su testigo.
    Con `meses`, solo esos meses, y se reescriben aunque su huella no haya cambiado
    (para rehacer uno puntual sin tocar el resto).

    Devuelve una fila de reporte por mes procesado y los hallazgos de deriva.
    """
    manifiesto = cen.leer_manifiesto(bronze)
    elegidos = pagina_cen.elegir_por_dia(manifiesto)
    dudosos_por_mes: dict[Mes, list[str]] = {}
    for dia, nombres in sorted(pagina_cen.dias_dudosos(manifiesto).items()):
        if dia >= pagina_cen.PRIMER_DIA:
            dudosos_por_mes.setdefault((dia.year, dia.month), []).append(
                f"{dia}: archivo sin clasificar ({', '.join(sorted(nombres))}); revisar a mano"
            )
    if not elegidos and not dudosos_por_mes:
        raise ValueError(
            f"no hay archivos desde {pagina_cen.PRIMER_DIA} en {bronze}. "
            "Corre primero:  cmg descargar-cen --desde AAAA-MM-DD"
        )

    huerfanas = escribir.limpiar_temporales(destino)
    if huerfanas:
        avisar(f"se limpiaron {huerfanas} carpeta(s) temporal(es) de una corrida previa")

    if elegidos:
        dias_todos = list(elegidos)
        checks.crear_tabla_calendario(con, dias_todos[0], dias_todos[-1])

    estado = leer_estado(bronze)
    reporte: list[FilaReporteCen] = []
    hallazgos: list[deriva.Hallazgo] = []
    staging.mkdir(parents=True, exist_ok=True)

    por_mes_elegidos = por_mes(elegidos)
    for mes in sorted(set(por_mes_elegidos) | set(dudosos_por_mes)):
        if meses is not None and mes not in meses:
            continue
        dias = por_mes_elegidos.get(mes, [])
        dudosos = dudosos_por_mes.get(mes, [])
        firma = huella(dias)
        forzado = forzar or meses is not None
        if not forzado and not dudosos and estado.get(_clave(mes)) == firma:
            avisar(f"{_clave(mes)}: sin cambios, se omite")
            continue

        entraron: list[tuple[date, cen.EntradaManifiesto]] = []
        if dias:
            avisar(f"{_clave(mes)}: {len(dias)} dia(s) por leer")
            with tempfile.TemporaryDirectory(dir=staging) as tmp:
                fila, h, entraron = _ingerir_mes(con, bronze, destino, Path(tmp), mes, dias, avisar)
            hallazgos += h
        else:  # solo dias dudosos: no se escribe nada, se reporta
            fila = _fila(mes, 0, 0, [], 0, 0, None)
        if not conservar_zip and entraron:
            avisar(f"{_clave(mes)}: reemplazando los ZIP por su testigo...")
            liberados = _dejar_testigos(bronze, manifiesto, entraron)
            if liberados:
                avisar(f"{_clave(mes)}: {liberados} ZIP reemplazado(s) por su testigo")
        # un dia dudoso no entra y deja el mes sin huella: se reintenta la proxima vez
        fila["omitidos"] = dudosos + fila["omitidos"]
        reporte.append(fila)

        # sin omitidos se guarda la huella; con omitidos, se reintenta la proxima vez
        if not fila["omitidos"]:
            estado[_clave(mes)] = firma
            guardar_estado(bronze, estado)

        alertas = ""
        if fila["dias_pre"]:
            alertas += f"  ({fila['dias_pre']} dia(s) preliminar)"
        if fila["desde_silver"]:
            alertas += f"  ({fila['desde_silver']} dia(s) tomados del Silver anterior)"
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
    avisar: Avisar = _nada,
) -> tuple[FilaReporteCen, list[deriva.Hallazgo], list[tuple[date, cen.EntradaManifiesto]]]:
    """Valida, mapea y escribe UN mes. Separado para que el bucle se lea solo.

    Devuelve tambien los dias que quedaron en Silver (con su archivo), para que
    se puedan reemplazar por su testigo.
    """
    hallazgos: list[deriva.Hallazgo] = []
    consultas: list[str] = []
    crudas: list[str] = []
    omitidos: list[str] = []
    entraron: list[tuple[date, cen.EntradaManifiesto]] = []
    de_silver: list[tuple[date, cen.EntradaManifiesto]] = []
    dias_pre = 0

    for i, (dia, entrada) in enumerate(dias, start=1):
        marca = f"  [{i}/{len(dias)}] {dia} {entrada['tipo']}"
        ruta = bronze / entrada["nombre"]
        if not ruta.exists():
            if _en_silver(bronze, entrada):
                de_silver.append((dia, entrada))
                avisar(f"{marca}  (ya en Silver, tiene testigo)")
            else:
                omitidos.append(
                    f"{dia}: {entrada['nombre']} esta en el manifiesto pero no en disco"
                )
                avisar(f"{marca}  [!] no esta en disco")
            continue
        h = deriva.revisar_zip(ruta, dia)
        hallazgos += h
        criticos = [x["tipo"] for x in h if x["severidad"] == "critico"]
        if criticos:
            omitidos.append(f"{dia}: {entrada['nombre']}: hallazgo critico ({', '.join(criticos)})")
            avisar(f"{marca}  [!] hallazgo critico, no entra")
            continue
        avisar(marca)
        csv = pagina_cen.extraer_comparativo(ruta, tmp)
        consultas.append(pagina_cen.sql_un_dia(csv, dia, entrada["tipo"]))
        crudas.append(pagina_cen.sql_crudo_un_dia(csv, entrada["tipo"]))
        dias_pre += entrada["tipo"] == "pre"
        entraron.append((dia, entrada))

    # los dias cuyo ZIP se reemplazo por su testigo: se copian del Silver anterior
    # ANTES de reescribir el mes (_entrante es una tabla, se lee una sola vez)
    vigentes = _dias_en_silver(con, destino, mes, de_silver)
    trae: set[str] = set()
    if vigentes:
        archivo = _lit(str(_archivo_silver(destino, mes)))
        trae = {
            str(f[0])
            for f in con.execute(f"DESCRIBE SELECT * FROM read_parquet({archivo})").fetchall()
        }
    for dia, entrada in de_silver:
        if dia not in vigentes:
            omitidos.append(
                f"{dia}: su ZIP se borro (hay testigo) pero Silver no tiene ese dia con ese "
                "origen; hay que volver a descargarlo"
            )
            continue
        consultas.append(_sql_dia_de_silver(destino, mes, dia, trae))
        dias_pre += entrada["tipo"] == "pre"
        entraron.append((dia, entrada))

    if not consultas:
        # no se escribe nada: un mes vacio borraria lo que hubiera antes
        return _fila(mes, 0, 0, omitidos, 0, 0, None), hallazgos, []

    # se cuenta la hora fantasma en el CRUDO; en lo que entra ya no debe estar
    # (lo que viene del Silver anterior ya se conto la vez que entro)
    filas_fantasma = 0
    if crudas:
        con.execute(f"CREATE OR REPLACE TEMP VIEW _crudo AS {pagina_cen.unir(crudas)}")
        fantasma = checks.hora_fantasma_presente(con, "_crudo")
        filas_fantasma = int(fantasma["filas"].sum()) if len(fantasma) else 0

    # TABLA y no vista: una vista se recalcula en cada validacion, y eso es leer
    # y parsear los ~30 CSV del mes seis veces. Materializado, se leen una.
    con.execute(f"CREATE OR REPLACE TEMP TABLE _entrante AS {pagina_cen.unir(consultas)}")
    res = checks.resumen(con, "_entrante")

    avisar(f"{_clave(mes)}: escribiendo Silver...")
    escritas = escribir.escribir_particion(con, "SELECT * FROM _entrante", destino, *mes)
    con.execute("DROP TABLE _entrante")
    fila = _fila(mes, len(consultas), dias_pre, omitidos, escritas, filas_fantasma, res)
    fila["desde_silver"] = len(entraron) - len(crudas)
    return fila, hallazgos, entraron


def _archivo_silver(destino: Path, mes: Mes) -> Path:
    return escribir.ruta_particion(destino, *mes) / escribir.NOMBRE_ARCHIVO


def _lit(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


def _dias_en_silver(
    con: duckdb.DuckDBPyConnection,
    destino: Path,
    mes: Mes,
    dias: list[tuple[date, cen.EntradaManifiesto]],
) -> set[date]:
    """De `dias`, los que el Silver actual tiene con el origen de su archivo.

    El testigo dice "esto quedo en Silver"; esto lo comprueba en Silver mismo
    antes de copiarlo, para no reescribir un mes con un dia que falta en silencio.
    """
    archivo = _archivo_silver(destino, mes)
    if not dias or not archivo.exists():
        return set()
    esperado = {dia: pagina_cen.ORIGEN_POR_TIPO[e["tipo"]] for dia, e in dias}
    fechas = ", ".join(f"DATE '{d}'" for d in esperado)
    filas = con.execute(
        f"SELECT fecha, min(origen), max(origen) FROM read_parquet({_lit(str(archivo))}) "
        f"WHERE fecha IN ({fechas}) GROUP BY fecha"
    ).fetchall()
    return {f for f, o1, o2 in filas if o1 == o2 == esperado[f]}


def _sql_dia_de_silver(destino: Path, mes: Mes, dia: date, trae: set[str]) -> str:
    """El SELECT de un dia tal como esta en Silver: mismas columnas, mismo orden.

    `trae` = las columnas del archivo. Uno escrito antes de agregar `usd_clp` no la
    tiene: va como NULL (con testigo, el ZIP ya no esta para volver a leerla).
    """
    columnas = ", ".join(
        c if c in trae else f"CAST(NULL AS {tipo}) AS {c}"
        for c, tipo in {**esquema.COLUMNAS, **esquema.COLUMNAS_LINAJE}.items()
    )
    archivo = _lit(str(_archivo_silver(destino, mes)))
    return f"SELECT {columnas} FROM read_parquet({archivo}) WHERE fecha = DATE '{dia}'"


def _dejar_testigos(
    bronze: Path,
    manifiesto: dict[str, cen.EntradaManifiesto],
    entraron: list[tuple[date, cen.EntradaManifiesto]],
) -> int:
    """Reemplaza por su testigo los ZIP de los dias que quedaron en Silver.

    Ademas los de versiones superadas de esos MISMOS dias (el pre cuando ya
    entro el def): nunca mas se van a leer. Los dias omitidos o sin clasificar
    no se tocan: su ZIP hace falta para reintentar o revisar a mano.
    """
    usados = {e["nombre"] for _, e in entraron}
    dias = {d for d, _ in entraron}
    dudosos = set(pagina_cen.dias_dudosos(manifiesto))
    liberados = 0
    for e in manifiesto.values():
        dia = date.fromisoformat(e["fecha_operacion"])
        if dia not in dias or dia in dudosos:
            continue
        uso = "silver" if e["nombre"] in usados else "superado"
        liberados += dejar_testigo(bronze, e, uso)
    return liberados


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
        "desde_silver": 0,
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
