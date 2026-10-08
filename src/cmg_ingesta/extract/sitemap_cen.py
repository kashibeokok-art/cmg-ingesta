"""Deteccion de REVISIONES (v2, v3...) usando el sitemap del sitio del Coordinador.

El problema que resuelve (problema P1 del informe de busqueda y descarga): el
Coordinador publica versiones corregidas de dias que YA tenian definitivo, con
una mediana de 38 dias de atraso y hasta 546. El programa solo volvia a mirar
dias sin archivo o en preliminar, asi que esas revisiones no se veian nunca.

Revisar las ~800 paginas de dia para encontrarlas cuesta ~36 minutos. El sitio
publica en cambio un `sitemap.xml` (WordPress + All in One SEO) que lista cada
documento con su fecha de ultima modificacion (`lastmod`):

    sitemap.xml                      indice: 826 "documentos-sitemapN.xml", con su lastmod
      documentos-sitemap826.xml      ~60 documentos, con su lastmod
        /documentos/antecedentes-costo-marginal-real-definitivo-v2-260831/

El slug de cada documento trae el dia de operacion al final (`260831`). Con
eso, ponerse al dia cuesta 1 peticion al indice + 1 por cada sub-sitemap que
cambio desde la ultima vez (normalmente 1 a 3).

Verificado el 2026-10-08: un solo sub-sitemap tenia 28 revisiones publicadas el
dia anterior (27 definitivos v2 y 1 v3), 20 de ellas de agosto 2026.

El "desde cuando" se guarda en `sitemap.json`, junto al manifiesto.
"""

import json
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TypedDict

from cmg_ingesta.extract import coordinador_cmg as cen

SITEMAP = f"{cen.BASE}/sitemap.xml"
ESTADO = "sitemap.json"

#: Tope de sub-sitemaps por corrida. Si el punto de partida es muy viejo podrian
#: ser cientos; mejor avisar y que el usuario decida un barrido completo.
MAX_SUBSITEMAPS = 40

#: Una entrada <url> o <sitemap>: loc + lastmod, con o sin CDATA.
RE_ENTRADA = re.compile(
    r"<loc>\s*(?:<!\[CDATA\[)?\s*(?P<url>[^<\]\s]+)\s*(?:\]\]>)?\s*</loc>\s*"
    r"<lastmod>\s*(?:<!\[CDATA\[)?\s*(?P<lastmod>[^<\]\s]+)\s*(?:\]\]>)?\s*</lastmod>"
)

#: La pagina de un documento de CMg Real; el dia de operacion va al final.
RE_DOC_CMG = re.compile(
    r"/documentos/antecedentes-costo-marginal-real[a-z0-9-]*?(?P<aammdd>\d{6})?/?$"
)


class Revision(TypedDict):
    """Un documento de CMg Real que cambio despues del punto de partida."""

    url: str
    modificado: str  # ISO con zona horaria, tal como viene en el sitemap
    dia: str | None  # dia de operacion (ISO), o None si el slug no lo trae


class Busqueda(TypedDict):
    revisiones: list[Revision]
    subsitemaps_consultados: int
    truncada: bool  # True si habia mas sub-sitemaps que MAX_SUBSITEMAPS


# ------------------------------------------------------------------ parseo


def parsear_entradas(xml: str) -> list[tuple[str, datetime]]:
    """(url, lastmod) de cada entrada. Funcion pura. Ignora fechas ilegibles."""
    salida: list[tuple[str, datetime]] = []
    for m in RE_ENTRADA.finditer(xml):
        try:
            salida.append((m.group("url"), datetime.fromisoformat(m.group("lastmod"))))
        except ValueError:
            continue
    return salida


def subsitemaps_modificados(indice_xml: str, desde: datetime) -> list[str]:
    """Los `documentos-sitemapN.xml` modificados despues de `desde`, el mas nuevo primero."""
    pares = [
        (url, mod)
        for url, mod in parsear_entradas(indice_xml)
        if "documentos-sitemap" in url and mod > desde
    ]
    return [url for url, _ in sorted(pares, key=lambda p: p[1], reverse=True)]


