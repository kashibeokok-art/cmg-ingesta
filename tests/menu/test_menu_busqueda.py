"""Tests de la busqueda difusa y de la lectura de selecciones."""

import pytest

from cmg_ingesta.menu import busqueda

BARRAS = [
    "A.BLANCAS_____013",
    "A.BLANCAS_____110",
    "ALTO_JAHUEL___220",
    "STA.ELVIRA____013",
    "QUELLON_______013",
    "LATORRE_______066",
]


def test_normalizar_colapsa_separadores() -> None:
    assert busqueda.normalizar("STA.ELVIRA____013") == "STA ELVIRA 013"
    assert busqueda.normalizar("  a-blancas  ") == "A BLANCAS"


def test_igual_ignorando_separadores_gana() -> None:
    assert busqueda.buscar("sta elvira 013", BARRAS)[0] == "STA.ELVIRA____013"


def test_subcadena_trae_todas_las_que_la_contienen() -> None:
    assert busqueda.buscar("blancas", BARRAS) == ["A.BLANCAS_____013", "A.BLANCAS_____110"]


def test_palabras_en_cualquier_orden() -> None:
    """'elvira 13' no es subcadena de 'STA ELVIRA 013': lo resuelve el nivel de palabras."""
    assert busqueda.buscar("elvira 13", BARRAS)[0] == "STA.ELVIRA____013"
    assert busqueda.buscar("013 quellon", BARRAS)[0] == "QUELLON_______013"


def test_tolera_errores_de_tipeo() -> None:
    assert busqueda.buscar("quelon 013", BARRAS)[0] == "QUELLON_______013"


def test_subcadena_le_gana_a_palabras_sueltas() -> None:
    assert busqueda.puntaje("BLANCAS 013", "A.BLANCAS_____013") > busqueda.puntaje(
        "013 BLANCAS", "A.BLANCAS_____013"
    )


def test_sin_coincidencias() -> None:
    assert busqueda.buscar("zzzz", BARRAS) == []
    assert busqueda.buscar("   ", BARRAS) == []


def test_respeta_el_limite() -> None:
    assert len(busqueda.buscar("0", BARRAS, limite=2)) == 2


def test_exacta_ignora_mayusculas() -> None:
    assert busqueda.exacta("a.blancas_____013", BARRAS) == "A.BLANCAS_____013"
    assert busqueda.exacta("blancas", BARRAS) is None


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1", [0]),
        ("1,3", [0, 2]),
        ("3 1", [2, 0]),
        ("2-4", [1, 2, 3]),
        ("1, 2-3, 2", [0, 1, 2]),
        ("todas", [0, 1, 2, 3, 4]),
    ],
)
def test_leer_numeros_validos(texto: str, esperado: list[int]) -> None:
    assert busqueda.leer_numeros(texto, 5) == esperado


@pytest.mark.parametrize("texto", ["0", "6", "a", "4-2", "1,x", "", "2-9"])
def test_leer_numeros_invalidos(texto: str) -> None:
    """Fuera de rango o ilegible: None, nunca una seleccion parcial."""
    assert busqueda.leer_numeros(texto, 5) is None


def test_ignora_acentos_y_la_enie() -> None:
    """En la base real conviven PENABLANCA y PEÑABLANCA: hay que ver las dos."""
    opciones = ["PENABLANCA____013", "PEÑABLANCA____013", "CASABLANCA____013"]
    assert busqueda.normalizar("PEÑABLANCA____013") == "PENABLANCA 013"
    assert busqueda.buscar("penablanca", opciones) == opciones[:2]
    assert busqueda.buscar("peñablanca", opciones) == opciones[:2]


def test_lo_aproximado_no_se_mezcla_con_coincidencias_por_texto() -> None:
    opciones = ["A.BLANCAS_____013", "BALANDRAS_____013", "LAJA__________013"]
    assert busqueda.buscar("blancas 13", opciones) == ["A.BLANCAS_____013"]
