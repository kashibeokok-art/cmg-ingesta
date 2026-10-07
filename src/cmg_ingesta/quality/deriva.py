"""Deteccion de cambios en la fuente del Coordinador (deriva / drift).

El sitio del Coordinador ya cambio cosas sin aviso: el slug del año en 2024, la
posicion del `_v2` en el nombre del archivo, re-subidas con sufijo `-1`. Este
modulo compara lo que publica el sitio contra un CATALOGO de lo conocido y
devuelve hallazgos.

Dos niveles, y la diferencia es deliberada:

1. **Se actualiza solo** lo que se puede LEER del sitio con certeza: el slug de
   cada año, que esta en el indice. Un año nuevo se adopta sin tocar codigo.

2. **Se detecta y se reporta, pero NO se adopta**: nombres de archivo nuevos,
   archivos nuevos dentro del ZIP, columnas nuevas o faltantes, cambios en la
   convencion de hora o en el largo del dia. Adoptar solo un formato desconocido
   podria cargar datos mal sin que nadie se entere — que es exactamente lo que
   este proyecto existe para evitar. El archivo SI se descarga (no se pierde),
   pero un humano o Claude decide como interpretarlo.

Cada hallazgo trae una `accion`: que hacer, en concreto. El reporte que escribe
`escribir_reporte` esta pensado para que lo lea una persona o se le entregue a
Claude con la instruccion "resuelve estos hallazgos".

Severidades:
    info     cambio conocido o benigno, ya se adopto o no afecta la ingesta
    aviso    algo cambio y conviene revisarlo, pero la ingesta sigue siendo valida
    critico  la ingesta de ese archivo NO es confiable hasta resolverlo
"""

import csv
import io
import json
import re
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Literal, TypedDict

from bs4 import BeautifulSoup

from cmg_ingesta.domain import calendario
from cmg_ingesta.extract import coordinador_cmg as cen

Severidad = Literal["info", "aviso", "critico"]


class Hallazgo(TypedDict):
    """Una diferencia entre lo que publica el sitio y lo que el programa conoce."""

    severidad: Severidad
    tipo: str
    detalle: str
    evidencia: str
    accion: str


def _h(severidad: Severidad, tipo: str, detalle: str, evidencia: str, accion: str) -> Hallazgo:
    return {
        "severidad": severidad,
        "tipo": tipo,
        "detalle": detalle,
        "evidencia": evidencia,
        "accion": accion,
    }


# =================================================================== catalogo
#
# Lo CONOCIDO. Verificado el 2026-10-06 contra 6 ZIP reales (3 pre, 3 def) y las
# paginas de indice, mes y dia. Si el sitio cambia, se actualiza AQUI y se agrega
# un test con el caso nuevo.

#: (nombre logico, patron, obligatorio). Las fechas del nombre son AAAAMMDD.
MIEMBROS_ZIP: list[tuple[str, re.Pattern[str], bool]] = [
    ("comparativo", re.compile(r"^CmgBarrasComparativo_\d{8}_\d{8}_15\.csv$"), True),
    ("promedios", re.compile(r"^PromediosBarras_\d{8}_\d{8}_[RP]\.csv$"), True),
    ("minuto", re.compile(r"^CMgBarrasMinuto_\d{8}_\d{8}\.zip$"), False),
    ("fpen", re.compile(r"^Fpen_\d{8}\.xlsx$"), False),
    ("fpen_mapeo", re.compile(r"^FPen_Mapeo_Barras\.xlsx$"), False),
    ("subsistemas", re.compile(r"^Barras_Subsistemas_\d{8}\.xlsx$"), False),
]

#: Igual en `pre` y en `def`.
ENCABEZADO_COMPARATIVO: tuple[str, ...] = (
    "FECHA",
    "HORA",
    "MINUTO",
    "BARRA",
    "INFO_BARRA_ID",
    "INFO_BARRA_NOMBRE",
    "CMG_EN_LINEA[USD/MWh]",
    "CMG_REAL_PRE[USD/MWh]",
    "CMG_REAL_DEF[USD/MWh]",
    "USD",
    "MODIFICADO",
)
#: Sin estas la ingesta no puede funcionar.
REQUERIDAS_COMPARATIVO = frozenset(
    {"FECHA", "HORA", "MINUTO", "BARRA", "CMG_REAL_PRE[USD/MWh]", "CMG_REAL_DEF[USD/MWh]"}
)

