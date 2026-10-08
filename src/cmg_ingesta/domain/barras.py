"""Barras que cambiaron de nombre: la tabla de equivalencias (alias) y su SQL.

En 2023-06 la fuente dejo de escribir la Ñ en los nombres de barra: hasta 2023-05
publica `PEÑABLANCA____013` y desde 2023-06 `PENABLANCA____013`. LASARAÑAS hizo
el mismo cambio en 2024-07. Sin esta tabla, cada una aparece como dos barras
distintas y una serie 2021-2024 queda partida en dos.

Evidencia (2026-10-08, sobre la Silver migrada): en los 20 pares el nombre con Ñ
termina el ultimo dia del mes anterior al corte, el nombre sin Ñ empieza el
primero del mes siguiente, y **no hay ni un intervalo con los dos nombres a la
vez**. Es un cambio de nombre, no dos barras. Confirmado por el usuario.

El nombre oficial es el **sin Ñ**, que es como publica hoy la pagina del CEN: lo
nuevo entra tal cual y solo se renombra el historico.

Es una lista **explicita** y no la regla generica "Ñ -> N": una barra nueva con Ñ
tiene que revisarse antes de unirla con otra (mismo criterio de ADR-H05).
"""

#: Nombre antiguo -> nombre oficial.
RENOMBRES: dict[str, str] = {
    "CAÑETE________023": "CANETE________023",
    "CAÑETE________066": "CANETE________066",
    "CHAPIQUIÑA____013": "CHAPIQUINA____013",
    "CHAPIQUIÑA____066": "CHAPIQUINA____066",
    "CHAÑARAL______023": "CHANARAL______023",
    "DEGAÑ_________013": "DEGAN_________013",
    "DEGAÑ_________110": "DEGAN_________110",
    "HUALAÑE_______013": "HUALANE_______013",
    "HUALAÑE_______066": "HUALANE_______066",
    "LASARAÑAS_____013": "LASARANAS_____013",  # cambia en 2024-07, no en 2023-06
    "LASARAÑAS_____066": "LASARANAS_____066",
    "LASARAÑAS_____110": "LASARANAS_____110",
    "PEÑABLANCA____013": "PENABLANCA____013",
    "PEÑABLANCA____110": "PENABLANCA____110",
    "PIÑATAS_______013": "PINATAS_______013",
    "PIÑATAS_______066": "PINATAS_______066",
    "REÑACA________013": "RENACA________013",
    "REÑACA________110": "RENACA________110",
    "TAP.REÑACA____110": "TAP.RENACA____110",
    "TAP_CHAÑARES__110": "TAP_CHANARES__110",
}


def canonico(barra: str) -> str:
    """El nombre oficial de una barra. Si no cambio de nombre, el mismo."""
    return RENOMBRES.get(barra, barra)


def _lit(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


def sql_canonico(columna: str = "barra") -> str:
    """`CASE` SQL que aplica `RENOMBRES`, generado desde la tabla.

    Igual que `bloques.sql_bloque`: una sola copia de la regla, que usan las dos
    fuentes al mapear al esquema canonico.
    """
    casos = " ".join(
        f"WHEN {_lit(viejo)} THEN {_lit(nuevo)}" for viejo, nuevo in sorted(RENOMBRES.items())
    )
    return f"CASE {columna} {casos} ELSE {columna} END"
