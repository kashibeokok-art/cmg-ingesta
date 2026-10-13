"""Tests de la configuración (src/cmg_ingesta/config.py)."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from cmg_ingesta.config import RAIZ_PROYECTO, Settings


@pytest.fixture(autouse=True)
def sin_archivo_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Aísla cada test de tu .env y de tus variables reales."""
    monkeypatch.chdir(tmp_path)
    for clave in list(os.environ):
        if clave.startswith("CMGI_"):
            monkeypatch.delenv(clave, raising=False)


def test_valores_por_defecto() -> None:
    """Sin variables ni .env (conftest lo apaga), se usan los valores por defecto."""
    assert Settings().data_dir == RAIZ_PROYECTO / "data"


def test_el_unico_campo_es_data_dir() -> None:
    """`log_level` y `cen_api_key` se quitaron porque nadie los leia (2026-10-13)."""
    assert list(Settings.model_fields) == ["data_dir"]


def test_lee_variable_de_entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CMGI_DATA_DIR del entorno reemplaza al valor por defecto."""
    monkeypatch.setenv("CMGI_DATA_DIR", str(tmp_path / "desde_entorno"))

    assert Settings().data_dir == tmp_path / "desde_entorno"


def test_lee_archivo_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Si no hay variable de entorno, se lee el archivo .env."""
    env = tmp_path / ".env"
    env.write_text(f"CMGI_DATA_DIR={tmp_path / 'desde_env'}\n", encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", env)

    assert Settings().data_dir == tmp_path / "desde_env"


def test_variable_desconocida_en_env_falla(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un `.env` viejo (o un nombre mal escrito) no se ignora en silencio: falla al arrancar."""
    env = tmp_path / ".env"
    env.write_text("CMGI_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", env)

    with pytest.raises(ValidationError, match="CMGI_LOG_LEVEL"):
        Settings()


def test_una_variable_ajena_en_env_se_ignora(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESION A17: un `.env` compartido con otra herramienta (p. ej. la API de SQL)
    hacia fallar `cmg` con "Extra inputs are not permitted"."""
    env = tmp_path / ".env"
    env.write_text("SQL_SERVIDOR=192.168.0.85\nSQL_PUERTO=5433\n", encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", env)

    assert Settings().data_dir == RAIZ_PROYECTO / "data"


def test_una_cmgi_comentada_no_cuenta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Como quedo el .env real: las variables viejas comentadas con #."""
    env = tmp_path / ".env"
    env.write_text("# sin uso: CMGI_LOG_LEVEL=INFO\nCMGI_DATA_DIR=data\n", encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", env)

    assert Settings().data_dir == RAIZ_PROYECTO / "data"


def test_una_cmgi_desconocida_del_sistema_tambien_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMGI_DAT_DIR", "data")
    with pytest.raises(ValidationError, match="CMGI_DAT_DIR"):
        Settings()


def test_variable_vacia_cuenta_como_no_configurada(monkeypatch: pytest.MonkeyPatch) -> None:
    """CMGI_DATA_DIR= (vacía) usa el valor por defecto, por `env_ignore_empty`."""
    monkeypatch.setenv("CMGI_DATA_DIR", "")

    assert Settings().data_dir == RAIZ_PROYECTO / "data"


def test_la_raiz_es_la_carpeta_del_proyecto() -> None:
    assert (RAIZ_PROYECTO / "pyproject.toml").exists()


def test_data_dir_relativo_se_ancla_al_proyecto(monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESION: `CMGI_DATA_DIR=data` desde otra carpeta buscaba <otra carpeta>\\data."""
    monkeypatch.setenv("CMGI_DATA_DIR", "data")  # el autouse ya hizo chdir a tmp_path

    assert Settings().data_dir == RAIZ_PROYECTO / "data"


def test_data_dir_absoluto_se_respeta(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CMGI_DATA_DIR", str(tmp_path / "otra"))

    assert Settings().data_dir == tmp_path / "otra"
