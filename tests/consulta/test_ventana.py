"""La ventana: sus funciones puras, y una prueba de humo que la abre de verdad."""

import time
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import ttk

import pytest

from cmg_ingesta.consulta import ventana

from .conftest import datos_conexion


@pytest.mark.parametrize("texto", ["05-03-2025", "05/03/2025", "2025-03-05", " 05-03-2025 "])
def test_parsear_fecha(texto: str) -> None:
    assert ventana.parsear_fecha(texto) == date(2025, 3, 5)


@pytest.mark.parametrize("texto", ["", "31-02-2025", "marzo", "2025-13-01"])
def test_fecha_invalida_explica_el_formato(texto: str) -> None:
    with pytest.raises(ValueError, match="dd-mm-aaaa"):
        ventana.parsear_fecha(texto)


def test_rangos_rapidos_recortados_a_la_base() -> None:
    r = ventana.rangos_rapidos(date(2026, 10, 14), (date(2023, 1, 1), date(2026, 10, 12)))
    assert r["Mes pasado"] == (date(2026, 9, 1), date(2026, 9, 30))
    assert r["Este año"] == (date(2026, 1, 1), date(2026, 10, 12))  # hasta lo que hay
    assert r["Ultimos 12 meses"] == (date(2025, 10, 1), date(2026, 10, 12))
    assert r["Todo"] == (date(2023, 1, 1), date(2026, 10, 12))


def test_si_la_base_termina_antes_el_rango_es_su_ultimo_mes() -> None:
    """Una base que termina en 2022 vista en 2026: nada de rangos al reves."""
    r = ventana.rangos_rapidos(date(2026, 10, 14), (date(2022, 11, 1), date(2022, 12, 31)))
    assert r["Mes pasado"] == (date(2022, 12, 1), date(2022, 12, 31))
    assert r["Este año"] == (date(2022, 12, 1), date(2022, 12, 31))
    assert r["Todo"] == (date(2022, 11, 1), date(2022, 12, 31))
    assert all(a <= b for a, b in r.values())


def test_modo_prueba_sin_ventana(
    datos_prueba: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ini = tmp_path / "conexion.ini"
    d = datos_conexion(datos_prueba[0])
    ini.write_text(
        f"[postgres]\nhost = {d['host']}\nport = {d['port']}\ndbname = {d['dbname']}\n"
        f"user = {d['user']}\npassword = {d['password']}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CMG_CONSULTA_INI", str(ini))
    salida = tmp_path / "salida"
    args = ["STA.ELVIRA____013", "A.BLANCAS_____013", "01-03-2025", "05-03-2025", str(salida)]
    assert ventana.prueba(args) == 0
    assert (salida / "prueba_cmg.xlsx").exists() and (salida / "prueba_riesgo.xlsx").exists()
    assert ventana.prueba(["NO_EXISTE", *args[1:]]) == 1
    assert "no tiene datos" in (salida / "prueba.log").read_text(encoding="utf-8")


def _todos(w: tk.Misc) -> list[tk.Misc]:
    hijos = list(w.winfo_children())
    return hijos + [n for h in hijos for n in _todos(h)]


def _textos(raiz: tk.Tk) -> list[str]:
    salida = []
    for w in _todos(raiz):
        if isinstance(w, ttk.Label):
            variable = str(w.cget("textvariable"))
            salida.append(str(raiz.getvar(variable)) if variable else str(w.cget("text")))
    return salida


def test_la_ventana_se_conecta_y_busca(datos_prueba: tuple[str, Path]) -> None:
    try:
        raiz = ventana.construir(datos_conexion(datos_prueba[0]))
    except tk.TclError:  # pragma: no cover - un equipo sin pantalla
        pytest.skip("sin pantalla para tkinter")
    try:
        limite = time.monotonic() + 15
        while not any(t.startswith("Conectado a") for t in _textos(raiz)):
            assert time.monotonic() < limite, _textos(raiz)
            raiz.update()
            time.sleep(0.05)
        estado = next(t for t in _textos(raiz) if t.startswith("Conectado a"))
        assert "del 01-03-2025 al 10-04-2025, 3 barras" in estado

        buscador = next(w for w in _todos(raiz) if isinstance(w, ttk.Entry))
        buscador.insert(0, "elvira")
        raiz.update()
        lista = next(w for w in _todos(raiz) if isinstance(w, tk.Listbox))
        assert str(lista.get(0)).startswith("STA.ELVIRA____013")
        assert "%" in str(lista.get(0))
        assert any(t == "Elegida: (ninguna)" for t in _textos(raiz))  # no elige sola
    finally:
        raiz.destroy()