#: Igual en `_P` (preliminar) y `_R` (definitivo).
ENCABEZADO_PROMEDIOS: tuple[str, ...] = (
    "BARRA",
    "BARRA INFO ID",
    "BARRA INFO NOMBRE",
    "FECHA",
    "HORA",
    "MINUTO",
    "CMg[CLP/KWh]",
    "CMg[USD/MWh]",
    "INTERVALO",
    "REPORTE",
)
REQUERIDAS_PROMEDIOS = frozenset({"BARRA", "FECHA", "HORA", "MINUTO", "CMg[USD/MWh]"})


# ===================================================================== slugs


def revisar_slugs(sitio: cen.Slugs, conocidos: cen.Slugs) -> list[Hallazgo]:
    """Compara los slugs que publica el indice contra los que el programa conocia."""
    if not sitio:
        return [
            _h(
                "critico",
                "indice_sin_años",
                "El indice no lista ningun año reconocible.",
                cen.INDICE,
                "Abrir el indice y revisar como enlaza los años. Si cambio el slug, "
                "ajustar RE_SLUG_ANIO en extract/coordinador_cmg.py y su test.",
            )
        ]
    hallazgos: list[Hallazgo] = []
    for anio in sorted(sitio):
        if anio not in conocidos:
            hallazgos.append(
                _h(
                    "info",
                    "año_nuevo",
                    f"El sitio publica el año {anio}; se adopto automaticamente.",
                    f"{anio}{sitio[anio]}",
                    "Nada. Queda en slugs.json. Opcional: agregarlo a SUFIJO como respaldo.",
                )
            )
        elif conocidos[anio] != sitio[anio]:
            hallazgos.append(
                _h(
                    "aviso",
                    "slug_cambiado",
                    f"El slug de {anio} cambio; se adopto el nuevo.",
                    f"antes {anio}{conocidos[anio]} -> ahora {anio}{sitio[anio]}",
                    "Verificar que los dias ya descargados de ese año sigan resolviendo, "
                    "y actualizar SUFIJO.",
                )
            )
    for anio in sorted(set(conocidos) - set(sitio)):
        hallazgos.append(
            _h(
                "aviso",
                "año_ya_no_listado",
                f"El año {anio} ya no aparece en el indice.",
                f"{anio}{conocidos[anio]}",
                "Revisar si el sitio lo movio de seccion. Los ZIP ya bajados no se pierden.",
            )
        )
    return hallazgos


def actualizar_slugs(
    sesion: cen.Sesion,
    carpeta: Path,
    anios_necesarios: Iterable[int] = (),
    forzar: bool = False,
    timeout: float = 30.0,
) -> tuple[cen.Slugs, list[Hallazgo]]:
    """Los slugs vigentes, consultando el indice solo si hace falta.

    Se consulta si se pide un año que no se conoce, o si `forzar`. Lo leido del
    sitio GANA sobre `SUFIJO` y sobre lo guardado: es la fuente de verdad.
    """
    guardados = cen.leer_slugs(carpeta)
    conocidos = {**cen.SUFIJO, **guardados}
    faltan = [a for a in anios_necesarios if a not in conocidos]
    if not (forzar or faltan):
        return conocidos, []

    sitio = cen.descubrir_slugs(sesion, timeout=timeout)
    hallazgos = revisar_slugs(sitio, conocidos)
    if sitio:
        cen.guardar_slugs(carpeta, {**guardados, **sitio})
    return {**conocidos, **sitio}, hallazgos


# ============================================================== pagina de dia


def _aammdd(d: date) -> str:
    return d.strftime("%y%m%d")


