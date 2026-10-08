"""Extractor de Costos Marginales Reales del sitio del Coordinador.

Hechos del sitio, verificados por el usuario inspeccionandolo (no deducidos):

- HTML estatico, server-rendered. Los `<a href>` a los ZIP vienen en el HTML
  inicial; `requests` + BeautifulSoup basta.
- Jerarquia de 4 niveles: INDICE -> AÑO -> MES -> DIA. **Los ZIP solo estan en
  el nivel DIA.** Cero ZIP en los tres primeros es correcto, no un bug.
- **El slug cambia por año** (ver `SUFIJO`). En 2024 lleva el sufijo largo
  `-costo-marginal-real-transferencias-economicas`.
- La carpeta `uploads/YYYY/MM/` es el mes de **publicacion**, no el del dato.
- Un dia puede tener `pre`, `def`, y despues `v2`/`v3` tras dictamenes del
  Panel de Expertos. Por eso hay que **re-visitar dias antiguos**.

**Por que la sesion usa `curl_cffi` y no `requests`.** El sitio esta detras de
Cloudflare, que filtra por **huella TLS** (JA3/JA4): la negociacion TLS de
`requests`/urllib3 es reconocible y la corta en el handshake
(`SSLEOFError: UNEXPECTED_EOF_WHILE_READING`); `httpx` llega mas lejos pero recibe
403 `cf-mitigated: challenge`. Cambiar headers o User-Agent no sirve, porque la
decision se toma ANTES de leerlos. `curl_cffi` con `impersonate="chrome"` negocia
TLS con la huella de Chrome y obtiene 200 en las paginas y en los ZIP. No hace
falta navegador.

El parseo y la sincronizacion NO dependen de `curl_cffi`: reciben cualquier objeto
con la forma de `Sesion`. Eso permite testearlos sin red.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol, TypedDict

from bs4 import BeautifulSoup

BASE = "https://www.coordinador.cl"
RAIZ = f"{BASE}/mercados/documentos/costo-marginal-real"

#: Sufijo del slug, que cambia por año. Verificado:
#:   15-enero-2026-costo-marginal-real                            -> 200
#:   15-agosto-2024-costo-marginal-real-transferencias-economicas -> 200
#:   15-agosto-2024-costo-marginal-real                           -> 404
SUFIJO: dict[int, str] = {
    2024: "-costo-marginal-real-transferencias-economicas",
    2025: "-costo-marginal-real",
    2026: "-costo-marginal-real",
}

#: En minuscula y sin tilde, como los usa el sitio.
MESES: dict[int, str] = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}

#: La fecha de publicacion se lee POR CONTENIDO y no por clase: el primer
#: `span.documentos-Publicar-Fecha` del bloque viene VACIO.
RE_PUB = re.compile(r"Fecha de publicaci[óo]n:\s*(\d{2})/(\d{2})/(\d{4})")

#: El nombre del archivo, en TODAS las variantes que publica el sitio. Verificadas
#: contra nombres reales (2026-10-06):
#:
#:     Antecedentes_CMG_Real_def_260115.zip       base
#:     Antecedentes_CMG_Real_def_v2_260707.zip    version ANTES de la fecha
#:     Antecedentes_CMG_Real_pre_260906_v2.zip    version DESPUES de la fecha
#:     Antecedentes_CMG_Real_pre_260404-1.zip     re-subida (sufijo -N de WordPress)
#:
#: El brief original solo documentaba la primera y la segunda forma. La tercera y
#: la cuarta aparecieron al bajar archivos reales: con el patron anterior quedaban
#: clasificadas como "desconocido".
RE_NOMBRE = re.compile(
    r"CMG_Real_(?P<tipo>[a-z]+)"
    r"(?:_v(?P<v_antes>\d+))?"
    r"_(?P<aammdd>\d{6})"
    r"(?:_v(?P<v_despues>\d+))?"
    r"(?:-(?P<reemision>\d+))?"
    r"\.zip$",
    re.I,
)

PAUSA_MINIMA = 1.0
MANIFIESTO = "manifiesto.json"


class Documento(TypedDict):
    """Un ZIP publicado para un dia de operacion.

    Es un `dict` con claves declaradas (`TypedDict`), no una clase: no se
    instancia ni tiene metodos. Se escribe `{"tipo": "def", ...}`.
    """

    fecha_operacion: str
    etiqueta: str
    tipo: str
    version: int
    #: 0 si es la subida original; N si WordPress le agrego "-N" al nombre.
    reemision: int
    fecha_publicacion: str | None
    url: str
    nombre: str


class EntradaManifiesto(TypedDict):
    """Lo que se guarda de cada archivo ya descargado."""

    url: str
    nombre: str
    tipo: str
    version: int
    reemision: int
    fecha_operacion: str
    fecha_publicacion: str | None
    sha256: str
    bytes: int
    descargado_en: str


class Respuesta(Protocol):
    """Lo minimo que se le pide a una respuesta HTTP.

    `Protocol` describe una FORMA, no una jerarquia: cualquier objeto con estos
    atributos sirve. Permite tipar sin `Any` y pasar una respuesta falsa en los
    tests sin heredar de nada.
    """

    status_code: int
    text: str
    content: bytes

    def raise_for_status(self) -> None: ...


class Sesion(Protocol):
    """Lo minimo que se le pide a una sesion HTTP."""

    def get(self, url: str, timeout: float = ...) -> Respuesta: ...


def nueva_sesion() -> Sesion:
    """Una sesion HTTP que el sitio acepta: huella TLS de Chrome via `curl_cffi`.

    Se importa adentro para que el resto del modulo (parseo, URLs, manifiesto) se
    pueda usar y testear sin tener `curl_cffi` instalado.
    """
    from curl_cffi import requests as curl_requests

    # El unico `type: ignore` justificado del modulo: es la frontera con codigo de
    # terceros. `curl_cffi.Session.get` acepta muchos mas parametros y devuelve su
    # propio tipo de respuesta, asi que no calza EXACTO con `Sesion`, aunque en
    # ejecucion tiene todo lo que usamos (status_code, text, content,
    # raise_for_status). mypy --strict avisa si un ignore sobra: si este dejara de
    # hacer falta, el chequeo fallaria y habria que quitarlo.
    sesion: Sesion = curl_requests.Session(impersonate="chrome")  # type: ignore[assignment]
    return sesion


# ------------------------------------------------------------- slugs de año

#: Mapa año -> sufijo. Se pasa a las funciones de URL; si es None se usa SUFIJO.
Slugs = dict[int, str]

#: La pagina que lista los años. Es la fuente de verdad de los slugs.
INDICE = f"{BASE}/mercados/documentos/transferencias-economicas/costo-marginal-real/"

#: El ultimo segmento de un enlace de año: "2024-costo-marginal-real-transferencias-..."
RE_SLUG_ANIO = re.compile(r"^(?P<anio>\d{4})(?P<sufijo>-costo-marginal-real[a-z0-9-]*)$")

ARCHIVO_SLUGS = "slugs.json"


def parsear_slugs_anio(html: str) -> Slugs:
    """Lee del indice el sufijo de cada año. Funcion pura.

    En vez de mantener `SUFIJO` a mano, el programa lo LEE del sitio: el ultimo
    segmento del enlace de cada año es "{año}{sufijo}". Asi un año nuevo, o un
    cambio de slug como el de 2024, se adopta solo.
    """
    soup = BeautifulSoup(html, "html.parser")
    slugs: Slugs = {}
    for a in soup.find_all("a", href=True):
        ultimo = str(a["href"]).rstrip("/").rsplit("/", 1)[-1]
        m = RE_SLUG_ANIO.match(ultimo)
        if m:
            slugs[int(m.group("anio"))] = m.group("sufijo")
    return slugs


def descubrir_slugs(sesion: Sesion, timeout: float = 30.0) -> Slugs:
    """Pide el indice y devuelve los slugs que publica hoy el sitio."""
    r = sesion.get(INDICE, timeout=timeout)
    r.raise_for_status()
    return parsear_slugs_anio(r.text)


def leer_slugs(carpeta: Path) -> Slugs:
    """Los slugs guardados de la ultima consulta al indice, o vacio."""
    ruta = carpeta / ARCHIVO_SLUGS
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {int(k): str(v) for k, v in datos.get("slugs", {}).items()}


def reemplazar_atomico(tmp: Path, ruta: Path, intentos: int = 5, espera: float = 0.2) -> None:
    """`tmp` pasa a ser `ruta` de un golpe, reintentando si Windows lo bloquea.

    En Windows, `os.replace` falla con `PermissionError` si otro proceso (el
    antivirus, el indexador, OneDrive) tiene el archivo abierto en ese instante.
    Se vio en un test el 2026-10-08. Dura milisegundos, asi que se reintenta unas
    veces antes de rendirse. Si igual falla, la excepcion sube: el archivo viejo
    sigue intacto, porque el reemplazo nunca llego a ocurrir.
    """
    for intento in range(intentos):
        try:
            tmp.replace(ruta)
            return
        except PermissionError:
            if intento == intentos - 1:
                raise
            time.sleep(espera)


def guardar_slugs(carpeta: Path, slugs: Slugs) -> Path:
    """Guarda los slugs con la fecha de consulta, de forma atomica."""
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / ARCHIVO_SLUGS
    tmp = ruta.with_suffix(".json.tmp")
    datos = {
        "consultado_en": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "slugs": {str(k): v for k, v in sorted(slugs.items())},
    }
    tmp.write_text(json.dumps(datos, indent=1, ensure_ascii=False), encoding="utf-8")
    reemplazar_atomico(tmp, ruta)
    return ruta


# --------------------------------------------------------------------- URLs


def _slug_anio(anio: int, slugs: Slugs | None = None) -> str:
    """El slug del año, con su sufijo. Revienta si el año no esta mapeado.

    Busca primero en `slugs` (los leidos del sitio) y despues en `SUFIJO`.

    El `raise` es deliberado: un año sin sufijo conocido construiria una URL
    que da 404, y el codigo lo interpretaria como "dia sin publicacion". Eso
    dejaria un hueco SILENCIOSO. Mejor que falle.
    """
    mapa = {**SUFIJO, **(slugs or {})}
    try:
        return f"{anio}{mapa[anio]}"
    except KeyError as e:
        raise ValueError(
            f"Slug desconocido para {anio}. El indice del sitio todavia no lo "
            f"publica, o cambio su formato. Años conocidos: {sorted(mapa)}"
        ) from e


def url_anio(anio: int, slugs: Slugs | None = None) -> str:
    return f"{RAIZ}/{_slug_anio(anio, slugs)}/"


def url_mes(anio: int, mes: int, slugs: Slugs | None = None) -> str:
    slug = _slug_anio(anio, slugs)
    return f"{RAIZ}/{slug}/{MESES[mes]}-{slug}/"


def url_dia(d: date, slugs: Slugs | None = None) -> str:
    """La URL de la pagina de un dia, que es la unica que tiene los ZIP."""
    slug = _slug_anio(d.year, slugs)
    mes = MESES[d.month]
    return f"{RAIZ}/{slug}/{mes}-{slug}/{d.day:02d}-{mes}-{slug}/"


def dias_entre(desde: date, hasta: date) -> Iterator[date]:
    """Los dias del rango, inclusive. Revienta si esta invertido."""
    if desde > hasta:
        raise ValueError(f"rango invertido: {desde} es posterior a {hasta}")
    dia = desde
    while dia <= hasta:
        yield dia
        dia += timedelta(days=1)


# ------------------------------------------------------------------ parseo


def _info_nombre(nombre: str) -> tuple[str, int, int]:
    """(tipo, version, reemision) a partir del nombre del archivo.

    def_260115.zip      -> ("def", 1, 0)
    def_v2_260707.zip   -> ("def", 2, 0)
    pre_260906_v2.zip   -> ("pre", 2, 0)
    pre_260404-1.zip    -> ("pre", 1, 1)
    otra cosa           -> ("desconocido", 1, 0)
    """
    m = RE_NOMBRE.search(nombre)
    if not m:
        return ("desconocido", 1, 0)
    tipo = m.group("tipo").lower()
    if tipo not in ("def", "pre"):
        return ("desconocido", 1, 0)
    version = int(m.group("v_antes") or m.group("v_despues") or 1)
    reemision = int(m.group("reemision") or 0)
    return (tipo, version, reemision)


def parsear_documentos(html: str, dia: date) -> list[Documento]:
    """Extrae los ZIP de CMg del HTML de una pagina de DIA.

    Funcion pura: recibe texto, devuelve datos. Sin red, asi que se testea
    contra fixtures.

    Tres detalles del markup que condicionan el codigo:

    1. El contenedor de cada documento **no tiene `class`**, solo estilo
       inline. Por eso se ancla en el `<a>` del ZIP y se sube con
       `find_parent("div")`, en vez de seleccionar por el estilo.
    2. Con varios documentos, cada uno esta en su propio `div` como HERMANO.
       Ninguno contiene al otro, asi que `find_parent` le da a cada ZIP su
       propia fecha y no se cruzan.
    3. El primer `span.documentos-Publicar-Fecha` del bloque viene **VACIO**.
       Por eso la fecha se busca por CONTENIDO con `RE_PUB` sobre el texto del
       bloque, no con `find(class_=...)`.
    """
    soup = BeautifulSoup(html, "html.parser")
    docs: list[Documento] = []
    vistos: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "/wp-content/uploads/" not in href or not href.lower().endswith(".zip"):
            continue
        if "CMG_Real" not in href or href in vistos:
            continue
        vistos.add(href)

        bloque = a.find_parent("div") or a.parent
        texto = " ".join(bloque.get_text(" ", strip=True).split()) if bloque else ""

        etiqueta = ""
        if bloque is not None:
            span = bloque.find("span", class_="informes-estudio-Titulo")
            if span is not None:
                etiqueta = str(span.get("title") or span.get_text(strip=True))

        pub = None
        if m := RE_PUB.search(texto):
            pub = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

        nombre = href.rsplit("/", 1)[-1]
        tipo, version, reemision = _info_nombre(nombre)

        docs.append(
            {
                "fecha_operacion": dia.isoformat(),
                "etiqueta": etiqueta or texto[:120],
                "tipo": tipo,
                "version": version,
                "reemision": reemision,
                "fecha_publicacion": pub,
                "url": href if href.startswith("http") else BASE + href,
                "nombre": nombre,
            }
        )
    return docs


def parsear_enlaces_hijos(html: str) -> list[str]:
    """Los enlaces a paginas hijas (meses desde un año, dias desde un mes).

    Sirve para el BACKFILL recorriendo el arbol en vez de construir URLs, que
    es mas robusto porque el slug ya cambio una vez.

    Se deduplica con un `dict` para conservar el orden de aparicion.
    """
    soup = BeautifulSoup(html, "html.parser")
    vistos: dict[str, None] = {}
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "/costo-marginal-real" not in href or href.lower().endswith(".zip"):
            continue
        absoluta = href if href.startswith("http") else BASE + href
        vistos.setdefault(absoluta, None)
    return list(vistos)


def obtener_pagina_dia(
    dia: date, sesion: Sesion, timeout: float = 30.0, slugs: Slugs | None = None
) -> str | None:
    """El HTML de la pagina del dia, o None si el dia no tiene pagina (404).

    Un **404 es un dia sin publicacion**, no un error. Cualquier otro codigo de
    error sí levanta excepcion, porque un 500 o un 403 no significan "no hay
    datos".
    """
    r = sesion.get(url_dia(dia, slugs), timeout=timeout)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def listar_documentos(
    dia: date, sesion: Sesion, timeout: float = 30.0, slugs: Slugs | None = None
) -> list[Documento]:
    """Pide la pagina del dia y devuelve sus documentos. Vacia si es 404."""
    html = obtener_pagina_dia(dia, sesion, timeout, slugs)
    return parsear_documentos(html, dia) if html is not None else []


# ------------------------------------------------------------- manifiesto


def ruta_manifiesto(carpeta: Path) -> Path:
    return carpeta / MANIFIESTO


def leer_manifiesto(carpeta: Path) -> dict[str, EntradaManifiesto]:
    """El manifiesto, o vacio si no existe o esta corrupto.

    Un manifiesto ilegible se trata como vacio a proposito: es peor abortar la
    sincronizacion que volver a descargar.
    """
    ruta = ruta_manifiesto(carpeta)
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return dict(datos) if isinstance(datos, dict) else {}


def guardar_manifiesto(carpeta: Path, manifiesto: dict[str, EntradaManifiesto]) -> Path:
    """Escribe el manifiesto de forma atomica: archivo temporal + reemplazo.

    Sin esto, un corte a mitad de la escritura deja un JSON truncado, y el
    manifiesto es justamente lo que permite no re-descargar.
    """
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = ruta_manifiesto(carpeta)
    tmp = ruta.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(manifiesto, indent=1, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    reemplazar_atomico(tmp, ruta)
    return ruta


def sha256_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


# ---------------------------------------------------------- sincronizacion


#: Gancho que se llama con cada pagina de dia leida: (dia, html, documentos).
AlLeerPagina = Callable[[date, str, list[Documento]], None]
#: Gancho que se llama con cada ZIP recien bajado: (ruta, entrada del manifiesto).
AlBajar = Callable[[Path, EntradaManifiesto], None]


def sincronizar(
    desde: date,
    hasta: date,
    carpeta: Path,
    sesion: Sesion,
    pausa: float = PAUSA_MINIMA,
    timeout: float = 30.0,
    slugs: Slugs | None = None,
    al_leer_pagina: AlLeerPagina | None = None,
    al_bajar: AlBajar | None = None,
) -> list[EntradaManifiesto]:
    """Descarga los ZIP nuevos del rango y los suma al manifiesto.

    **Idempotente**: la clave del manifiesto es el NOMBRE del archivo, asi que
    una segunda corrida sobre el mismo rango no descarga nada y devuelve lista
    vacia. Y como `_def_v2_260115.zip` tiene un nombre distinto de
    `_def_260115.zip`, una revision nueva **sí** se descarga y **no** borra la
    anterior: las dos quedan, con su propio sha256.

    **Descarga todo lo que parezca CMg Real**, aunque su nombre no calce con un
    patron conocido (queda `tipo="desconocido"`): bajar de mas no rompe nada y
    asegura que un archivo nuevo no se pierda. Lo estricto es la INGESTA, no la
    descarga.

    Los ganchos `al_leer_pagina` y `al_bajar` permiten inspeccionar cada pagina
    y cada ZIP sin que este modulo sepa nada de validaciones: es el mismo
    patron que `avisar` en la migracion (inyeccion de dependencias). Los usa
    `quality/deriva.py` para detectar cambios en el sitio.

    Devuelve solo lo nuevo de ESTA corrida.
    """
    if pausa < PAUSA_MINIMA:
        raise ValueError(
            f"pausa={pausa} es menor que el minimo {PAUSA_MINIMA}s. "
            "El sitio es de un tercero: no se le pega sin pausa."
        )

    carpeta.mkdir(parents=True, exist_ok=True)
    manifiesto = leer_manifiesto(carpeta)
    nuevos: list[EntradaManifiesto] = []

    for i, dia in enumerate(dias_entre(desde, hasta)):
        if i:
            time.sleep(pausa)
        html = obtener_pagina_dia(dia, sesion, timeout, slugs)
        if html is None:
            continue
        docs = parsear_documentos(html, dia)
        if al_leer_pagina is not None:
            al_leer_pagina(dia, html, docs)

        for doc in docs:
            if doc["nombre"] in manifiesto:
                continue

            time.sleep(pausa)
            r = sesion.get(doc["url"], timeout=timeout)
            r.raise_for_status()
            datos = r.content

            destino = carpeta / doc["nombre"]
            destino.write_bytes(datos)

            entrada: EntradaManifiesto = {
                "url": doc["url"],
                "nombre": doc["nombre"],
                "tipo": doc["tipo"],
                "version": doc["version"],
                "reemision": doc["reemision"],
                "fecha_operacion": doc["fecha_operacion"],
                "fecha_publicacion": doc["fecha_publicacion"],
                "sha256": sha256_bytes(datos),
                "bytes": len(datos),
                "descargado_en": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            manifiesto[doc["nombre"]] = entrada
            nuevos.append(entrada)
            if al_bajar is not None:
                al_bajar(destino, entrada)

    if nuevos:
        guardar_manifiesto(carpeta, manifiesto)
    return nuevos


def mejor_version(docs: list[Documento]) -> Documento | None:
    """De varios documentos de un dia, el que hay que usar.

    Prioridad: `def` sobre `pre`, y mayor `version` sobre menor. Asi
    `def_v2` gana a `def`, que gana a `pre_v2`, que gana a `pre`.
    """
    if not docs:
        return None
    orden = {"def": 2, "pre": 1, "desconocido": 0}
    return max(docs, key=lambda d: (orden.get(d["tipo"], 0), d["version"], d["reemision"]))


#: Primer dia de la fuente (b). Lo anterior viene del Maestro (CLAUDE.md §0.1).
INICIO_FUENTE = date(2025, 1, 1)


def desde_sugerido(manifiesto: dict[str, EntradaManifiesto], ayer: date) -> date:
    """Desde que dia conviene sincronizar para ponerse al dia sin rehacer todo.

    Es el menor entre:
    - el PRIMER DIA SIN ARCHIVO desde el inicio de la fuente (un hueco), y
    - el primer dia que todavia solo tiene preliminar (puede haber llegado el def).

    "Lo nuevo" (el dia siguiente al ultimo descargado) es un caso del primero.

    Antes solo miraba despues del ultimo dia descargado: con el manifiesto vacio
    sugeria 2025-01-01, pero apenas se bajaban unos dias recientes pasaba a
    sugerir esos dias, y el hueco anterior quedaba fuera para siempre.

    Nunca devuelve un dia posterior a `ayer`.
    """
    tipos: dict[date, set[str]] = {}
    for e in manifiesto.values():
        dia = date.fromisoformat(e["fecha_operacion"])
        tipos.setdefault(dia, set()).add(e["tipo"])
    hueco = next((d for d in dias_entre(INICIO_FUENTE, ayer) if d not in tipos), ayer)
    solo_pre = [d for d, t in tipos.items() if "def" not in t and d >= INICIO_FUENTE]
    return min([hueco, *solo_pre, ayer])
