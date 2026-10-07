"""Tests de los dialogos: cada uno se recorre entero con un guion de respuestas."""

import pytest
from guion import Guion

from cmg_ingesta.menu import consola as dialogos
from cmg_ingesta.menu.consola import Nav

BARRAS = ["A.BLANCAS_____013", "A.BLANCAS_____110", "STA.ELVIRA____013", "QUELLON_______013"]


# ------------------------------------------------------------------ elegir_una


def test_una_por_nombre_exacto_sin_preguntar_numero() -> None:
    g = Guion("sta.elvira____013")
    assert dialogos.elegir_una(g.consola(), BARRAS, "Barra") == "STA.ELVIRA____013"


def test_una_con_coincidencia_unica_la_toma_directo() -> None:
    g = Guion("quellon")
    assert dialogos.elegir_una(g.consola(), BARRAS, "Barra") == "QUELLON_______013"
    assert "-> QUELLON_______013" in g.texto


def test_una_elige_de_la_lista() -> None:
    g = Guion("blancas", "2")
    assert dialogos.elegir_una(g.consola(), BARRAS, "Barra") == "A.BLANCAS_____110"


def test_una_rechaza_varios_numeros_y_vuelve_a_buscar() -> None:
    g = Guion("blancas", "1,2", "blancas", "1")
    assert dialogos.elegir_una(g.consola(), BARRAS, "Barra") == "A.BLANCAS_____013"
    assert "un solo numero" in g.texto


def test_una_sin_coincidencias_pide_de_nuevo() -> None:
    g = Guion("zzzz", "quellon")
    assert dialogos.elegir_una(g.consola(), BARRAS, "Barra") == "QUELLON_______013"
    assert "Sin coincidencias" in g.texto


def test_una_enter_cancela_y_v_vuelve() -> None:
    assert dialogos.elegir_una(Guion("").consola(), BARRAS, "Barra") is None
    assert dialogos.elegir_una(Guion("v").consola(), BARRAS, "Barra") is Nav.VOLVER


def test_una_entrada_cerrada_cancela() -> None:
    assert dialogos.elegir_una(Guion().consola(), BARRAS, "Barra") is None


# ------------------------------------------------------------------ elegir_varias


def test_varias_por_busquedas_sucesivas() -> None:
    g = Guion("blancas", "todas", "elvira", "")
    r = dialogos.elegir_varias(g.consola(), BARRAS, "Barra")
    assert r == ["A.BLANCAS_____013", "A.BLANCAS_____110", "STA.ELVIRA____013"]


def test_varias_no_duplica_y_marca_las_ya_elegidas() -> None:
    g = Guion("blancas", "1", "blancas", "1,2", "")
    r = dialogos.elegir_varias(g.consola(), BARRAS, "Barra")
    assert r == ["A.BLANCAS_____013", "A.BLANCAS_____110"]
    assert "A.BLANCAS_____013  (ya)" in g.texto


def test_varias_quitar() -> None:
    g = Guion("blancas", "todas", "quitar", "1", "")
    assert dialogos.elegir_varias(g.consola(), BARRAS, "Barra") == ["A.BLANCAS_____110"]


def test_varias_conserva_lo_previo_al_volver_a_este_paso() -> None:
    g = Guion("quellon", "")
    r = dialogos.elegir_varias(g.consola(), BARRAS, "Barra", previas=["STA.ELVIRA____013"])
    assert r == ["STA.ELVIRA____013", "QUELLON_______013"]


def test_varias_seleccion_invalida_no_agrega_nada() -> None:
    g = Guion("blancas", "9", "")
    assert dialogos.elegir_varias(g.consola(), BARRAS, "Barra") == []
    assert "invalida" in g.texto


def test_varias_v_vuelve() -> None:
    assert dialogos.elegir_varias(Guion("v").consola(), BARRAS, "Barra") is Nav.VOLVER


# ------------------------------------------------------------------ periodo


def test_periodo_reintenta_si_no_se_entiende() -> None:
    g = Guion("marzo", "2024-03 a 2024-05")
    r = dialogos.pedir_periodo(g.consola(), (2024, 1), (2024, 12))
    assert r == ((2024, 3), (2024, 5))
    assert "no reconocido" in g.texto


def test_periodo_enter_es_todo_lo_disponible() -> None:
    assert dialogos.pedir_periodo(Guion("").consola(), (2024, 1), (2024, 12)) == (
        (2024, 1),
        (2024, 12),
    )


def test_periodo_v_vuelve() -> None:
    assert dialogos.pedir_periodo(Guion("v").consola(), (2024, 1), (2024, 12)) is Nav.VOLVER


# ------------------------------------------------------------------ formatos


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1", ("excel",)),
        ("3,1", ("excel", "parquet")),
        ("csv excel", ("excel", "csv")),
        ("todos", ("excel", "csv", "parquet")),
        ("4", None),
        ("pdf", None),
    ],
)
def test_leer_formatos(texto: str, esperado: tuple[str, ...] | None) -> None:
    assert dialogos.leer_formatos(texto) == esperado


def test_formatos_enter_es_excel() -> None:
    assert dialogos.pedir_formatos(Guion("").consola()) == ("excel",)


def test_formatos_reintenta() -> None:
    g = Guion("pdf", "2")
    assert dialogos.pedir_formatos(g.consola()) == ("csv",)
    assert "invalida" in g.texto


# ------------------------------------------------------------------ confirmar


@pytest.mark.parametrize(
    ("texto", "esperado"), [("s", True), ("SI", True), ("", False), ("n", False)]
)
def test_confirmar(texto: str, esperado: bool) -> None:
    assert Guion(texto).consola().confirmar("¿Seguro?") is esperado


def test_varias_con_coincidencia_unica_la_agrega_directo() -> None:
    g = Guion("elvira", "")
    assert dialogos.elegir_varias(g.consola(), BARRAS, "Barra") == ["STA.ELVIRA____013"]
    assert "+ STA.ELVIRA____013" in g.texto
