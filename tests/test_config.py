"""Tests de la configuración (src/cmg_ingesta/config.py)."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from cmg_ingesta.config import Settings


@pytest.fixture(autouse=True)
def sin_archivo_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Aísla cada test de tu .env y de tus variables reales."""
    monkeypatch.chdir(tmp_path)
    for clave in list(os.environ):
        if clave.startswith("CMGI_"):
            monkeypatch.delenv(clave, raising=False)


def test_valores_por_defecto() -> None:
    """Sin variables ni .env, se usan los valores por defecto de la clase."""
    config = Settings()

    assert config.data_dir == Path("data")
    assert config.log_level == "INFO"
    assert config.cen_api_key is None


def test_lee_variable_de_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """CMGI_LOG_LEVEL del entorno reemplaza al valor por defecto."""
    monkeypatch.setenv("CMGI_LOG_LEVEL", "DEBUG")

    config = Settings()

    assert config.log_level == "DEBUG"


def test_lee_archivo_env(tmp_path: Path) -> None:
    """Si no hay variable de entorno, se lee el archivo .env."""
    (tmp_path / ".env").write_text("CMGI_LOG_LEVEL=WARNING\n", encoding="utf-8")

    config = Settings()

    assert config.log_level == "WARNING"


@pytest.mark.parametrize("log_invalido", ["VERBOSO", "debug", "TRACE", "INFOs"])
def test_rechaza_nivel_invalido(monkeypatch: pytest.MonkeyPatch, log_invalido: str) -> None:
    """Un log_level inválido debe fallar al iniciar (fail fast), no usar el valor por defecto."""
    monkeypatch.setenv("CMGI_LOG_LEVEL", log_invalido)

    with pytest.raises(ValidationError):
        Settings()


def test_secreto_no_se_imprime(monkeypatch: pytest.MonkeyPatch) -> None:
    """La clave se oculta al imprimir la configuración, pero se puede leer explícitamente."""
    monkeypatch.setenv("CMGI_CEN_API_KEY", "clave-super-secreta")

    config = Settings()

    assert "clave-super-secreta" not in str(config)
    assert config.cen_api_key is not None
    assert config.cen_api_key.get_secret_value() == "clave-super-secreta"


def test_clave_vacia_cuenta_como_no_configurada(monkeypatch: pytest.MonkeyPatch) -> None:
    """CMGI_CEN_API_KEY= (vacío, como en .env.example) se interpreta como None."""
    monkeypatch.setenv("CMGI_CEN_API_KEY", "")

    config = Settings()

    assert config.cen_api_key is None
