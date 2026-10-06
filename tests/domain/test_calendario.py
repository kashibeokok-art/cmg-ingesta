"""Tests del calendario. Los casos NO son inventados: salen medidos del estudio E0.

Cada fecha de abril y septiembre fue verificada contra CMG_DB y contra la API del
CEN (ver CLAUDE.md 4.5.1 y 4.5.4).
"""

from datetime import date, timedelta

import pytest

from cmg_ingesta.domain import calendario

# Dias de 25 horas, medidos: CMG_DB tiene hora=24 en estas fechas (2021, 2022 y
# 2024 estan corridos un dia en la base; aqui va la fecha CORRECTA, la del
# primer sabado, que es la que confirma zoneinfo).
DIAS_LARGOS = [
    date(2021, 4, 3),
    date(2022, 4, 2),
    date(2023, 4, 1),
    date(2024, 4, 6),
    date(2025, 4, 5),
    date(2026, 4, 4),
]

# Dias de 23 horas, medidos: CMG_DB trae la hora 0 en CERO en estas fechas.
DIAS_CORTOS = [
    date(2021, 9, 5),
    date(2022, 9, 11),
    date(2023, 9, 3),
    date(2024, 9, 8),
    date(2025, 9, 7),
]

DIAS_NORMALES = [
    date(2025, 6, 15),
    date(2026, 1, 1),
    date(2025, 12, 31),
    date(2024, 2, 29),  # año bisiesto
]


@pytest.mark.parametrize("dia", DIAS_NORMALES)
def test_dia_normal(dia: date) -> None:
    assert calendario.horas_del_dia(dia) == 24
    assert calendario.cuartos_esperados(dia) == 96
    assert calendario.horas_inexistentes(dia) == []
    assert calendario.horas_esperadas(dia) == list(range(24))
    assert not calendario.es_dia_largo(dia)
    assert not calendario.es_dia_corto(dia)


@pytest.mark.parametrize("dia", DIAS_LARGOS)
def test_dia_largo_abril(dia: date) -> None:
    """25 horas: la 23:00 se repite y la segunda recibe el indice 24."""
    assert calendario.horas_del_dia(dia) == 25
    assert calendario.cuartos_esperados(dia) == 100
    assert calendario.es_dia_largo(dia)
    # ninguna hora falta: el dia tiene de mas, no de menos
    assert calendario.horas_inexistentes(dia) == []
    assert calendario.horas_esperadas(dia) == [*range(24), calendario.HORA_EXTRA]


@pytest.mark.parametrize("dia", DIAS_LARGOS)
def test_dia_largo_es_primer_sabado_de_abril(dia: date) -> None:
    """La regla de negocio: el dia de 25 horas es el primer sabado de abril."""
    assert dia.month == 4
    assert dia.weekday() == 5, "debe ser sabado"
    assert dia.day <= 7, "debe ser el primero del mes"


@pytest.mark.parametrize("dia", DIAS_CORTOS)
def test_dia_corto_septiembre(dia: date) -> None:
    """23 horas: el reloj salta de 00:00 a 01:00, la hora 0 no existe."""
    assert calendario.horas_del_dia(dia) == 23
    assert calendario.cuartos_esperados(dia) == 92
    assert calendario.es_dia_corto(dia)
    assert calendario.horas_inexistentes(dia) == [0]
    assert calendario.horas_esperadas(dia) == list(range(1, 24))
    assert 0 not in calendario.horas_esperadas(dia)


@pytest.mark.parametrize("dia", DIAS_LARGOS + DIAS_CORTOS)
def test_los_vecinos_de_una_transicion_son_normales(dia: date) -> None:
    """Prueba de borde: el dia antes y el dia despues duran 24 horas."""
    for delta in (-1, 1):
        vecino = dia + timedelta(days=delta)
        assert calendario.cuartos_esperados(vecino) == 96, f"fallo en {vecino}"


@pytest.mark.parametrize("dia", DIAS_NORMALES + DIAS_LARGOS + DIAS_CORTOS)
def test_cuartos_siempre_son_cuatro_por_hora(dia: date) -> None:
    """Invariante: las tres funciones no pueden contradecirse entre si."""
    esperadas = calendario.horas_esperadas(dia)
    assert len(esperadas) == calendario.horas_del_dia(dia)
    assert calendario.cuartos_esperados(dia) == len(esperadas) * 4


def test_un_año_completo_suma_las_horas_del_calendario() -> None:
    """2025 tiene 365 dias, pero 8.760 - 1 + 1 = 8.760 horas: se compensan."""
    dia = date(2025, 1, 1)
    total_horas = 0
    total_cuartos = 0
    dias = 0
    while dia.year == 2025:
        total_horas += calendario.horas_del_dia(dia)
        total_cuartos += calendario.cuartos_esperados(dia)
        dias += 1
        dia += timedelta(days=1)

    assert dias == 365
    # el dia largo aporta +1 y el corto -1: se anulan
    assert total_horas == 365 * 24
    assert total_cuartos == 365 * 96


def test_solo_hay_un_dia_largo_y_uno_corto_por_año() -> None:
    """Si algun año tuviera dos, la validacion de la ingesta estaria mal pensada."""
    for anio in range(2021, 2031):
        largos = []
        cortos = []
        dia = date(anio, 1, 1)
        while dia.year == anio:
            if calendario.es_dia_largo(dia):
                largos.append(dia)
            if calendario.es_dia_corto(dia):
                cortos.append(dia)
            dia += timedelta(days=1)
        assert len(largos) == 1, f"{anio}: {largos}"
        assert len(cortos) == 1, f"{anio}: {cortos}"