def revisar_pagina_dia(dia: date, html: str, docs: list[cen.Documento]) -> list[Hallazgo]:
    """Busca en la pagina de un dia cosas que el parser no conoce."""
    hallazgos: list[Hallazgo] = []
    url = cen.url_dia(dia) if dia.year in cen.SUFIJO else f"(dia {dia})"

    soup = BeautifulSoup(html, "html.parser")
    zips_en_pagina = {
        str(a["href"])
        for a in soup.find_all("a", href=True)
        if "/wp-content/uploads/" in str(a["href"]) and str(a["href"]).lower().endswith(".zip")
    }
    urls_cmg = {d["url"] for d in docs}

    if zips_en_pagina and not docs:
        hallazgos.append(
            _h(
                "critico",
                "pagina_con_zip_sin_documentos",
                f"La pagina del {dia} tiene ZIP, pero el parser no reconocio ninguno.",
                "; ".join(sorted(zips_en_pagina))[:500],
                "El producto pudo cambiar de nombre (el filtro exige 'CMG_Real'). "
                "Revisar parsear_documentos en extract/coordinador_cmg.py.",
            )
        )

    for otro in sorted(zips_en_pagina - urls_cmg):
        if not docs:
            break  # ya cubierto arriba
        hallazgos.append(
            _h(
                "info",
                "archivo_no_cmg_en_pagina",
                f"La pagina del {dia} publica un ZIP que no es CMg Real.",
                otro,
                "Revisar si es un producto nuevo que interese. No se descarga.",
            )
        )

    for d in docs:
        if d["tipo"] == "desconocido":
            parece = "def" if "_def" in d["nombre"] else "pre" if "_pre" in d["nombre"] else "?"
            hallazgos.append(
                _h(
                    "aviso",
                    "nombre_no_catalogado",
                    f"Nombre de archivo que no calza con ningun patron conocido "
                    f"(parece '{parece}'). Se descargo igual, pero queda ultimo en "
                    "mejor_version().",
                    d["nombre"],
                    "Agregar la variante a RE_NOMBRE en extract/coordinador_cmg.py y un "
                    "caso a test_tipo_version_y_reemision_del_nombre.",
                )
            )
        if d["fecha_publicacion"] is None:
            hallazgos.append(
                _h(
                    "aviso",
                    "fecha_publicacion_no_encontrada",
                    f"No se encontro la fecha de publicacion de {d['nombre']}. Suele "
                    "significar que cambio el markup del bloque.",
                    url,
                    "Revisar RE_PUB y el find_parent('div') de parsear_documentos. "
                    "Recapturar el fixture y compararlo con el guardado.",
                )
            )
        m = cen.RE_NOMBRE.search(d["nombre"])
        if m and m.group("aammdd") != _aammdd(dia):
            hallazgos.append(
                _h(
                    "aviso",
                    "fecha_del_nombre_distinta",
                    f"La pagina del {dia} enlaza un archivo de otra fecha.",
                    f"{d['nombre']} (esperado ...{_aammdd(dia)}...)",
                    "Revisar a que dia corresponden los datos antes de ingerirlo.",
                )
            )
    return hallazgos


# ===================================================================== ZIP


def revisar_miembros(nombres: list[str]) -> list[Hallazgo]:
    """Compara los archivos de un ZIP contra los conocidos."""
    hallazgos: list[Hallazgo] = []
    encontrados: set[str] = set()
    for nombre in nombres:
        logico = next((n for n, patron, _ in MIEMBROS_ZIP if patron.match(nombre)), None)
        if logico is None:
            hallazgos.append(
                _h(
                    "info",
                    "archivo_nuevo_en_zip",
                    "El ZIP trae un archivo que el programa no conoce.",
                    nombre,
                    "Revisar si aporta algo. Si es un reemplazo de uno conocido, "
                    "actualizar MIEMBROS_ZIP en quality/deriva.py.",
                )
            )
        else:
            encontrados.add(logico)
    for logico, patron, obligatorio in MIEMBROS_ZIP:
        if logico in encontrados:
            continue
        hallazgos.append(
            _h(
                "critico" if obligatorio else "aviso",
                "falta_archivo_requerido" if obligatorio else "falta_archivo_opcional",
                f"El ZIP no trae el archivo '{logico}'.",
                patron.pattern,
                "Si cambio de nombre, actualizar MIEMBROS_ZIP. Si desaparecio, la "
                "ingesta no puede leer este ZIP."
                if obligatorio
                else "No afecta la ingesta del CMg. Anotarlo.",
            )
        )
    return hallazgos


