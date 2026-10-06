"""Configuracion leida de variables de entorno y .env (Twelve-Factor)."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

NivelLog = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CMGI_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
    )

    data_dir: Path = Path("data")
    log_level: NivelLog = "INFO"

    cen_api_key: SecretStr | None = None
