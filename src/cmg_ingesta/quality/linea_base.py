"""Chequeo previo: comparar el sitio de hoy con la linea base del programa.

El programa se construyo sobre un sitio concreto: ciertas clases en el HTML,
cierto formato de fecha, cierta ubicacion de los ZIP. Si el Coordinador cambia
algo de eso, el lector puede dejar de ver documentos SIN fallar: no hay error,
solo dias "sin publicacion". Este chequeo se corre ANTES de descargar y compara
contra `config/linea_base_cen.toml`:

    indice          ¿sigue listando los años, con sufijo leible?
    marcadores      ¿la pagina de un dia tiene las clases y textos que usa el lector?
    dia referencia  ¿un dia conocido sigue teniendo sus documentos, con la misma
                    etiqueta y fecha de publicacion?
    sitemap         ¿responde y tiene sub-sitemaps de documentos?

Severidades:
- critico  algo estructural cambio: la descarga NO se hace (`detiene`).
- aviso    cambio que hay que mirar, pero la descarga sigue siendo valida.
- info     cambio esperado: el dia de referencia tiene una revision nueva. Los
           documentos se revisan con el tiempo (v2, v3); eso no es un problema.

Cuesta 3 peticiones: indice, dia de referencia y sitemap.
"""

import tomllib
from datetime import date
from pathlib import Path
from typing import TypedDict

from cmg_ingesta.config import RAIZ_PROYECTO
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.quality.deriva import Hallazgo, _h

RUTA_POR_DEFECTO = RAIZ_PROYECTO / "config" / "linea_base_cen.toml"


class DocumentoEsperado(TypedDict):
    nombre: str
    etiqueta: str
    publicado: str


class LineaBase(TypedDict):
    ruta: str
    indice_url: str
    anios: list[int]
    marcadores: dict[str, str]
    dia_referencia: date
    documentos: list[DocumentoEsperado]
    sitemap_url: str
    sitemap_debe_contener: str


def cargar(ruta: Path | None = None) -> LineaBase:
    """Lee `config/linea_base_cen.toml`. Lanza ValueError con el detalle si esta mal."""
    ruta = ruta or RUTA_POR_DEFECTO
    try:
        d = tomllib.loads(ruta.read_text(encoding="utf-8"))
        ref = d["dia_referencia"]
        return {
            "ruta": str(ruta),
            "indice_url": str(d["indice"]["url"]),
            "anios": [int(a) for a in d["indice"]["anios"]],
            "marcadores": {str(k): str(v) for k, v in d["marcadores"].items()},
            "dia_referencia": date.fromisoformat(str(ref["fecha"])),
            "documentos": [
                {
                    "nombre": str(x["nombre"]),
                    "etiqueta": str(x["etiqueta"]),
                    "publicado": str(x["publicado"]),
                }
                for x in ref["documentos"]
            ],
            "sitemap_url": str(d["sitemap"]["url"]),
            "sitemap_debe_contener": str(d["sitemap"]["debe_contener"]),
        }
    except FileNotFoundError as e:
        raise ValueError(f"no existe la linea base: {ruta}") from e
    except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as e:
        raise ValueError(f"la linea base {ruta} esta mal escrita: {e}") from e


ACCION_CRITICA = (
    "No se descargo nada. Revisar el sitio en el navegador y comparar con "
    "config/linea_base_cen.toml. Si el cambio es real y el programa ya se adapto, "
    "actualizar la linea base. Para descargar igual: cmg descargar-cen --omitir-chequeo."
)


# ------------------------------------------------------------ comparaciones


def comparar_indice(html: str, base: LineaBase) -> list[Hallazgo]:
    """¿El indice sigue listando los años de la linea base?"""
    slugs = cen.parsear_slugs_anio(html)
    faltan = [a for a in base["anios"] if a not in slugs]
    if not slugs:
        return [
            _h(
                "critico",
                "base_indice_sin_anios",
                "El indice de años no lista ningun año con el formato conocido.",
                base["indice_url"],
                ACCION_CRITICA,
            )
        ]
    if faltan:
        return [
            _h(
                "critico",
                "base_indice_sin_anio",
                f"El indice ya no lista los años {faltan}.",
                f"lista: {sorted(slugs)}",
                ACCION_CRITICA,
            )
        ]
    return []


def comparar_marcadores(html: str, base: LineaBase) -> list[Hallazgo]:
    """¿La pagina de dia tiene los textos y clases de los que depende el lector?"""
    faltan = {k: v for k, v in base["marcadores"].items() if v not in html}
    if not faltan:
        return []
    return [
        _h(
            "critico",
            "base_marcador_ausente",
            f"La pagina del dia de referencia ya no contiene: {', '.join(faltan)}. "
            "El lector depende de esas marcas.",
            "; ".join(f"{k} = {v!r}" for k, v in faltan.items()),
            ACCION_CRITICA,
        )
    ]