def revisar_encabezado(
    miembro: str, columnas: list[str], esperado: tuple[str, ...], requeridas: frozenset[str]
) -> list[Hallazgo]:
    """Compara el encabezado real de un CSV contra el conocido."""
    hallazgos: list[Hallazgo] = []
    reales = set(columnas)
    faltan = sorted(requeridas - reales)
    nuevas = sorted(reales - set(esperado))
    quitadas = sorted(set(esperado) - reales - requeridas)

    if faltan:
        hallazgos.append(
            _h(
                "critico",
                "columna_requerida_ausente",
                f"{miembro}: faltan columnas sin las que la ingesta no funciona.",
                ", ".join(faltan),
                "No ingerir este archivo. Ver si la columna cambio de nombre y actualizar "
                "el catalogo y el mapeo de M5.",
            )
        )
    if nuevas:
        hallazgos.append(
            _h(
                "aviso",
                "columnas_nuevas",
                f"{miembro}: el archivo trae columnas que el programa no conoce.",
                ", ".join(nuevas),
                "La ingesta las ignora. Revisar si aportan algo y, si se quieren, "
                "agregarlas al catalogo.",
            )
        )
    if quitadas:
        hallazgos.append(
            _h(
                "aviso",
                "columnas_eliminadas",
                f"{miembro}: desaparecieron columnas conocidas (no requeridas).",
                ", ".join(quitadas),
                "Actualizar el catalogo. No bloquea la ingesta del CMg.",
            )
        )
    if not (faltan or nuevas or quitadas) and tuple(columnas) != esperado:
        hallazgos.append(
            _h(
                "info",
                "columnas_reordenadas",
                f"{miembro}: mismas columnas, en otro orden.",
                ";".join(columnas),
                "Nada: la lectura es por nombre de columna, no por posicion.",
            )
        )
    return hallazgos


def revisar_forma_dia(dia: date, filas: Iterable[tuple[str, int, str]]) -> list[Hallazgo]:
    """Revisa fechas, convencion de hora y largo del dia. Funcion pura.

    `filas` son (FECHA, HORA, BARRA) del archivo cuarto-horario.
    """
    fechas: set[str] = set()
    horas: set[int] = set()
    por_barra: Counter[str] = Counter()
    for fecha, hora, barra in filas:
        fechas.add(fecha)
        horas.add(hora)
        por_barra[barra] += 1

    if not por_barra:
        return [
            _h(
                "critico",
                "archivo_vacio",
                f"El archivo cuarto-horario del {dia} no trae filas.",
                "",
                "No ingerir. Revisar el ZIP a mano.",
            )
        ]

    hallazgos: list[Hallazgo] = []
    esperada = dia.strftime("%Y%m%d")
    if fechas != {esperada}:
        hallazgos.append(
            _h(
                "critico",
                "fechas_inesperadas",
                f"El archivo del {dia} trae fechas distintas de ese dia.",
                ", ".join(sorted(fechas))[:300],
                "No ingerir hasta entender a que dias corresponden los datos.",
            )
        )

    hmin, hmax = min(horas), max(horas)
    # Base 1 se delata porque la hora mas alta es IGUAL al numero de horas del dia
    # (1..24 un dia normal, 1..25 el dia largo). El dia CORTO no se evalua: ahi la
    # numeracion correcta en base 0 (1..23, porque la hora 0 no existio) y una en
    # base 1 (1..23) dan el MISMO conjunto, y no hay forma de distinguirlas.
    # Aplicar la regla ese dia era una falsa alarma justo en el caso borde.
    if (
        not calendario.es_dia_corto(dia)
        and 0 not in horas
        and hmax == calendario.horas_del_dia(dia)
    ):
        hallazgos.append(
            _h(
                "critico",
                "convencion_hora_base_1",
                "La columna HORA parece estar en base 1 (empieza en 1). El programa "
                "espera base 0, y confundirlas corre TODO una hora sin error.",
                f"HORA {hmin}..{hmax}",
                "No ingerir. Agregar la conversion (HORA - 1) al mapeo de M5.",
            )
        )
    if hmax > calendario.HORA_EXTRA:
        hallazgos.append(
            _h(
                "critico",
                "hora_fuera_de_rango",
                f"HORA llega a {hmax}; el maximo valido es {calendario.HORA_EXTRA}.",
                f"HORA {hmin}..{hmax}",
                "Revisar la convencion de hora del archivo.",
            )
        )

    cuartos_tipicos, n_barras = Counter(por_barra.values()).most_common(1)[0]
    esperados = calendario.cuartos_esperados(dia)
    if calendario.es_dia_largo(dia) and cuartos_tipicos != esperados:
        hallazgos.append(
            _h(
                "critico",
                "falta_hora_extra",
                f"El {dia} tiene 25 horas y el archivo trae {cuartos_tipicos} cuartos por "
                f"barra en vez de {esperados}. Es el defecto que tenia la API: el "
                "balance mensual descuadra.",
                f"{n_barras} barras con {cuartos_tipicos} cuartos",
                "No ingerir ese dia desde este archivo. Buscar otra version (def/v2).",
            )
        )
    elif calendario.es_dia_corto(dia):
        if cuartos_tipicos == calendario.CUARTOS_NORMAL and 0 in horas:
            hallazgos.append(
                _h(
                    "info",
                    "hora_fantasma_presente",
                    f"El {dia} tiene 23 horas y el archivo trae la hora 0, que no "
                    "existio. Comportamiento conocido del CEN: la ingesta la descarta.",
                    f"{n_barras} barras con {cuartos_tipicos} cuartos",
                    "Nada.",
                )
            )
        elif cuartos_tipicos == esperados:
            hallazgos.append(
                _h(
                    "info",
                    "hora_fantasma_ausente",
                    f"El {dia} ya NO trae la hora inexistente: el CEN cambio su "
                    "comportamiento (para bien).",
                    f"{n_barras} barras con {cuartos_tipicos} cuartos",
                    "Nada: la ingesta funciona igual. Actualizar CLAUDE.md 4.5.4.",
                )
            )
        else:
            hallazgos.append(
                _h(
                    "critico",
                    "largo_de_dia_inesperado",
                    f"El {dia} (23 h) trae {cuartos_tipicos} cuartos por barra.",
                    f"esperado {esperados} o {calendario.CUARTOS_NORMAL} con hora fantasma",
                    "Revisar el archivo antes de ingerir.",
                )
            )
    elif not calendario.es_dia_largo(dia) and cuartos_tipicos != esperados:
        hallazgos.append(
            _h(
                "critico",
                "largo_de_dia_inesperado",
                f"El {dia} es un dia normal y trae {cuartos_tipicos} cuartos por barra.",
                f"esperado {esperados}",
                "Revisar el archivo antes de ingerir.",
            )
        )

    incompletas = sorted(b for b, n in por_barra.items() if n != cuartos_tipicos)
    if incompletas:
        hallazgos.append(
            _h(
                "aviso",
                "barras_con_otro_largo",
                f"{len(incompletas)} de {len(por_barra)} barras no tienen "
                f"{cuartos_tipicos} cuartos.",
                ", ".join(incompletas[:10]) + (" ..." if len(incompletas) > 10 else ""),
                "La ingesta las va a reportar en la validacion de horas. Revisar si "
                "son barras nuevas o retiradas a mitad de dia.",
            )
        )
    return hallazgos


