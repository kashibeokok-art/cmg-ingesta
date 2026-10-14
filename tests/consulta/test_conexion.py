"""El archivo conexion.ini de la app."""

from pathlib import Path

import pytest

from cmg_ingesta.consulta import conexion

COMPLETO = """[postgres]
host = 192.168.0.85
port = 5433
dbname = cmg_cen
user = cmg_lectura
password = clave-secreta
"""


def test_lee_el_archivo(tmp_path: Path) -> None:
    ruta = tmp_path / "conexion.ini"
    ruta.write_text(COMPLETO, encoding="utf-8")
    datos = conexion.leer_ini(ruta)
    assert datos == {
        "host": "192.168.0.85",
        "port": 5433,
        "dbname": "cmg_cen",
        "user": "cmg_lectura",
        "password": "clave-secreta",
    }


def test_sin_archivo_explica_donde_buscarlo(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="conexion.ini"):
        conexion.leer_ini(tmp_path / "conexion.ini")


def test_falta_un_dato_lo_nombra_sin_mostrar_la_clave(tmp_path: Path) -> None:
    ruta = tmp_path / "conexion.ini"
    ruta.write_text(COMPLETO.replace("host = 192.168.0.85\n", ""), encoding="utf-8")
    with pytest.raises(ValueError, match="host") as e:
        conexion.leer_ini(ruta)
    assert "clave-secreta" not in str(e.value)


def test_puerto_invalido(tmp_path: Path) -> None:
    ruta = tmp_path / "conexion.ini"
    ruta.write_text(COMPLETO.replace("5433", "cinco"), encoding="utf-8")
    with pytest.raises(ValueError, match="puerto"):
        conexion.leer_ini(ruta)


def test_la_variable_de_entorno_elige_otro_archivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(conexion.VARIABLE, str(tmp_path / "otro.ini"))
    assert conexion.ruta_ini() == tmp_path / "otro.ini"


def test_sin_variable_busca_junto_al_programa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(conexion.VARIABLE, raising=False)
    assert conexion.ruta_ini() == conexion.carpeta_del_programa() / "conexion.ini"
    assert (conexion.carpeta_del_programa() / "pyproject.toml").exists()  # en desarrollo
