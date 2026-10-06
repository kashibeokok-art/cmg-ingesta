"""Tests de bloques horarios. Los bordes son donde nacen los bugs."""

import pytest

from cmg_ingesta.domain import bloques
from cmg_ingesta.domain.bloques import Bloque


@pytest.mark.parametrize(
    ("hora", "esperado"),
    [
        (0, "A"),
        (7, "A"),  # borde: ultima hora de A por la mañana
        (8, "B"),  # borde: primera de B
        (12, "B"),
        (17, "B"),  # borde: ultima de B
        (18, "C"),  # borde: primera de C
        (22, "C"),  # borde: ultima de C
        (23, "A"),  # borde: la 23 vuelve a A
        (24, "A"),  # la hora extra de abril
    ],
)
def test_bloque_de_hora(hora: int, esperado: Bloque) -> None:
    assert bloques.bloque_de_hora(hora) == esperado


@pytest.mark.parametrize("hora", [-1, 25, 100, 999])
def test_hora_fuera_de_rango_falla(hora: int) -> None:
    """Fail fast: una hora invalida no puede devolver un bloque por defecto."""
    with pytest.raises(ValueError, match="fuera de rango"):
        bloques.bloque_de_hora(hora)


def test_la_tabla_cubre_las_25_horas() -> None:
    assert sorted(bloques.BLOQUE_POR_HORA) == list(range(25))


def test_las_horas_de_cada_bloque_suman_24() -> None:
    """Invariante: A + B + C = 24 horas de un dia normal."""
    assert bloques.HORAS_A + bloques.HORAS_B + bloques.HORAS_C == 24


def test_cantidad_de_horas_por_bloque_calza_con_la_tabla() -> None:
    """La tabla y las constantes no pueden decir cosas distintas."""
    # se excluye la hora 24, que es extraordinaria
    normales = {h: b for h, b in bloques.BLOQUE_POR_HORA.items() if h < 24}
    conteo = {b: sum(1 for x in normales.values() if x == b) for b in ("A", "B", "C")}
    assert conteo["A"] == bloques.HORAS_A
    assert conteo["B"] == bloques.HORAS_B
    assert conteo["C"] == bloques.HORAS_C


def test_cmg_solar_es_el_bloque_b() -> None:
    assert bloques.cmg_solar(61.5) == 61.5


def test_cmg_no_solar_pondera_por_horas() -> None:
    """(A x 9 + C x 5) / 14, no el promedio simple de A y C."""
    resultado = bloques.cmg_no_solar(promedio_a=100.0, promedio_c=200.0)
    assert resultado == pytest.approx((100 * 9 + 200 * 5) / 14)
    # y es distinto del promedio simple, que seria 150
    assert resultado != pytest.approx(150.0)


def test_cmg_no_solar_con_valores_iguales_da_el_mismo_valor() -> None:
    """Si A y C valen lo mismo, cualquier ponderacion da ese valor."""
    assert bloques.cmg_no_solar(80.0, 80.0) == pytest.approx(80.0)
