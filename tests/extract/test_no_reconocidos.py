"""Tests del registro de enlaces no reconocidos (nombres_no_reconocidos.csv)."""

import csv
from datetime import date
from pathlib import Path

from cmg_ingesta.extract import no_reconocidos as nr

UP = "https://www.coordinador.cl/wp-content/uploads/2026/05/"
DIA = date(2026, 5, 8)


def bloque(nombre: str, etiqueta: str = "Antecedentes Costo Marginal Real Definitivo") -> str:
    return (
        f'<div><span class="informes-estudio-Titulo" title="{etiqueta}">x</span> '
        f'Fecha de publicaci&oacute;n: 15/05/2026 <a href="{UP}{nombre}">Descargar ZIP</a></div>'
    )


PAGINA = (
    bloque("Antecedentes_CMG_Real_def_260508.zip")  # conocido: no se registra
    + bloque("Antecedentes_CMG_Real_def_260508_final.zip")  # CMg con forma nueva
    + bloque("CmgBarrasComparativo_20260508_20260508_15.zip", etiqueta="Comparativo")  # real
)


def test_detectar_solo_lo_que_no_calza() -> None:
    enlaces = nr.detectar(PAGINA, DIA)
    assert [(e["nombre"], e["motivo"]) for e in enlaces] == [
        ("Antecedentes_CMG_Real_def_260508_final.zip", "forma_desconocida"),
        ("CmgBarrasComparativo_20260508_20260508_15.zip", "otro_producto"),
    ]


def test_se_guarda_el_texto_encontrado() -> None:
    e = nr.detectar(PAGINA, DIA)[0]
    assert e["etiqueta"] == "Antecedentes Costo Marginal Real Definitivo"
    assert "Fecha de publicación: 15/05/2026" in e["texto_encontrado"]
    assert e["dia_pagina"] == "2026-05-08"
    assert e["url"] == UP + "Antecedentes_CMG_Real_def_260508_final.zip"


def test_registrar_no_duplica_y_cuenta_las_veces(tmp_path: Path) -> None:
    enlaces = nr.detectar(PAGINA, DIA)
    assert nr.registrar(tmp_path, enlaces, "2026-10-09T10:00:00") == 2
    assert nr.registrar(tmp_path, enlaces, "2026-10-10T10:00:00") == 0  # ya conocidos

    registro = nr.leer(tmp_path)
    assert len(registro) == 2
    fila = registro[UP + "Antecedentes_CMG_Real_def_260508_final.zip"]
    assert (fila["primera_vez"], fila["ultima_vez"], fila["veces"]) == (
        "2026-10-09T10:00:00",
        "2026-10-10T10:00:00",
        "2",
    )


def test_el_csv_se_abre_en_excel(tmp_path: Path) -> None:
    """UTF-8 con BOM (tildes bien en Excel) y separador ';' (configuracion regional de Chile)."""
    nr.registrar(tmp_path, nr.detectar(PAGINA, DIA), "2026-10-09T10:00:00")
    crudo = (tmp_path / nr.ARCHIVO).read_bytes()
    assert crudo.startswith(b"\xef\xbb\xbf")
    with (tmp_path / nr.ARCHIVO).open(encoding="utf-8-sig", newline="") as f:
        filas = list(csv.reader(f, delimiter=";"))
    assert filas[0] == nr.COLUMNAS
    assert "publicación" in (tmp_path / nr.ARCHIVO).read_text(encoding="utf-8-sig")


def test_sin_enlaces_no_crea_archivo(tmp_path: Path) -> None:
    assert nr.registrar(tmp_path, [], "2026-10-09T10:00:00") == 0
    assert not (tmp_path / nr.ARCHIVO).exists()
