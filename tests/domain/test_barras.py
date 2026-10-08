"""Tests de la tabla de barras renombradas."""

import duckdb

from cmg_ingesta.domain import barras


def test_canonico_aplica_el_renombre() -> None:
    assert barras.canonico("PEÑABLANCA____013") == "PENABLANCA____013"
    assert barras.canonico("LASARAÑAS_____110") == "LASARANAS_____110"


def test_canonico_deja_igual_lo_que_no_cambio() -> None:
    assert barras.canonico("STA.ELVIRA____013") == "STA.ELVIRA____013"
    assert barras.canonico("PENABLANCA____013") == "PENABLANCA____013"


def test_la_tabla_es_solo_quitar_la_enie() -> None:
    """Si alguien agrega un par que hace otra cosa, que se discuta antes."""
    for viejo, nuevo in barras.RENOMBRES.items():
        assert "Ñ" in viejo
        assert nuevo == viejo.replace("Ñ", "N")


def test_un_nombre_oficial_nunca_es_a_su_vez_un_nombre_viejo() -> None:
    """Sin cadenas A -> B -> C: un solo paso deja el nombre final."""
    assert not set(barras.RENOMBRES.values()) & set(barras.RENOMBRES)


def test_el_sql_y_python_dicen_lo_mismo() -> None:
    con = duckdb.connect()
    nombres = [*barras.RENOMBRES, "STA.ELVIRA____013", "O'HIGGINS_013"]
    filas = con.execute(
        f"SELECT barra, {barras.sql_canonico()} FROM (SELECT unnest(?) AS barra)", [nombres]
    ).fetchall()
    assert {b: c for b, c in filas} == {n: barras.canonico(n) for n in nombres}
