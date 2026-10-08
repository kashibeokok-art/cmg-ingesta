"""Configuracion leida de variables de entorno y .env (Twelve-Factor).

Las rutas relativas se resuelven contra la CARPETA DEL PROYECTO, no contra la
carpeta desde donde se ejecuta el comando. Antes, `CMGI_DATA_DIR=data` significaba
"la carpeta data de donde estes parado": corriendo `cmg` desde la carpeta Scripts
buscaba Scripts/data y decia que la base estaba vacia, y ni siquiera leia el `.env`.
"""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NivelLog = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

#: La carpeta que tiene `pyproject.toml`: este archivo esta en src/cmg_ingesta/,
#: dos niveles mas abajo. Funciona porque `uv sync` instala el paquete en modo
#: editable (el codigo se importa desde `src/`, no desde una copia).
RAIZ_PROYECTO = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CMGI_",
        env_file=RAIZ_PROYECTO / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
    )

    data_dir: Path = Path("data")
    log_level: NivelLog = "INFO"

    cen_api_key: SecretStr | None = None

    @field_validator("data_dir")
    @classmethod
    def _anclar_al_proyecto(cls, ruta: Path) -> Path:
        """Una ruta relativa se cuelga de la carpeta del proyecto; una absoluta, tal cual.

        `field_validator` es la forma de pydantic de decir "pasa este campo por esta
        funcion al leerlo". Es lo unico de la clase que no es una declaracion.
        """
        return ruta if ruta.is_absolute() else RAIZ_PROYECTO / ruta
