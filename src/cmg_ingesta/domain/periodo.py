"""Parseo de periodos que escribe el usuario, a un rango concreto de meses.

Funcion pura: no sabe que hay en la base. El rango disponible se le pasa como
argumento (`primero` y `ultimo`), asi que se puede testear sin tocar nada.

Formatos que acepta:
    "2025"              -> todo 2025
    "2025-03"           -> desde marzo 2025 hasta el ultimo disponible
    "2025-03 a 2025-08" -> ese rango
    "ultimos 6"         -> los ultimos 6 meses disponibles
    ""                  -> todo lo disponible
"""

import re

#: Un mes es (año, mes). Se usa una tupla y no una clase: es un dato, no un objeto.
Mes = tuple[int, int]

_ANIO = re.compile(r"^(\d{4})$")
_MES = re.compile(r"^(\d{4})-(\d{1,2})$")
_RANGO = re.compile(r"^(\d{4})-(\d{1,2})\s*a\s*(\d{4})-(\d{1,2})$", re.IGNORECASE)
_ULTIMOS = re.compile(r"^ultimos?\s+(\d{1,3})$", re.IGNORECASE)


def _validar_mes(anio: int, mes: int) -> Mes:
    if not 1 <= mes <= 12:
        raise ValueError(f"mes fuera de rango: {mes}")
    if not 2000 <= anio <= 2100:
        raise ValueError(f"año fuera de rango: {anio}")
    return (anio, mes)


def indice(mes: Mes) -> int:
    """Convierte (año, mes) a un numero corrido, para poder sumar y restar meses."""
    anio, m = mes
    return anio * 12 + (m - 1)


def desde_indice(n: int) -> Mes:
    """La inversa de `indice`."""
    return (n // 12, n % 12 + 1)


def meses_entre(desde: Mes, hasta: Mes) -> list[Mes]:
    """Todos los meses del rango, inclusive en los dos extremos."""
    if indice(desde) > indice(hasta):
        raise ValueError(f"rango invertido: {desde} es posterior a {hasta}")
    return [desde_indice(n) for n in range(indice(desde), indice(hasta) + 1)]


def parsear_periodo(texto: str, primero: Mes, ultimo: Mes) -> tuple[Mes, Mes]:
    """Interpreta lo que escribio el usuario y devuelve (desde, hasta) concretos.

    `primero` y `ultimo` son el rango disponible en la base, y se usan para
    resolver los formatos abiertos ("", "2025-03", "ultimos 6").

    El resultado se RECORTA al rango disponible: pedir 2019 cuando la base
    empieza en 2021 devuelve el inicio de la base, no un rango vacio.
    """
    texto = texto.strip()

    if not texto:
        return (primero, ultimo)

    if m := _ANIO.match(texto):
        anio = int(m.group(1))
        desde = _validar_mes(anio, 1)
        hasta = _validar_mes(anio, 12)
    elif m := _RANGO.match(texto):
        desde = _validar_mes(int(m.group(1)), int(m.group(2)))
        hasta = _validar_mes(int(m.group(3)), int(m.group(4)))
    elif m := _MES.match(texto):
        desde = _validar_mes(int(m.group(1)), int(m.group(2)))
        hasta = ultimo
    elif m := _ULTIMOS.match(texto):
        n = int(m.group(1))
        if n < 1:
            raise ValueError("la cantidad de meses debe ser al menos 1")
        desde = desde_indice(indice(ultimo) - (n - 1))
        hasta = ultimo
    else:
        raise ValueError(
            f"periodo no reconocido: {texto!r}. Ejemplos validos: "
            "'2025', '2025-03', '2025-03 a 2025-08', 'ultimos 6', o vacio para todo"
        )

    if indice(desde) > indice(hasta):
        raise ValueError(f"rango invertido: {texto!r}")

    # recorte al rango disponible
    desde = desde_indice(max(indice(desde), indice(primero)))
    hasta = desde_indice(min(indice(hasta), indice(ultimo)))
    if indice(desde) > indice(hasta):
        raise ValueError(
            f"el periodo {texto!r} queda fuera de lo disponible "
            f"({primero[0]}-{primero[1]:02d} a {ultimo[0]}-{ultimo[1]:02d})"
        )
    return (desde, hasta)


def formatear(mes: Mes) -> str:
    """(2025, 3) -> '2025-03'."""
    return f"{mes[0]}-{mes[1]:02d}"
