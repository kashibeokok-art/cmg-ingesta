"""Tests de los flujos del menu, de punta a punta, sobre una base sintetica.

Cada test es una conversacion completa: el guion trae lo que escribiria el
usuario, y se verifica lo que quedo en disco y lo que se le mostro.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import openpyxl
import pytest
from guion import Guion

from cmg_ingesta.config import Settings
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import ingerir_cen
from cmg_ingesta.menu import app
from cmg_ingesta.quality import deriva

Sembrar = Callable[..., None]
HOY = date(2026, 10, 7)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import os

    for clave in list(os.environ):
        if clave.startswith("CMGI_"):
            monkeypatch.delenv(clave, raising=False)
    monkeypatch.chdir(tmp_path)  # que no lea el .env real
    return tmp_path / "data"


@pytest.fixture
def con_base(con: duckdb.DuckDBPyConnection, data_dir: Path, sembrar: Sembrar) -> Path:
    """BARRA_1 en junio y julio, BARRA_2 solo en julio y mas cara en A y C."""
    silver = data_dir / "silver" / "cmg"
    sembrar(con, silver, 2024, 6, "BARRA_1")
    sembrar(con, silver, 2024, 7, "BARRA_1", otras={"BARRA_2": {"A": 120.0, "B": 10.0, "C": 260.0}})
    return silver


def contexto(data_dir: Path, g: Guion) -> app.Contexto:
    return app.Contexto(cfg=Settings(data_dir=data_dir), consola=g.consola(), hoy=lambda: HOY)


def descargados(data_dir: Path, patron: str = "*") -> list[str]:
    return sorted(p.name for p in (data_dir / "descargas").glob(patron))


# ------------------------------------------------------------------ 1. descargar


def test_descargar_varias_barras_en_varios_formatos(data_dir: Path, con_base: Path) -> None:
    g = Guion("barra", "todas", "", "", "1,2", "s")
    app.descargar(contexto(data_dir, g))

    assert descargados(data_dir, "*.xlsx") == [
        "CMg_BARRA_1_2024-06_a_2024-07.xlsx",
        "CMg_BARRA_2_2024-06_a_2024-07.xlsx",
    ]
    assert len(descargados(data_dir, "*.csv")) == 4  # serie + bloques, por barra
    assert "Listo" in g.texto


def test_descargar_muestra_la_previsualizacion_antes_de_escribir(
    data_dir: Path, con_base: Path
) -> None:
    g = Guion("BARRA_1", "", "2024-06 a 2024-06", "", "")  # Enter al confirmar = cancelar
    app.descargar(contexto(data_dir, g))

    assert "BARRA_1" in g.texto
    assert "96" in g.texto  # intervalos de un dia
    assert "Cancelado" in g.texto
    assert descargados(data_dir) == []


def test_descargar_volver_conserva_lo_elegido(data_dir: Path, con_base: Path) -> None:
    """'v' en el periodo vuelve a las barras sin perder la seleccion."""
    g = Guion("BARRA_1", "", "v", "BARRA_2", "", "2024-07", "", "s")
    app.descargar(contexto(data_dir, g))

    assert "Seleccionadas (1): BARRA_1" in g.texto
    assert descargados(data_dir, "*.xlsx") == [
        "CMg_BARRA_1_2024-07_a_2024-07.xlsx",
        "CMg_BARRA_2_2024-07_a_2024-07.xlsx",
    ]


def test_descargar_omite_y_avisa_la_barra_sin_datos(data_dir: Path, con_base: Path) -> None:
    g = Guion("barra", "todas", "", "2024-06 a 2024-06", "", "s")
    app.descargar(contexto(data_dir, g))

    assert "se omiten): BARRA_2" in g.texto
    assert descargados(data_dir, "*.xlsx") == ["CMg_BARRA_1_2024-06_a_2024-06.xlsx"]


def test_descargar_cancelar_al_inicio(data_dir: Path, con_base: Path) -> None:
    g = Guion("")
    app.descargar(contexto(data_dir, g))
    assert "Cancelado" in g.texto
    assert descargados(data_dir) == []


# ------------------------------------------------------------------ 2. bloques


def test_bloques_de_una_barra_muestra_el_detalle(data_dir: Path, con_base: Path) -> None:
    g = Guion("BARRA_2", "", "")
    app.ver_bloques(contexto(data_dir, g))
    assert "NoSolar" in g.texto
    assert "120.00" in g.texto  # bloque A de BARRA_2


def test_bloques_de_varias_barras_las_pone_lado_a_lado(data_dir: Path, con_base: Path) -> None:
    g = Guion("barra", "todas", "", "2024-07")
    app.ver_bloques(contexto(data_dir, g))
    encabezado = next(linea for linea in g.salida if linea.lstrip().startswith("anio"))
    assert "BARRA_1" in encabezado
    assert "BARRA_2" in encabezado


# ------------------------------------------------------------------ 3. riesgo


def test_riesgo_contra_una_barra(data_dir: Path, con_base: Path) -> None:
    g = Guion("BARRA_1", "BARRA_2", "", "", "", "s")
    app.riesgo(contexto(data_dir, g))

    archivos = descargados(data_dir, "*.xlsx")
    assert archivos == ["Riesgo_BARRA_1_vs_BARRA_2_2024-06_a_2024-07.xlsx"]
    libro = openpyxl.load_workbook(data_dir / "descargas" / archivos[0], read_only=True)
    assert libro.sheetnames == ["2024", "Resumen", "Info"]


def test_riesgo_no_ofrece_la_referencia_como_comparada(data_dir: Path, con_base: Path) -> None:
    """Buscando 'barra' solo queda BARRA_2: la referencia no esta entre las opciones."""
    g = Guion("BARRA_1", "barra", "")
    app.riesgo(contexto(data_dir, g))
    assert "+ BARRA_2" in g.texto
    assert "+ BARRA_1" not in g.texto


def test_riesgo_v_en_comparadas_vuelve_a_la_referencia(data_dir: Path, con_base: Path) -> None:
    g = Guion("BARRA_2", "v", "BARRA_1", "BARRA_2", "", "", "", "s")
    app.riesgo(contexto(data_dir, g))
    assert descargados(data_dir, "*.xlsx") == ["Riesgo_BARRA_1_vs_BARRA_2_2024-06_a_2024-07.xlsx"]


# ------------------------------------------------------------------ 4. actualizar


@pytest.fixture
def llamadas(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Reemplaza la descarga y la ingesta: aqui se prueba el dialogo, no la red."""
    registro: dict[str, Any] = {}

    def sincronizar(desde: date, hasta: date, *_: Any, **kw: Any) -> tuple[list[Any], list[Any]]:
        registro["sincronizar"] = (desde, hasta)
        kw["avisar"]("  2026-10-06  1 archivo(s) publicado(s)")
        return [], []

    def ingerir(*_: Any, **__: Any) -> tuple[list[Any], list[Any]]:
        registro["ingerir"] = True
        return [], []

    monkeypatch.setattr(deriva, "sincronizar_vigilando", sincronizar)
    monkeypatch.setattr(ingerir_cen, "ingerir_pagina", ingerir)
    return registro