def _leer_csv_de_zip(zf: zipfile.ZipFile, miembro: str) -> Iterator[list[str]]:
    """Un lector CSV en streaming de un archivo dentro del ZIP, sin extraerlo."""
    flujo = io.TextIOWrapper(zf.open(miembro), encoding="utf-8-sig", errors="replace")
    return csv.reader(flujo, delimiter=";")


def _filas_comparativo(
    lector: Iterator[list[str]], i_fecha: int, i_hora: int, i_barra: int
) -> Iterator[tuple[str, int, str]]:
    """(FECHA, HORA, BARRA) de cada fila valida.

    Es una funcion de modulo y no una funcion definida dentro del `for` de
    `revisar_zip`, a proposito: una funcion anidada en un loop lee las variables
    del loop cuando se EJECUTA, no cuando se define (late binding, regla B023 de
    ruff). Recibiendo los indices como argumentos no hay ambiguedad posible.
    """
    minimo = max(i_fecha, i_hora, i_barra)
    for fila in lector:
        if len(fila) > minimo and fila[i_hora].strip().isdigit():
            yield (fila[i_fecha], int(fila[i_hora]), fila[i_barra])


def revisar_zip(ruta: Path, dia: date) -> list[Hallazgo]:
    """Integridad, archivos, encabezados y forma del dia de un ZIP descargado."""
    try:
        zf = zipfile.ZipFile(ruta)
    except zipfile.BadZipFile:
        return [
            _h(
                "critico",
                "zip_corrupto",
                f"{ruta.name} no es un ZIP valido.",
                str(ruta),
                "Borrarlo del manifiesto y volver a descargar.",
            )
        ]
    with zf:
        malo = zf.testzip()
        if malo is not None:
            return [
                _h(
                    "critico",
                    "zip_corrupto",
                    f"{ruta.name} tiene un archivo dañado.",
                    malo,
                    "Borrarlo del manifiesto y volver a descargar.",
                )
            ]

        nombres = zf.namelist()
        hallazgos = revisar_miembros(nombres)

        patron_comparativo = MIEMBROS_ZIP[0][1]
        patron_promedios = MIEMBROS_ZIP[1][1]
        for miembro in nombres:
            if patron_promedios.match(miembro):  # solo el encabezado
                cab: list[str] = next(_leer_csv_de_zip(zf, miembro), [])
                hallazgos += revisar_encabezado(
                    miembro, cab, ENCABEZADO_PROMEDIOS, REQUERIDAS_PROMEDIOS
                )
            elif patron_comparativo.match(miembro):  # encabezado y forma del dia
                lector = _leer_csv_de_zip(zf, miembro)
                cab = next(lector, [])
                h_cab = revisar_encabezado(
                    miembro, cab, ENCABEZADO_COMPARATIVO, REQUERIDAS_COMPARATIVO
                )
                hallazgos += h_cab
                if any(h["severidad"] == "critico" for h in h_cab):
                    continue
                filas = _filas_comparativo(
                    lector, cab.index("FECHA"), cab.index("HORA"), cab.index("BARRA")
                )
                hallazgos += revisar_forma_dia(dia, filas)
    return hallazgos


