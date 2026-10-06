"""Bloques horarios de las licitaciones reguladas y los perfiles Solar / NoSolar.

    A: 00:00-07:59 y 23:00-23:59   (9 horas)
    B: 08:00-17:59                 (10 horas)
    C: 18:00-22:59                 (5 horas)

La hora 24 (la segunda 23:00 del dia largo de abril) va al bloque A, igual que la
23. Regla de negocio de ppa-pipeline ADR-002.
"""

from typing import Literal

Bloque = Literal["A", "B", "C"]

HORAS_A = 9
HORAS_B = 10
HORAS_C = 5

#: Tabla hora -> bloque. Se usa como lookup y no como cadena de `if`, para poder
#: vectorizar despues con `Series.map(BLOQUE_POR_HORA)` en vez de `.apply()`.
BLOQUE_POR_HORA: dict[int, Bloque] = {
    **{hora: "A" for hora in range(0, 8)},
    **{hora: "B" for hora in range(8, 18)},
    **{hora: "C" for hora in range(18, 23)},
    23: "A",
    24: "A",  # la hora extra de abril
}


def bloque_de_hora(hora: int) -> Bloque:
    """El bloque de una hora de inicio (0 a 24).

    Se valida la entrada al principio y se falla con un mensaje claro (guard
    clause): un `hora` fuera de rango casi siempre significa que el dato viene en
    base 1 y falta restarle uno.
    """
    if hora not in BLOQUE_POR_HORA:
        raise ValueError(
            f"hora fuera de rango: {hora!r}. Se espera 0 a 24 "
            "(¿el dato viene en base 1 y falta restarle 1?)"
        )
    return BLOQUE_POR_HORA[hora]


def sql_bloque(columna: str = "hora") -> str:
    """`CASE` SQL que traduce hora -> bloque, generado desde `BLOQUE_POR_HORA`.

    Se genera en vez de escribirlo a mano para que no exista una segunda copia de
    la regla: si cambia `BLOQUE_POR_HORA`, este SQL cambia con ella. Lo usan las
    dos fuentes (Maestro y pagina), por eso vive aqui y no en una de ellas.
    """
    casos = " ".join(
        f"WHEN {hora} THEN '{bloque}'" for hora, bloque in sorted(BLOQUE_POR_HORA.items())
    )
    return f"CASE {columna} {casos} ELSE NULL END"


def cmg_solar(promedio_b: float) -> float:
    """Perfil solar: es el promedio del bloque B, sin mas."""
    return promedio_b


def cmg_no_solar(promedio_a: float, promedio_c: float) -> float:
    """Perfil no solar: promedio ponderado de A y C por su cantidad de horas.

        (A x 9 + C x 5) / 14

    Se pondera por horas y no se promedian los promedios, porque A dura 9 horas y
    C solo 5: un promedio simple le daria a C casi el doble del peso que le
    corresponde.
    """
    return (promedio_a * HORAS_A + promedio_c * HORAS_C) / (HORAS_A + HORAS_C)
