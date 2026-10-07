"""Busqueda difusa (fuzzy search) y lectura de selecciones, como funciones puras.

Viene de `CMG_Build/src/core.py: buscar_barras()`, que ya funcionaba bien con los
usuarios. Se conserva su orden de prioridad y se le agrega un nivel:

    3.0      igual al nombre (ignorando mayusculas y separadores)
    2.x      el texto aparece tal cual dentro del nombre
    1.x      todas las palabras aparecen, en cualquier orden   <- nuevo
    0.5-1.0  parecido segun `difflib` (tolera errores de tipeo)

El nivel nuevo resuelve "elvira 13" -> `STA.ELVIRA____013`: ni es subcadena
("ELVIRA 013") ni se parece lo suficiente para `difflib`.

Dos cambios mas respecto del legado, vistos al probar con la base real:

- **Se ignoran los acentos y la Ñ.** La base tiene `PENABLANCA____013` y
  `PEÑABLANCA____013` como barras distintas; buscar "penablanca" debe mostrar
  las dos, no solo la que se escribio igual.
- **Lo aproximado solo aparece si no hubo coincidencia por texto.** Buscando
  "blancas 13", `difflib` agregaba ANCOA o LAJA al final de la lista: ruido.

Nada de este modulo pregunta ni imprime: recibe texto y devuelve datos.
"""

import difflib
import re
import unicodedata

#: Debajo de este parecido, `difflib` da coincidencias que confunden mas que ayudan.
PARECIDO_MINIMO = 0.5

#: Desde este puntaje la coincidencia es por texto, no por parecido.
POR_TEXTO = 1.0

_NO_ALFANUMERICO = re.compile(r"[^0-9A-Z]+")


def normalizar(texto: str) -> str:
    """Mayusculas, sin acentos, y cualquier separador a un solo espacio.

    `STA.ELVIRA____013` -> `STA ELVIRA 013`. Asi el usuario no tiene que adivinar
    cuantos guiones bajos tiene el nombre.

    Los acentos se quitan descomponiendo cada letra (NFKD: `Ñ` = `N` + tilde) y
    descartando las marcas. Sin esto, la `Ñ` caia en el separador: `PEÑABLANCA`
    quedaba como `PE ABLANCA`.
    """
    descompuesto = unicodedata.normalize("NFKD", texto.upper())
    sin_marcas = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return _NO_ALFANUMERICO.sub(" ", sin_marcas).strip()


def puntaje(consulta: str, opcion: str) -> float:
    """Que tan bien calza `opcion` con `consulta`. 0 = no calza."""
    q = normalizar(consulta)
    n = normalizar(opcion)
    if not q:
        return 0.0
    if q == n:
        return 3.0
    if q in n:
        # a igual coincidencia, gana el nombre mas corto (el mas especifico)
        return 2.0 + len(q) / len(n)
    palabras = n.split()
    if all(any(p in palabra for palabra in palabras) for p in q.split()):
        # tope bajo 2.0: nunca debe ganarle a una subcadena literal
        return 1.0 + min(len(q) / len(n), 0.99)
    parecido = difflib.SequenceMatcher(None, q, n).ratio()
    return parecido if parecido >= PARECIDO_MINIMO else 0.0


def buscar(consulta: str, opciones: list[str], limite: int = 15) -> list[str]:
    """Las `limite` opciones que mejor calzan, de mejor a peor.

    Si alguna calza por texto, se descartan las que solo se parecen. El empate se
    rompe por orden alfabetico, para que el resultado sea estable.
    """
    puntuadas = [(puntaje(consulta, o), o) for o in opciones]
    utiles = [(p, o) for p, o in puntuadas if p > 0]
    if any(p >= POR_TEXTO for p, _ in utiles):
        utiles = [(p, o) for p, o in utiles if p >= POR_TEXTO]
    utiles.sort(key=lambda x: (-x[0], x[1]))
    return [o for _, o in utiles[:limite]]


def exacta(consulta: str, opciones: list[str]) -> str | None:
    """La opcion escrita completa, sin importar mayusculas. None si no hay."""
    buscada = consulta.strip().upper()
    for o in opciones:
        if o.upper() == buscada:
            return o
    return None


def leer_numeros(texto: str, cantidad: int) -> list[int] | None:
    """Interpreta "1,3", "2-5", "1 4 7" o "todas" como posiciones de una lista.

    Devuelve indices base 0, sin repetir y en el orden escrito. None si algo no se
    entiende o se sale de la lista: es mejor pedir de nuevo que elegir de mas.
    """
    texto = texto.strip().lower()
    if texto in ("todas", "todos", "*"):
        return list(range(cantidad))

    elegidos: list[int] = []
    for trozo in re.split(r"[,;\s]+", texto):
        if not trozo:
            continue
        if m := re.fullmatch(r"(\d+)-(\d+)", trozo):
            inicio, fin = int(m.group(1)), int(m.group(2))
            if inicio > fin:
                return None
            numeros = list(range(inicio, fin + 1))
        elif trozo.isdigit():
            numeros = [int(trozo)]
        else:
            return None
        for n in numeros:
            if not 1 <= n <= cantidad:
                return None
            if n - 1 not in elegidos:
                elegidos.append(n - 1)
    return elegidos or None
