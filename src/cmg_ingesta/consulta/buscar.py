"""Busqueda aproximada de barras: "santa elvira 13" encuentra STA.ELVIRA____013.

Los nombres del Coordinador son codigos de 17 caracteres: abreviados, con puntos
y relleno de guiones bajos, y el voltaje con ceros a la izquierda. Nadie los
escribe asi. La busqueda compara por PALABRAS:

| Escrito      | En la barra | Por que calza                                     |
|--------------|-------------|---------------------------------------------------|
| elvira       | ELVIRA      | igual                                             |
| elv          | ELVIRA      | comienzo de la palabra                            |
| 13           | 013         | el mismo numero                                   |
| santa, cerro | STA, C      | abreviatura: sus letras, en orden, desde la 1ra   |
| quelon       | QUELLON     | parecido (error de tipeo)                         |

Las abreviaturas son las del propio Coordinador, medidas sobre 1.436 barras:
C.NAVIA (Cerro Navia), D.ALMAGRO (Diego Almagro), A.JAHUEL (Alto Jahuel), STA, NVA.
Cada palabra escrita busca su mejor pareja en la barra; el puntaje es el promedio.
Siempre se entregan las 10 mas parecidas, de mayor a menor, con su porcentaje:
quien busca elige. La busqueda nunca elige sola (pedido del usuario).
"""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import NamedTuple

#: Desde cuanto parecido (0 a 1) dos palabras se consideran la misma.
UMBRAL = 0.72

#: Palabras cortas que son palabras y no abreviaturas: EL no abrevia ELVIRA.
NO_ABREVIAN = frozenset({"EL", "LA", "LO", "LOS", "LAS", "DE", "DEL", "Y", "TAP", "SUR", "RIO"})


def palabras(texto: str) -> list[str]:
    """'Sta. Elvira__013' -> ['STA', 'ELVIRA', '013']. Sin tildes ni Ñ."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return [p for p in re.split(r"[^A-Z0-9]+", sin_tildes.upper()) if p]


def _es_abreviatura(corta: str, larga: str) -> bool:
    """STA de SANTA, NVA de NUEVA, C de CERRO: las letras en orden, desde la primera."""
    if corta in NO_ABREVIAN or len(corta) > 3 or len(corta) >= len(larga) or corta[0] != larga[0]:
        return False
    resto = iter(larga[1:])
    return all(letra in resto for letra in corta[1:])


def _parecido(escrita: str, de_barra: str, blando: bool = False, sola: bool = False) -> float:
    """`blando`: un numero que no calza no descarta (sugerencias: el voltaje mal puesto).

    `sola`: la consulta es UNA palabra; ahi una inicial (C de CERRO) no basta, porque
    "elvira" traeria todas las barras que empiezan con E.
    """
    if escrita == de_barra:
        return 1.0
    if escrita.isdigit() or de_barra.isdigit():
        if escrita.isdigit() and de_barra.isdigit() and int(escrita) == int(de_barra):
            return 1.0
        return 0.5 if blando and escrita.isdigit() else 0.0
    if de_barra.startswith(escrita):
        return 0.9 if len(escrita) >= 2 else 0.75
    if _es_abreviatura(de_barra, escrita):
        # una inicial sola calza con muchas palabras: vale menos que 2-3 letras
        if len(de_barra) >= 2:
            return 0.85
        return 0.0 if sola else 0.74
    return SequenceMatcher(None, escrita, de_barra).ratio()


class Coincidencia(NamedTuple):
    barra: str
    porcentaje: int  # 0 a 100, lo que se muestra al usuario
    todas: bool  # True si cada palabra escrita encontro su pareja en la barra


def coincidencia(consulta: str, barra: str) -> Coincidencia:
    """Que tanto se parece la barra a lo escrito: el promedio del mejor parecido por palabra.

    Un numero que no calza (el voltaje) cuenta 0,5 y no 0: "sta elvira 220" sigue
    trayendo STA.ELVIRA en 13 kV, debajo de las que calzan en todo.
    """
    escritas = palabras(consulta)
    de_barra = palabras(barra)
    sola = len(escritas) == 1
    mejores = [max((_parecido(e, b, True, sola) for b in de_barra), default=0.0) for e in escritas]
    # a igual parecido, primero la que contiene el texto tal cual se escribio
    junto = "".join(escritas)
    valor = sum(mejores) / len(mejores) + (0.05 if junto in "".join(de_barra) else 0.0)
    return Coincidencia(barra, round(100 * min(valor, 1.0)), min(mejores) >= UMBRAL)


def buscar(consulta: str, barras: list[str], limite: int = 10) -> list[Coincidencia]:
    """Las `limite` barras mas parecidas, de mayor a menor coincidencia. NUNCA elige una.

    Primero las que calzan en todas las palabras; despues el resto por parecido,
    hasta completar. Sin texto, las primeras en orden alfabetico.
    """
    if not palabras(consulta):
        return [Coincidencia(b, 0, False) for b in barras[:limite]]
    todas = [coincidencia(consulta, b) for b in barras]
    # a igual puntaje: la de menos palabras extra (P.MONTT antes que NVA_P.MONTT)
    todas.sort(key=lambda c: (not c.todas, -c.porcentaje, len(palabras(c.barra)), c.barra))
    return [c for c in todas[:limite] if c.porcentaje > 0]
