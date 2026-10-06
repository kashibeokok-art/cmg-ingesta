"""Tests del parseo de periodos. El rango disponible se inyecta, no se consulta."""

import pytest

from cmg_ingesta.domain import periodo
from cmg_ingesta.domain.periodo import Mes

PRIMERO: Mes = (2021, 1)
ULTIMO: Mes = (2026, 7)


def parsear(texto: str) -> tuple[Mes, Mes]:
    return periodo.parsear_periodo(texto, PRIMERO, ULTIMO)


def test_vacio_es_todo_lo_disponible() -> None:
    assert parsear("") == (PRIMERO, ULTIMO)
    assert parsear("   ") == (PRIMERO, ULTIMO)


def test_un_año_completo() -> None:
    assert parsear("2025") == ((2025, 1), (2025, 12))


def test_un_mes_abre_hasta_el_final() -> None:
    assert parsear("2025-03") == ((2025, 3), ULTIMO)


def test_mes_con_un_digito() -> None:
    assert parsear("2025-3") == ((2025, 3), ULTIMO)


def test_rango_explicito() -> None:
    assert parsear("2025-03 a 2025-08") == ((2025, 3), (2025, 8))


def test_rango_sin_espacios() -> None:
    assert parsear("2025-03a2025-08") == ((2025, 3), (2025, 8))


def test_rango_interanual() -> None:
    assert parsear("2024-07 a 2025-06") == ((2024, 7), (2025, 6))


def test_ultimos_n_meses() -> None:
    assert parsear("ultimos 6") == ((2026, 2), ULTIMO)
    assert parsear("ultimo 1") == (ULTIMO, ULTIMO)


def test_el_año_en_curso_se_recorta_a_lo_disponible() -> None:
    """2026 solo llega hasta julio en la base: no se pide hasta diciembre."""
    assert parsear("2026") == ((2026, 1), (2026, 7))


def test_un_año_anterior_al_inicio_se_recorta() -> None:
    """2020 no existe en la base; 2020-01 a 2021-06 arranca donde hay datos."""
    assert parsear("2020-01 a 2021-06") == ((2021, 1), (2021, 6))


def test_ultimos_mas_que_lo_disponible_se_recorta() -> None:
    assert parsear("ultimos 999") == (PRIMERO, ULTIMO)


@pytest.mark.parametrize("texto", ["2025-13", "2025-00", "1999", "2101-01", "2025-03 a 2025-13"])
def test_mes_o_año_invalido_falla(texto: str) -> None:
    with pytest.raises(ValueError, match="fuera de rango"):
        parsear(texto)


def test_rango_invertido_falla() -> None:
    with pytest.raises(ValueError, match="invertido"):
        parsear("2025-08 a 2025-03")


@pytest.mark.parametrize(
    "texto", ["ayer", "2025/03", "marzo 2025", "ultimos", "ultimos -3", "2025-03-15"]
)
def test_texto_no_reconocido_falla(texto: str) -> None:
    with pytest.raises(ValueError, match="no reconocido|fuera de rango"):
        parsear(texto)


def test_periodo_completamente_fuera_de_lo_disponible_falla() -> None:
    with pytest.raises(ValueError, match="fuera de lo disponible"):
        parsear("2018-01 a 2019-12")


def test_indice_y_desde_indice_son_inversos() -> None:
    for anio in (2021, 2025, 2026):
        for mes in range(1, 13):
            assert periodo.desde_indice(periodo.indice((anio, mes))) == (anio, mes)


def test_meses_entre_cuenta_bien() -> None:
    assert periodo.meses_entre((2025, 1), (2025, 1)) == [(2025, 1)]
    assert len(periodo.meses_entre((2025, 1), (2025, 12))) == 12
    assert len(periodo.meses_entre((2024, 7), (2025, 6))) == 12  # interanual


def test_meses_entre_cruza_el_año() -> None:
    assert periodo.meses_entre((2024, 11), (2025, 2)) == [
        (2024, 11),
        (2024, 12),
        (2025, 1),
        (2025, 2),
    ]


def test_meses_entre_invertido_falla() -> None:
    with pytest.raises(ValueError, match="invertido"):
        periodo.meses_entre((2025, 6), (2025, 1))


def test_formatear() -> None:
    assert periodo.formatear((2025, 3)) == "2025-03"
    assert periodo.formatear((2025, 12)) == "2025-12"
