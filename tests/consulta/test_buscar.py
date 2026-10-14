"""Busqueda aproximada de barras, con nombres reales del Coordinador."""

import pytest

from cmg_ingesta.consulta import buscar

#: Muestra real (Silver 2022-12): abreviaturas, guiones, voltajes con ceros.
BARRAS = [
    "A.BLANCAS_____013", "A.JAHUEL______110", "A.JAHUEL______220", "A.JAHUEL2_____220",
    "C.CHENA_______220", "C.NAVIA_______110", "C.NAVIA_______220", "CHARRUA_______500",
    "CRUCERO_______220", "CRUCEROSALAUX_220", "D.ALMAGRO_____220", "D.ALMAGRO1____220",
    "E.PENON_______013", "EL_MELON______013", "EL_SALTO______220", "L.VILOS_______220",
    "LUCERO________066", "N.MAITENCILLO_220", "NVA_P.MONTT___220", "P.MONTT_______220",
    "QUELLON_______013", "QUELLON_______110", "SANTA_ELENA___013", "STA.ELISA_____023",
    "STA.ELVIRA____013", "STA.ELVIRA____066", "TAP_EL_ROMERO_220",
]  # fmt: skip


def primera(consulta: str) -> str:
    return buscar.buscar(consulta, BARRAS)[0].barra


@pytest.mark.parametrize(
    ("escrito", "esperada"),
    [
        ("STA.ELVIRA____013", "STA.ELVIRA____013"),  # el nombre exacto
        ("santa elvira 13", "STA.ELVIRA____013"),  # abreviatura y voltaje sin ceros
        ("elvira", "STA.ELVIRA____013"),
        ("quelon", "QUELLON_______013"),  # error de tipeo
        ("cerro navia 220", "C.NAVIA_______220"),  # inicial: C = Cerro
        ("diego almagro 220", "D.ALMAGRO_____220"),
        ("alto jahuel 220", "A.JAHUEL______220"),
        ("nueva maitencillo", "N.MAITENCILLO_220"),
        ("puerto montt", "P.MONTT_______220"),  # antes que NVA_P.MONTT: menos palabras extra
        ("Charrúa 500", "CHARRUA_______500"),  # tilde
    ],
)
def test_la_mas_parecida_va_primero(escrito: str, esperada: str) -> None:
    assert primera(escrito) == esperada


def test_entrega_diez_ordenadas_de_mayor_a_menor() -> None:
    """Pedido del usuario: 10 opciones para elegir, nunca una elegida sola."""
    r = buscar.buscar("santa elvira 13", BARRAS)
    assert len(r) == 10
    porcentajes = [c.porcentaje for c in r]
    assert porcentajes == sorted(porcentajes, reverse=True)
    assert r[0].todas and r[0].porcentaje > r[1].porcentaje


def test_las_que_calzan_en_todo_van_antes_que_las_parecidas() -> None:
    r = buscar.buscar("sta elvira 220", BARRAS)  # no existe en 220 kV
    assert {c.barra for c in r[:2]} == {"STA.ELVIRA____013", "STA.ELVIRA____066"}
    assert not any(c.todas for c in r)


def test_una_palabra_sola_no_calza_por_la_inicial() -> None:
    """ "elvira" no trae E.PENON solo porque empieza con E."""
    r = buscar.buscar("elvira", BARRAS)
    assert "E.PENON_______013" not in [c.barra for c in r if c.todas]


def test_un_articulo_no_es_abreviatura() -> None:
    """EL no abrevia ELVIRA: TAP_EL_ROMERO no se cuela arriba."""
    r = buscar.buscar("sta elvira 220", BARRAS)
    assert r[0].barra.startswith("STA.ELVIRA")


def test_sin_texto_las_primeras_en_orden() -> None:
    assert [c.barra for c in buscar.buscar("  ", BARRAS, limite=3)] == BARRAS[:3]


def test_palabras() -> None:
    assert buscar.palabras("Sta. Elvira__013") == ["STA", "ELVIRA", "013"]
    assert buscar.palabras("Peñablanca") == ["PENABLANCA"]