# ============================================================== completitud

#: El preliminar se publica al dia siguiente. Medido: 2026-01-15 -> 01-16,
#: 2026-04-04 -> 04-07, 2026-09-28 -> 09-29. Antes de 3 dias, que un dia no tenga
#: archivo es normal y no se avisa.
DIAS_GRACIA = 3

#: El definitivo se publica 7 a 10 dias despues del dia de operacion. Medido:
#: 2026-01-15 -> 7 d, 2026-04-04 -> 10 d, 2026-09-06 -> 9 d, 2026-09-28 -> 8 d.
#: 15 dias deja margen sobre el peor caso observado antes de avisar.
DIAS_MAX_PRE = 15


def _rangos(dias: list[date]) -> list[tuple[date, date]]:
    """Agrupa dias en rangos consecutivos: [1,2,3,7,8] -> [(1,3), (7,8)].

    Sirve para que un hueco de un mes sea UN hallazgo y no treinta.
    """
    rangos: list[tuple[date, date]] = []
    for d in sorted(dias):
        if rangos and d - rangos[-1][1] == timedelta(days=1):
            rangos[-1] = (rangos[-1][0], d)
        else:
            rangos.append((d, d))
    return rangos


def _texto_rango(inicio: date, fin: date) -> str:
    return str(inicio) if inicio == fin else f"{inicio} a {fin}"


def revisar_completitud(
    manifiesto: dict[str, cen.EntradaManifiesto],
    desde: date,
    hasta: date,
    hoy: date,
    dias_gracia: int = DIAS_GRACIA,
    dias_max_pre: int = DIAS_MAX_PRE,
) -> list[Hallazgo]:
    """Dias sin ningun archivo, y dias que siguen solo con preliminar.

    Funcion pura sobre el manifiesto: no hace peticiones. `hoy` se inyecta para
    poder testear sin depender del reloj.

    Dos hallazgos, ambos para revision manual:

    - `dia_sin_registro`: un dia del rango no tiene ningun archivo, y ya paso el
      plazo normal de publicacion del preliminar.
    - `pre_sin_definitivo`: un dia tiene preliminar pero no definitivo, y ya paso
      el plazo normal en que el CEN publica el definitivo.

    Los dias consecutivos se agrupan en un solo hallazgo.
    """
    por_dia: dict[str, set[str]] = {}
    for e in manifiesto.values():
        por_dia.setdefault(e["fecha_operacion"], set()).add(e["tipo"])

    sin_registro: list[date] = []
    solo_pre: list[date] = []
    for dia in cen.dias_entre(desde, hasta):
        edad = (hoy - dia).days
        tipos_dia = por_dia.get(dia.isoformat())
        if not tipos_dia:
            if edad >= dias_gracia:
                sin_registro.append(dia)
        elif "def" not in tipos_dia and edad > dias_max_pre:
            solo_pre.append(dia)

    hallazgos: list[Hallazgo] = []
    for inicio, fin in _rangos(sin_registro):
        n = (fin - inicio).days + 1
        hallazgos.append(
            _h(
                "aviso",
                "dia_sin_registro",
                f"{n} dia(s) sin ningun archivo descargado.",
                _texto_rango(inicio, fin),
                f"Correr `cmg descargar-cen --desde {inicio} --hasta {fin}`. Si la "
                "pagina del dia sigue sin documentos, revisar a mano en el sitio: puede "
                "ser un dia que el CEN no publico o que movio de seccion.",
            )
        )
    for inicio, fin in _rangos(solo_pre):
        n = (fin - inicio).days + 1
        edad = (hoy - inicio).days
        hallazgos.append(
            _h(
                "aviso",
                "pre_sin_definitivo",
                f"{n} dia(s) siguen solo con el preliminar; el mas antiguo lleva "
                f"{edad} dias. El CEN suele publicar el definitivo en 7 a 10 dias.",
                _texto_rango(inicio, fin),
                f"Correr `cmg descargar-cen --desde {inicio} --hasta {fin}`, que vuelve "
                "a visitar las paginas. Si sigue sin definitivo, revision manual: puede "
                "haber una discrepancia en tramite ante el Panel de Expertos.",
            )
        )
    return hallazgos


