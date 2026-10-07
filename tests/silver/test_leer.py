"""Tests de la lectura de Silver que no cubren otros modulos."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from cmg_ingesta.silver import leer

Sembrar = Callable[..., None]


@pytest.fixture
def base(con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar) -> Path:
    silver = tmp_path / "silver"
    sembrar(con, silver, 2024, 6, "BARRA_1", dias=2)
    sembrar(con, silver, 2024, 7, "BARRA_2")
    return silver


def test_resumen_barras_una_fila_por_barra(con: duckdb.DuckDBPyConnection, base: Path) -> None:
    df = leer.resumen_barras(con, base, ["BARRA_2", "BARRA_1"], (2024, 6), (2024, 7))
    assert list(df["barra"]) == ["BARRA_1", "BARRA_2"]
    assert list(df["intervalos"]) == [2 * 96, 96]
    # 9 h de A a 100, 10 h de B a 10, 5 h de C a 200 -> (900 + 100 + 1000) / 24
    assert df["cmg_promedio"].iloc[0] == pytest.approx(2000 / 24)
    assert list(df["hora_extra"]) == [0, 0]


def test_resumen_barras_omite_las_sin_datos_en_el_periodo(
    con: duckdb.DuckDBPyConnection, base: Path
) -> None:
    df = leer.resumen_barras(con, base, ["BARRA_1", "BARRA_2", "NO_EXISTE"], (2024, 6), (2024, 6))
    assert list(df["barra"]) == ["BARRA_1"]


def test_resumen_barras_lista_vacia(con: duckdb.DuckDBPyConnection, base: Path) -> None:
    df = leer.resumen_barras(con, base, [], (2024, 6), (2024, 7))
    assert df.empty
    assert "barra" in df.columns
