"""Catalogo EDITABLE de las formas de nombre de los ZIP: `config/nombres_cen.toml`.

Antes las formas vivian en una expresion regular dentro del codigo. El nombre de
los archivos lo escribe a mano el Coordinador (26 formas en 1.765 casos), asi que
cada forma nueva obligaba a editar Python. Ahora son plantillas legibles en un
archivo de texto:

    {tipo}-v{version}_{fecha}      ->  Antecedentes_CMG_Real_def-v2_250203.zip

Este modulo las convierte en expresiones regulares y responde una sola pregunta:
"¿que dice este nombre?" (tipo, version, reemision, fecha y que plantilla calzo).

Al cargar se valida todo: marcadores conocidos, tipos declarados y que el
`ejemplo` de cada forma calce con su propia plantilla. Un error de tipeo en el
archivo detiene el programa con un mensaje claro, en vez de dejar de reconocer
nombres en silencio.
"""

import re
import tomllib
from pathlib import Path
from typing import TypedDict

from cmg_ingesta.config import RAIZ_PROYECTO

RUTA_POR_DEFECTO = RAIZ_PROYECTO / "config" / "nombres_cen.toml"

#: Que expresion regular reemplaza a cada marcador. {tipo} se arma con [tipos].
MARCADORES = {
    "fecha": r"(?P<fecha>\d{5,8})",
    "version": r"(?P<version>\d+)",
    "version2": r"(?P<version2>\d+)",
    "reemision": r"(?P<reemision>\d+)",
}
RE_MARCADOR = re.compile(r"\{(\w+)\}")


class ErrorCatalogo(ValueError):
    """El archivo de nombres tiene un error. El mensaje dice cual y donde."""


class Forma(TypedDict):
    plantilla: str
    ejemplo: str
    nota: str
    patron: re.Pattern[str]


class Catalogo(TypedDict):
    ruta: str
    prefijo: str
    extension: str
    debe_contener: str
    tipos: dict[str, str]  # como aparece en el nombre -> "def" | "pre"
    formas: list[Forma]


class Lectura(TypedDict):
    """Lo que dice un nombre que calzo con alguna forma."""

    tipo: str
    version: int
    reemision: int
    fecha: str  # los digitos tal cual, "" si el nombre no trae
    plantilla: str


def compilar(
    plantilla: str, prefijo: str, extension: str, tipos: dict[str, str]
) -> re.Pattern[str]:
    """Convierte una plantilla en expresion regular. Lanza ErrorCatalogo si esta mal."""
    # el tipo mas largo primero: "prel" se prueba antes que "pre" y "pr"
    alternativas = "|".join(re.escape(t) for t in sorted(tipos, key=len, reverse=True))
    reemplazos = {**MARCADORES, "tipo": f"(?P<tipo>{alternativas})"}

    vistos: list[str] = []
    partes: list[str] = []
    ultimo = 0
    for m in RE_MARCADOR.finditer(plantilla):
        nombre = m.group(1)
        if nombre not in reemplazos:
            raise ErrorCatalogo(
                f"marcador desconocido {{{nombre}}} en la plantilla {plantilla!r}. "
                f"Se pueden usar: {', '.join('{' + k + '}' for k in sorted(reemplazos))}"
            )
        if nombre in vistos:
            raise ErrorCatalogo(
                f"el marcador {{{nombre}}} aparece dos veces en {plantilla!r} "
                "(para una segunda version usa {version2})"
            )
        vistos.append(nombre)
        partes.append(re.escape(plantilla[ultimo : m.start()]))
        partes.append(reemplazos[nombre])
        ultimo = m.end()
    partes.append(re.escape(plantilla[ultimo:]))
    if "tipo" not in vistos:
        raise ErrorCatalogo(f"la plantilla {plantilla!r} no tiene {{tipo}}")
    cuerpo = "".join(partes)
    return re.compile(f"^{re.escape(prefijo)}{cuerpo}{re.escape(extension)}$", re.I)


