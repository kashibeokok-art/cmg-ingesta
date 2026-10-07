"""Los dialogos del menu: elegir barras, periodo, formatos, confirmar.

Es la unica parte del programa que pregunta, igual que `cli.py` es la unica que
imprime. Pero a diferencia de `CMG_Build/src/core.py: elegir_barras()`, que
llamaba a `input()` directo, aqui la entrada y la salida se **inyectan**
(dependency injection): `Consola` recibe las funciones de leer y escribir.

En el programa real son `input` y `print`. En los tests son una lista de
respuestas escritas de antemano y una lista que junta lo impreso. Asi cada
dialogo se prueba entero, sin teclado.

Convenciones que se mantienen del programa anterior, porque el usuario ya las sabe:
    Enter vacio   termina (o cancela, si todavia no se eligio nada)
    v             vuelve al paso anterior
    quitar        saca una barra de la seleccion
    1,3 / todas   elige varias de la lista de coincidencias
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from cmg_ingesta.domain import periodo
from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.menu import busqueda
from cmg_ingesta.reportes.exportar import FORMATOS, Formato


class Nav(Enum):
    """Respuestas que no son un dato sino una orden de navegacion.

    Un `Enum` de un solo valor es la forma tipada de un centinela (sentinel):
    mypy obliga a quien llama a revisar `if r is Nav.VOLVER` antes de usar el dato.
    El programa anterior devolvia el texto "back", que se podia confundir con una
    barra llamada asi y que mypy no puede distinguir de un `str` cualquiera.
    """

    VOLVER = "volver"


Volver = Literal[Nav.VOLVER]

_PALABRAS_VOLVER = ("v", "volver", "atras")
_PALABRAS_QUITAR = ("quitar", "q", "-")


@dataclass
class Consola:
    """Entrada y salida del menu. Por defecto, el teclado y la pantalla."""

    leer: Callable[[str], str] = input
    escribir: Callable[[str], None] = print
    #: Cuantas coincidencias mostrar por busqueda.
    limite: int = 15

    def preguntar(self, texto: str) -> str | None:
        """Lee una linea. None si se cerro la entrada (Ctrl+Z, fin del script)."""
        try:
            return self.leer(texto).strip()
        except EOFError:
            return None

    def decir(self, texto: str = "") -> None:
        self.escribir(texto)

    def confirmar(self, texto: str) -> bool:
        respuesta = self.preguntar(f"{texto} [s/N]: ")
        return respuesta is not None and respuesta.lower() in ("s", "si", "sí")


def _mostrar_lista(consola: Consola, opciones: list[str], ya: list[str]) -> None:
    for i, o in enumerate(opciones, 1):
        marca = "  (ya)" if o in ya else ""
        consola.decir(f"    {i:2d}) {o}{marca}")


def elegir_una(consola: Consola, opciones: list[str], etiqueta: str) -> str | None | Volver:
    """Busca y elige UNA opcion. None = cancelado; Nav.VOLVER = paso anterior."""
    while True:
        consulta = consola.preguntar(f"\n  {etiqueta} (texto a buscar, Enter cancela, v volver): ")
        if not consulta:
            return None
        if consulta.lower() in _PALABRAS_VOLVER:
            return Nav.VOLVER
        if hallada := busqueda.exacta(consulta, opciones):
            return hallada

        candidatas = busqueda.buscar(consulta, opciones, consola.limite)
        if not candidatas:
            consola.decir("  Sin coincidencias. Prueba con otra palabra.")
            continue
        if len(candidatas) == 1:
            consola.decir(f"  -> {candidatas[0]}")
            return candidatas[0]

        consola.decir(f"  Coincidencias para '{consulta}':")
        _mostrar_lista(consola, candidatas, [])
        sel = consola.preguntar("  Numero (Enter para buscar otra vez): ")
        if not sel:
            continue
        indices = busqueda.leer_numeros(sel, len(candidatas))
        if indices is None or len(indices) != 1:
            consola.decir("  Elige un solo numero de la lista.")
            continue
        return candidatas[indices[0]]


def elegir_varias(
    consola: Consola, opciones: list[str], etiqueta: str, previas: list[str] | None = None
) -> list[str] | Volver:
    """Arma una seleccion de varias opciones con busquedas sucesivas.

    Lista vacia = cancelado. `previas` permite volver a este paso sin perder lo
    elegido.
    """
    seleccion = list(previas or [])
    while True:
        if seleccion:
            muestra = ", ".join(seleccion[:6]) + (" ..." if len(seleccion) > 6 else "")
            consola.decir(f"\n  Seleccionadas ({len(seleccion)}): {muestra}")
        consulta = consola.preguntar(
            f"  {etiqueta} (texto a buscar | Enter termina | quitar | v volver): "
        )
        if not consulta:
            return seleccion
        if consulta.lower() in _PALABRAS_VOLVER:
            return Nav.VOLVER
        if consulta.lower() in _PALABRAS_QUITAR:
            _quitar(consola, seleccion)
            continue
        if hallada := busqueda.exacta(consulta, opciones):
            if hallada not in seleccion:
                seleccion.append(hallada)
            continue

        candidatas = busqueda.buscar(consulta, opciones, consola.limite)
        if not candidatas:
            consola.decir("  Sin coincidencias. Prueba con otra palabra.")
            continue
        if len(candidatas) == 1:
            if candidatas[0] not in seleccion:
                seleccion.append(candidatas[0])
            consola.decir(f"  + {candidatas[0]}")
            continue
        consola.decir(f"  Coincidencias para '{consulta}':")
        _mostrar_lista(consola, candidatas, seleccion)
        sel = consola.preguntar("  Numeros (ej 1,3 o 2-5), 'todas', o Enter para buscar otra: ")
        if not sel:
            continue
        indices = busqueda.leer_numeros(sel, len(candidatas))
        if indices is None:
            consola.decir("  Seleccion invalida.")
            continue
        for i in indices:
            if candidatas[i] not in seleccion:
                seleccion.append(candidatas[i])


def _quitar(consola: Consola, seleccion: list[str]) -> None:
    if not seleccion:
        consola.decir("  No hay nada que quitar.")
        return
    _mostrar_lista(consola, seleccion, [])
    sel = consola.preguntar("  Numeros a quitar (Enter cancela): ")
    if not sel:
        return
    indices = busqueda.leer_numeros(sel, len(seleccion))
    if indices is None:
        consola.decir("  Seleccion invalida.")
        return
    quitadas = [seleccion[i] for i in indices]
    for q in quitadas:
        seleccion.remove(q)
    consola.decir(f"  Quitadas: {', '.join(quitadas)}")


def pedir_periodo(consola: Consola, primero: Mes, ultimo: Mes) -> tuple[Mes, Mes] | None | Volver:
    """Pide un periodo y lo valida con `domain.periodo`. Reintenta si no se entiende."""
    consola.decir(
        f"\n  Disponible: {periodo.formatear(primero)} a {periodo.formatear(ultimo)}"
        "\n  Ejemplos: 2024 | 2024-03 | 2024-03 a 2024-08 | ultimos 6 | Enter = todo | v volver"
    )
    while True:
        texto = consola.preguntar("  Periodo: ")
        if texto is None:
            return None
        if texto.lower() in _PALABRAS_VOLVER:
            return Nav.VOLVER
        try:
            desde, hasta = periodo.parsear_periodo(texto, primero, ultimo)
        except ValueError as e:
            consola.decir(f"  {e}")
            continue
        consola.decir(f"  -> {periodo.formatear(desde)} a {periodo.formatear(hasta)}")
        return desde, hasta


_ALIAS_FORMATO: dict[str, tuple[Formato, ...]] = {
    "1": ("excel",),
    "excel": ("excel",),
    "xlsx": ("excel",),
    "2": ("csv",),
    "csv": ("csv",),
    "3": ("parquet",),
    "parquet": ("parquet",),
    "todos": FORMATOS,
}


def leer_formatos(texto: str) -> tuple[Formato, ...] | None:
    """'1,3' o 'excel csv' -> formatos, en el orden canonico. None si no se entiende."""
    elegidos: set[Formato] = set()
    for trozo in texto.replace(";", ",").replace(" ", ",").split(","):
        trozo = trozo.strip().lower()
        if not trozo:
            continue
        if trozo not in _ALIAS_FORMATO:
            return None
        elegidos.update(_ALIAS_FORMATO[trozo])
    return tuple(f for f in FORMATOS if f in elegidos) or None


def pedir_formatos(consola: Consola) -> tuple[Formato, ...] | None | Volver:
    """Pide uno o varios formatos. Enter = Excel, el mas usado."""
    consola.decir("\n  Formatos: [1] Excel  [2] CSV  [3] Parquet   (varios: 1,2 | todos)")
    while True:
        texto = consola.preguntar("  Formato (Enter = Excel, v volver): ")
        if texto is None:
            return None
        if texto.lower() in _PALABRAS_VOLVER:
            return Nav.VOLVER
        if not texto:
            return ("excel",)
        formatos = leer_formatos(texto)
        if formatos is None:
            consola.decir("  Opcion invalida.")
            continue
        return formatos