def contexto_red(data_dir: Path, g: Guion) -> app.Contexto:
    ctx = contexto(data_dir, g)
    ctx.nueva_sesion = lambda: object()  # type: ignore[assignment,return-value]
    return ctx


def test_actualizar_sin_nada_descargado_sugiere_el_backfill(
    data_dir: Path, llamadas: dict[str, Any]
) -> None:
    g = Guion("", "s")
    app.actualizar(contexto_red(data_dir, g))

    assert llamadas["sincronizar"] == (cen.INICIO_FUENTE, date(2026, 10, 6))
    assert llamadas["ingerir"]
    assert "descarga grande" in g.texto
    assert "1 archivo(s) publicado(s)" in g.texto  # el avance llega a la pantalla


def test_actualizar_con_fecha_escrita(data_dir: Path, llamadas: dict[str, Any]) -> None:
    g = Guion("2026-10-01", "s")
    app.actualizar(contexto_red(data_dir, g))
    assert llamadas["sincronizar"] == (date(2026, 10, 1), date(2026, 10, 6))
    assert "descarga grande" not in g.texto


def test_actualizar_rechaza_fechas_fuera_de_la_fuente(
    data_dir: Path, llamadas: dict[str, Any]
) -> None:
    g = Guion("2024-07-31", "2026-10-07", "ayer", "v")
    app.actualizar(contexto_red(data_dir, g))

    assert "vienen del Maestro" in g.texto
    assert "ultimo dia publicable es 2026-10-06" in g.texto
    assert "Fecha invalida" in g.texto
    assert llamadas == {}


def test_actualizar_sin_confirmar_no_descarga(data_dir: Path, llamadas: dict[str, Any]) -> None:
    g = Guion("2026-10-01", "")
    app.actualizar(contexto_red(data_dir, g))
    assert "Cancelado" in g.texto
    assert llamadas == {}


# ------------------------------------------------------------------ menu principal


def test_menu_muestra_el_estado_y_sale_con_cero(data_dir: Path, con_base: Path) -> None:
    g = Guion("0")
    app.ejecutar(contexto(data_dir, g))
    assert "2024-06 a 2024-07" in g.texto
    assert "2 barras" in g.texto
    assert "nada descargado todavia" in g.texto


def test_menu_estado_de_la_pagina_lee_el_manifiesto(data_dir: Path) -> None:
    bronze = data_dir / "bronze" / "cen_cmg"
    entrada: cen.EntradaManifiesto = {
        "url": "",
        "nombre": "Antecedentes_CMG_Real_def_260930.zip",
        "tipo": "def",
        "version": 1,
        "reemision": 0,
        "fecha_operacion": "2026-09-30",
        "fecha_publicacion": None,
        "sha256": "",
        "bytes": 0,
        "descargado_en": "",
    }
    cen.guardar_manifiesto(bronze, {entrada["nombre"]: entrada})
    lineas = app.estado_base(contexto(data_dir, Guion()))
    assert "(vacia)" in lineas[0]
    assert "1 ZIP descargados, 1 dias, ultimo 2026-09-30" in lineas[1]


def test_menu_opcion_invalida(data_dir: Path, con_base: Path) -> None:
    g = Guion("9", "", "0")
    app.ejecutar(contexto(data_dir, g))
    assert "Opcion no valida" in g.texto


def test_menu_con_base_vacia_no_deja_consultar(data_dir: Path) -> None:
    g = Guion("1", "", "0")
    app.ejecutar(contexto(data_dir, g))
    assert "La base esta vacia. Usa la opcion 4" in g.texto


def test_menu_un_error_no_cierra_el_programa(
    data_dir: Path, con_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def falla(_: app.Contexto) -> None:
        raise ValueError("algo salio mal")

    def revienta(_: app.Contexto) -> None:
        raise RuntimeError("inesperado")

    monkeypatch.setattr(
        app, "OPCIONES", (app.Opcion("1", "falla", falla), app.Opcion("2", "revienta", revienta))
    )
    g = Guion("1", "", "2", "", "0")
    app.ejecutar(contexto(data_dir, g))

    assert "Error: algo salio mal" in g.texto
    assert "RuntimeError: inesperado" in g.texto
    assert g.salida[-1] == "  Opcion: "  # volvio al menu y salio con 0