def _texto(datos: dict[str, object], clave: str, donde: str) -> str:
    valor = datos.get(clave)
    if not isinstance(valor, str) or not valor:
        raise ErrorCatalogo(f"falta '{clave}' (texto) en {donde}")
    return valor


def cargar(ruta: Path | None = None) -> Catalogo:
    """Lee y valida el archivo de nombres. Lanza ErrorCatalogo con el detalle."""
    ruta = ruta or RUTA_POR_DEFECTO
    try:
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ErrorCatalogo(f"no existe el archivo de nombres: {ruta}") from e
    except tomllib.TOMLDecodeError as e:
        raise ErrorCatalogo(f"{ruta} no es TOML valido: {e}") from e

    prefijo = _texto(datos, "prefijo", str(ruta))
    extension = _texto(datos, "extension", str(ruta))
    debe_contener = _texto(datos, "debe_contener", str(ruta)).lower()

    tipos_crudos = datos.get("tipos")
    if not isinstance(tipos_crudos, dict) or not tipos_crudos:
        raise ErrorCatalogo(f"falta la seccion [tipos] en {ruta}")
    tipos = {str(k).lower(): str(v) for k, v in tipos_crudos.items()}
    malos = sorted(v for v in tipos.values() if v not in ("def", "pre"))
    if malos:
        raise ErrorCatalogo(f"en [tipos] solo se permite 'def' o 'pre' como valor: {malos}")

    formas_crudas = datos.get("formas")
    if not isinstance(formas_crudas, list) or not formas_crudas:
        raise ErrorCatalogo(f"no hay ninguna [[formas]] en {ruta}")

    formas: list[Forma] = []
    for i, f in enumerate(formas_crudas, 1):
        if not isinstance(f, dict):
            raise ErrorCatalogo(f"la forma numero {i} de {ruta} esta mal escrita")
        donde = f"la forma numero {i} de {ruta}"
        plantilla = _texto(f, "plantilla", donde)
        ejemplo = _texto(f, "ejemplo", donde)
        patron = compilar(plantilla, prefijo, extension, tipos)
        if not patron.match(ejemplo):
            raise ErrorCatalogo(
                f"el ejemplo {ejemplo!r} no calza con su plantilla {plantilla!r} ({donde}). "
                "Revisa la plantilla o el ejemplo."
            )
        formas.append(
            {
                "plantilla": plantilla,
                "ejemplo": ejemplo,
                "nota": str(f.get("nota", "")),
                "patron": patron,
            }
        )
    return {
        "ruta": str(ruta),
        "prefijo": prefijo,
        "extension": extension,
        "debe_contener": debe_contener,
        "tipos": tipos,
        "formas": formas,
    }


_VIGENTE: dict[str, Catalogo] = {}


def vigente() -> Catalogo:
    """El catalogo del proyecto, leido una vez por ejecucion."""
    if "c" not in _VIGENTE:
        _VIGENTE["c"] = cargar()
    return _VIGENTE["c"]


def leer_nombre(nombre: str, catalogo: Catalogo | None = None) -> Lectura | None:
    """Que dice el nombre, segun la primera forma que calce. None si ninguna calza."""
    cat = catalogo or vigente()
    for forma in cat["formas"]:
        m = forma["patron"].match(nombre)
        if not m:
            continue
        grupos = m.groupdict()
        versiones = [int(v) for v in (grupos.get("version"), grupos.get("version2")) if v]
        return {
            "tipo": cat["tipos"][grupos["tipo"].lower()],
            "version": max(versiones, default=1),
            "reemision": int(grupos.get("reemision") or 0),
            "fecha": grupos.get("fecha") or "",
            "plantilla": forma["plantilla"],
        }
    return None


def es_candidato(href: str, catalogo: Catalogo | None = None) -> bool:
    """¿Este enlace es, por su texto, un ZIP de CMg Real? (aunque su forma sea nueva)."""
    cat = catalogo or vigente()
    h = href.lower()
    return cat["debe_contener"] in h and h.endswith(cat["extension"].lower())
