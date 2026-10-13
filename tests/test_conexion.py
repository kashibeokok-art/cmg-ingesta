"""Tests de la apertura de DuckDB (src/cmg_ingesta/conexion.py)."""

from pathlib import Path

import pytest

from cmg_ingesta import conexion


def test_carpeta_inexistente_falla_sin_crear_nada(tmp_path: Path) -> None:
    """REGRESION: con CMGI_DATA_DIR mal escrito, `mkdir(parents=True)` creaba la carpeta
    equivocada en silencio y el comando caia despues con un error de DuckDB."""
    mal_escrita = tmp_path / "dta"

    with pytest.raises(ValueError, match="no existe la carpeta de datos"):
        conexion.abrir(mal_escrita)
    assert not mal_escrita.exists()


def test_crear_permite_partir_de_cero(tmp_path: Path) -> None:
    """`migrar-historico` construye la base desde cero: ahi la carpeta aun no existe."""
    nueva = tmp_path / "data"

    con = conexion.abrir(nueva, crear=True)
    con.close()
    assert (nueva / "_duckdb_tmp").is_dir()


def test_configura_hilos_memoria_y_desborde(tmp_path: Path) -> None:
    con = conexion.abrir(tmp_path, threads=2, memoria="1GB")
    try:

        def ajuste(nombre: str) -> str:
            fila = con.execute(f"SELECT current_setting('{nombre}')").fetchone()
            assert fila is not None
            return str(fila[0])

        assert ajuste("threads") == "2"
        assert ajuste("preserve_insertion_order") == "False"
        assert Path(ajuste("temp_directory")) == tmp_path / "_duckdb_tmp"
    finally:
        con.close()