# ============================================================ orquestacion


def hay_que_revisar(hallazgos: list[Hallazgo]) -> bool:
    """True si hay algo mas que informativo."""
    return any(h["severidad"] in ("aviso", "critico") for h in hallazgos)


def sincronizar_vigilando(
    desde: date,
    hasta: date,
    carpeta: Path,
    sesion: cen.Sesion,
    pausa: float = cen.PAUSA_MINIMA,
    timeout: float = 30.0,
    hoy: date | None = None,
    avisar: Callable[[str], None] | None = None,
) -> tuple[list[cen.EntradaManifiesto], list[Hallazgo]]:
    """`sincronizar` + deteccion de cambios en cada pagina y cada ZIP.

    Los slugs se actualizan solos desde el indice si el rango pide un año que no
    se conoce: el 1 de enero no hace falta tocar codigo.

    Al final revisa la completitud del rango: dias sin archivo y dias que siguen
    solo con preliminar.

    `avisar` recibe una linea por dia revisado y por ZIP bajado: un backfill son
    cientos de dias y sin avance la pantalla parece colgada.
    """
    anios = range(desde.year, hasta.year + 1)
    slugs, hallazgos = actualizar_slugs(sesion, carpeta, anios, timeout=timeout)

    def al_leer_pagina(dia: date, html: str, docs: list[cen.Documento]) -> None:
        hallazgos.extend(revisar_pagina_dia(dia, html, docs))
        if avisar:
            avisar(f"  {dia}  {len(docs)} archivo(s) publicado(s)")

    def al_bajar(ruta: Path, entrada: cen.EntradaManifiesto) -> None:
        hallazgos.extend(revisar_zip(ruta, date.fromisoformat(entrada["fecha_operacion"])))
        if avisar:
            avisar(f"    + {entrada['nombre']}  ({entrada['bytes'] / 1024 / 1024:.1f} MB)")

    nuevos = cen.sincronizar(
        desde,
        hasta,
        carpeta,
        sesion,
        pausa=pausa,
        timeout=timeout,
        slugs=slugs,
        al_leer_pagina=al_leer_pagina,
        al_bajar=al_bajar,
    )
    hallazgos += revisar_completitud(
        cen.leer_manifiesto(carpeta), desde, hasta, hoy or date.today()
    )
    return nuevos, hallazgos