def comparar_documentos(html: str, base: LineaBase) -> list[Hallazgo]:
    """¿El dia de referencia sigue teniendo sus documentos, igual que antes?"""
    dia = base["dia_referencia"]
    docs = {d["nombre"]: d for d in cen.parsear_documentos(html, dia)}
    hallazgos: list[Hallazgo] = []
    esperados = {e["nombre"] for e in base["documentos"]}

    faltan = sorted(esperados - set(docs))
    if faltan:
        hallazgos.append(
            _h(
                "critico",
                "base_documento_ausente",
                f"El dia de referencia {dia} ya no muestra documentos que tenia. El lector "
                "podria no estar viendo lo que publica el sitio.",
                ", ".join(faltan),
                ACCION_CRITICA,
            )
        )
    for e in base["documentos"]:
        d = docs.get(e["nombre"])
        if d is None:
            continue
        if d["etiqueta"] != e["etiqueta"]:
            hallazgos.append(
                _h(
                    "aviso",
                    "base_etiqueta_distinta",
                    f"La etiqueta de {e['nombre']} cambio.",
                    f"antes {e['etiqueta']!r}, hoy {d['etiqueta']!r}",
                    "Revisar config/linea_base_cen.toml y RE_ETIQUETA.",
                )
            )
        if d["fecha_publicacion"] != e["publicado"]:
            hallazgos.append(
                _h(
                    "aviso",
                    "base_fecha_publicacion_distinta",
                    f"La fecha de publicacion de {e['nombre']} cambio: puede ser una re-subida.",
                    f"antes {e['publicado']}, hoy {d['fecha_publicacion']}",
                    "Revisar si el archivo cambio; si es asi, se baja como reemision.",
                )
            )
    nuevos = sorted(set(docs) - esperados)
    if nuevos:
        hallazgos.append(
            _h(
                "info",
                "base_revision_en_referencia",
                f"El dia de referencia {dia} tiene documentos nuevos. Es esperable: los "
                "documentos se revisan con el tiempo (v2, v3).",
                ", ".join(nuevos),
                "Nada que hacer. Si se quiere, agregarlos a la linea base.",
            )
        )
    return hallazgos


def comparar_sitemap(xml: str, base: LineaBase) -> list[Hallazgo]:
    """¿El sitemap tiene sub-sitemaps de documentos?"""
    if base["sitemap_debe_contener"] in xml:
        return []
    return [
        _h(
            "aviso",
            "base_sitemap_cambiado",
            "El sitemap ya no lista sub-sitemaps de documentos: las revisiones de dias "
            "antiguos no se van a detectar.",
            base["sitemap_url"],
            "Revisar el sitemap en el navegador. La descarga normal sigue funcionando.",
        )
    ]


# --------------------------------------------------------------------- red


def chequeo_previo(
    sesion: cen.Sesion, base: LineaBase | None = None, timeout: float = 30.0
) -> list[Hallazgo]:
    """Compara el sitio de hoy con la linea base. 3 peticiones, sin ZIP.

    Un sitio caido no se reporta aqui: sube `cen.ErrorDescarga` desde `pedir`,
    igual que en la descarga.
    """
    base = base or cargar()
    hallazgos: list[Hallazgo] = []

    r = cen.pedir(sesion, base["indice_url"], timeout)
    if r.status_code != 200:
        raise cen.ErrorDescarga(f"{base['indice_url']}: HTTP {r.status_code}")
    hallazgos += comparar_indice(r.text, base)

    url_ref = cen.url_dia(base["dia_referencia"], cen.parsear_slugs_anio(r.text) or None)
    r = cen.pedir(sesion, url_ref, timeout)
    if r.status_code != 200:
        hallazgos.append(
            _h(
                "critico",
                "base_dia_referencia_inaccesible",
                f"La pagina del dia de referencia responde HTTP {r.status_code}: el patron "
                "de URL de los dias pudo cambiar.",
                url_ref,
                ACCION_CRITICA,
            )
        )
    else:
        hallazgos += comparar_marcadores(r.text, base)
        hallazgos += comparar_documentos(r.text, base)

    r = cen.pedir(sesion, base["sitemap_url"], timeout)
    if r.status_code != 200:
        hallazgos.append(
            _h(
                "aviso",
                "base_sitemap_inaccesible",
                f"El sitemap responde HTTP {r.status_code}: las revisiones de dias antiguos "
                "no se van a detectar.",
                base["sitemap_url"],
                "La descarga normal sigue funcionando. Reintentar mas tarde.",
            )
        )
    else:
        hallazgos += comparar_sitemap(r.text, base)
    return hallazgos


def detiene(hallazgos: list[Hallazgo]) -> bool:
    """True si el chequeo encontro algo critico: no se debe descargar."""
    return any(h["severidad"] == "critico" and h["tipo"].startswith("base_") for h in hallazgos)