def dia_del_slug(url: str) -> date | None:
    """El dia de operacion escrito al final del slug (`...-v2-260831/` -> 2026-08-31)."""
    m = RE_DOC_CMG.search(url)
    if not m or not m.group("aammdd"):
        return None
    try:
        return datetime.strptime(m.group("aammdd"), "%y%m%d").date()
    except ValueError:
        return None


def revisiones_en(xml: str, desde: datetime) -> list[Revision]:
    """Los documentos de CMg Real de un sub-sitemap modificados despues de `desde`."""
    salida: list[Revision] = []
    for url, mod in parsear_entradas(xml):
        if mod <= desde or not RE_DOC_CMG.search(url):
            continue
        dia = dia_del_slug(url)
        salida.append(
            {"url": url, "modificado": mod.isoformat(), "dia": dia.isoformat() if dia else None}
        )
    return salida


# ------------------------------------------------------------------ estado


def leer_estado(carpeta: Path) -> datetime | None:
    """Hasta cuando ya se revisaron las revisiones. None si nunca."""
    ruta = carpeta / ESTADO
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return datetime.fromisoformat(datos["revisado_hasta"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def guardar_estado(carpeta: Path, momento: datetime) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / ESTADO
    tmp = ruta.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"revisado_hasta": momento.isoformat()}), encoding="utf-8")
    cen.reemplazar_atomico(tmp, ruta)


def punto_de_partida(
    carpeta: Path, manifiesto: dict[str, cen.EntradaManifiesto]
) -> datetime | None:
    """Desde cuando buscar revisiones.

    1. Lo guardado en `sitemap.json` (la ultima busqueda completa).
    2. Si no hay, la PRIMERA descarga del manifiesto: todo lo que cambio despues de
       que se miro el sitio por primera vez puede ser una revision no vista.
    3. Si no hay nada descargado, None: no hay contra que comparar todavia, y la
       primera descarga ya trae las versiones vigentes.

    `descargado_en` esta en hora local sin zona; se convierte a UTC para comparar
    con el sitemap, que trae `+00:00`.
    """
    guardado = leer_estado(carpeta)
    if guardado is not None:
        return guardado
    fechas = [e["descargado_en"] for e in manifiesto.values() if e.get("descargado_en")]
    if not fechas:
        return None
    try:
        return datetime.fromisoformat(min(fechas)).astimezone(UTC)
    except ValueError:
        return None


# --------------------------------------------------------------------- red


def buscar_revisiones(
    sesion: cen.Sesion,
    desde: datetime,
    pausa: float = cen.PAUSA_MINIMA,
    timeout: float = 30.0,
    max_subsitemaps: int = MAX_SUBSITEMAPS,
) -> Busqueda:
    """Las revisiones publicadas despues de `desde`. 1 + N peticiones, sin ZIP.

    Sube `cen.ErrorDescarga` si el sitemap no responde: quien llama decide si eso
    es grave (en la vigilancia es un aviso, no un error).
    """
    r = cen.pedir(sesion, SITEMAP, timeout)
    if r.status_code != 200:
        raise cen.ErrorDescarga(f"{SITEMAP}: HTTP {r.status_code}")
    subs = subsitemaps_modificados(r.text, desde)
    truncada = len(subs) > max_subsitemaps

    revisiones: list[Revision] = []
    for url in subs[:max_subsitemaps]:
        time.sleep(pausa)
        r = cen.pedir(sesion, url, timeout)
        if r.status_code != 200:
            raise cen.ErrorDescarga(f"{url}: HTTP {r.status_code}")
        revisiones += revisiones_en(r.text, desde)
    return {
        "revisiones": revisiones,
        "subsitemaps_consultados": min(len(subs), max_subsitemaps),
        "truncada": truncada,
    }