def vigilar_fuente(
    sesion: cen.Sesion,
    carpeta: Path,
    hasta: date,
    dias: int = 7,
    pausa: float = cen.PAUSA_MINIMA,
    timeout: float = 30.0,
    hoy: date | None = None,
) -> list[Hallazgo]:
    """Chequeo liviano para correr periodicamente. **No descarga ZIP.**

    1. Consulta el indice siempre (slugs nuevos o cambiados).
    2. Revisa las paginas de los ultimos `dias` dias.
    3. Re-inspecciona el ZIP mas reciente que ya este en `carpeta`.
    4. Avisa si no hubo publicaciones en todo el periodo (frescura).
    5. Revisa la completitud de TODO lo descargado: dias sin archivo y
       preliminares que llevan demasiado sin definitivo.
    """
    if pausa < cen.PAUSA_MINIMA:
        raise ValueError(f"pausa={pausa} es menor que el minimo {cen.PAUSA_MINIMA}s.")
    desde = hasta - timedelta(days=dias - 1)
    slugs, hallazgos = actualizar_slugs(
        sesion, carpeta, range(desde.year, hasta.year + 1), forzar=True, timeout=timeout
    )

    con_documentos = 0
    for dia in cen.dias_entre(desde, hasta):
        time.sleep(pausa)
        html = cen.obtener_pagina_dia(dia, sesion, timeout, slugs)
        if html is None:
            continue
        docs = cen.parsear_documentos(html, dia)
        con_documentos += bool(docs)
        hallazgos += revisar_pagina_dia(dia, html, docs)

    if con_documentos == 0:
        hallazgos.append(
            _h(
                "aviso",
                "sin_publicaciones_recientes",
                f"Ningun dia entre {desde} y {hasta} tiene documentos publicados.",
                f"{dias} dias revisados",
                "El CEN publica el preliminar al dia siguiente. Si pasan varios dias sin "
                "nada, revisar si cambio la seccion del sitio o el slug.",
            )
        )

    manifiesto = cen.leer_manifiesto(carpeta)
    if manifiesto:
        ultimo = max(manifiesto.values(), key=lambda e: e["fecha_operacion"])
        ruta = carpeta / ultimo["nombre"]
        if ruta.exists():
            hallazgos += revisar_zip(ruta, date.fromisoformat(ultimo["fecha_operacion"]))
        primero = date.fromisoformat(min(e["fecha_operacion"] for e in manifiesto.values()))
        hallazgos += revisar_completitud(manifiesto, primero, hasta, hoy or date.today())
    return hallazgos


# ================================================================== reporte

ORDEN_SEVERIDAD = {"critico": 0, "aviso": 1, "info": 2}


def escribir_reporte(
    hallazgos: list[Hallazgo], carpeta: Path, titulo: str = "Vigilancia de la fuente"
) -> tuple[Path, Path] | None:
    """Escribe el reporte en Markdown (para leer) y JSON (para procesar).

    No escribe nada si no hay hallazgos. Devuelve (ruta_md, ruta_json).
    """
    if not hallazgos:
        return None
    carpeta.mkdir(parents=True, exist_ok=True)
    sello = time.strftime("%Y%m%d_%H%M%S")
    md, js = carpeta / f"deriva_{sello}.md", carpeta / f"deriva_{sello}.json"
    ordenados = sorted(hallazgos, key=lambda h: (ORDEN_SEVERIDAD[h["severidad"]], h["tipo"]))
    conteo = Counter(h["severidad"] for h in hallazgos)

    lineas = [
        f"# {titulo}",
        "",
        f"Generado el {time.strftime('%Y-%m-%d %H:%M:%S')}. "
        f"**{conteo['critico']} critico(s), {conteo['aviso']} aviso(s), "
        f"{conteo['info']} informativo(s).**",
        "",
        "## Como usar este reporte",
        "",
        "Cada hallazgo es una diferencia entre lo que publica el Coordinador y lo que el "
        "programa conoce (el catalogo de `quality/deriva.py` y los patrones de "
        "`extract/coordinador_cmg.py`).",
        "",
        "- **critico**: no ingerir el archivo afectado hasta resolverlo.",
        "- **aviso**: algo cambio; la ingesta sigue siendo valida, pero hay que revisarlo.",
        "- **info**: cambio conocido o ya adoptado automaticamente.",
        "",
        "Para resolverlos con Claude: entregarle este archivo con la instruccion "
        '"resuelve los hallazgos criticos y avisos de este reporte". Por cada cambio '
        "que se adopte hay que actualizar el catalogo **y agregar un test con el caso "
        "real**, para que quede como regresion.",
        "",
        "## Hallazgos",
        "",
    ]
    for i, h in enumerate(ordenados, 1):
        lineas += [
            f"### {i}. [{h['severidad'].upper()}] {h['tipo']}",
            "",
            h["detalle"],
            "",
            f"- **Evidencia:** `{h['evidencia']}`" if h["evidencia"] else "- **Evidencia:** —",
            f"- **Accion:** {h['accion']}",
            "",
        ]
    md.write_text("\n".join(lineas), encoding="utf-8")
    js.write_text(json.dumps(ordenados, indent=1, ensure_ascii=False), encoding="utf-8")
    return md, js
