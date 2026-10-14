"""Configuracion leida de variables de entorno y .env (Twelve-Factor).

Las rutas relativas se resuelven contra la CARPETA DEL PROYECTO, no contra la
carpeta desde donde se ejecuta el comando. Antes, `CMGI_DATA_DIR=data` significaba
"la carpeta data de donde estes parado": corriendo `cmg` desde la carpeta Scripts
buscaba Scripts/data y decia que la base estaba vacia, y ni siquiera leia el `.env`.

`data_dir`, `desde` y `conservar_zip` los usan los comandos de datos; `pg_url`, solo
la publicacion a PostgreSQL.
`log_level` y `cen_api_key` se quitaron el 2026-10-13: nadie los usaba (el programa
no usa `logging`, y la API del CEN quedo fuera del alcance).

Variables desconocidas (A17):
- Una `CMGI_*` que el programa no conoce DETIENE el arranque, venga del `.env` o
  del sistema: casi siempre es un nombre mal escrito (`CMGI_DAT_DIR`) o una
  variable vieja, y si se ignorara, el programa usaria el valor por defecto sin
  avisar.
- Cualquier otra variable (por ejemplo `SQL_SERVIDOR`, de la API) se IGNORA. El
  `.env` del servidor la puede compartir sin romper `cmg`.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cmg_ingesta.domain import periodo

#: La carpeta que tiene `pyproject.toml`: este archivo esta en src/cmg_ingesta/,
#: dos niveles mas abajo. Funciona porque `uv sync` instala el paquete en modo
#: editable (el codigo se importa desde `src/`, no desde una copia).
RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

PREFIJO = "CMGI_"


def claves_del_env(ruta: Path) -> list[str]:
    """Los nombres de variable de un `.env` (sin valores). Ignora comentarios.

    Un lector minimo a proposito: aqui solo importan los NOMBRES, y asi no hace
    falta depender directamente de python-dotenv.
    """
    if not ruta.is_file():
        return []
    claves: list[str] = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave = linea.split("=", 1)[0].strip()
        claves.append(clave.removeprefix("export ").strip())
    return claves


def cmgi_desconocidas(
    conocidas: set[str], env_file: Path | None, entorno: Mapping[str, str]
) -> list[str]:
    """Las variables `CMGI_*` (del `.env` o del sistema) que no son un campo."""
    vistas = [k for k in entorno if k.upper().startswith(PREFIJO)]
    if env_file is not None:
        vistas += [k for k in claves_del_env(env_file) if k.upper().startswith(PREFIJO)]
    return sorted({k.upper() for k in vistas} - conocidas)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=PREFIJO,
        env_file=RAIZ_PROYECTO / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        # Lo ajeno se ignora; los CMGI_ desconocidos los detiene `_sin_cmgi_desconocidas`.
        extra="ignore",
    )

    data_dir: Path = Path("data")

    #: Donde viven Silver y Bronze, si no es bajo data_dir (por ejemplo, una carpeta
    #: de la red, para un equipo con poco disco). Son las carpetas finales: Silver
    #: contiene `anio=.../mes=...` y Bronze los ZIP y `manifiesto.json`. Tienen que
    #: existir; el programa no las crea. Los temporales siguen en data_dir.
    silver_dir: Path | None = None
    bronze_dir: Path | None = None

    #: False en un equipo con poco disco: cada ZIP del Coordinador que ya quedo en
    #: Silver se borra y queda un testigo de ~300 bytes (ver `ingerir_cen`).
    conservar_zip: bool = True

    #: Conexion a la base cmg_cen (PostgreSQL), solo para `cmg publicar-sql`. Con el
    #: usuario que ESCRIBE (cmg_carga), nunca con el de la API. Formato:
    #:     postgresql://cmg_carga:CLAVE@192.168.0.85:5433/cmg_cen
    #: `SecretStr` evita que la clave aparezca si se imprime la configuracion.
    pg_url: SecretStr | None = None

    #: Primer mes de la base, AAAA-MM (por ejemplo 2023-01). `estado` y el menu no
    #: cuentan como faltante nada anterior, y PostgreSQL no sube meses anteriores.
    #: Sin este ajuste, la base empieza en 2021-01 (el primer mes del Maestro).
    #: (Hasta el 2026-10-13 se llamaba CMGI_PG_DESDE y solo valia para PostgreSQL.)
    desde: str | None = None

    @field_validator("silver_dir", "bronze_dir")
    @classmethod
    def _anclar_si_hay(cls, ruta: Path | None) -> Path | None:
        """Igual que data_dir: relativa se cuelga del proyecto. Una ruta de red es absoluta."""
        return None if ruta is None else cls._anclar_al_proyecto(ruta)

    @field_validator("data_dir")
    @classmethod
    def _anclar_al_proyecto(cls, ruta: Path) -> Path:
        """Una ruta relativa se cuelga de la carpeta del proyecto; una absoluta, tal cual.

        `field_validator` es la forma de pydantic de decir "pasa este campo por esta
        funcion al leerlo".
        """
        return ruta if ruta.is_absolute() else RAIZ_PROYECTO / ruta

    @field_validator("desde")
    @classmethod
    def _mes_valido(cls, texto: str | None) -> str | None:
        """Mal escrito se detiene al arrancar, no a mitad de una carga."""
        return None if texto is None else periodo.formatear(periodo.parsear_mes(texto))

    @model_validator(mode="after")
    def _sin_cmgi_desconocidas(self) -> "Settings":
        """Se detiene si hay una `CMGI_*` que no es un campo (nombre mal escrito o viejo)."""
        env_file = self.model_config.get("env_file")
        ruta = Path(env_file) if isinstance(env_file, str | Path) else None
        conocidas = {f"{PREFIJO}{campo}".upper() for campo in type(self).model_fields}
        desconocidas = cmgi_desconocidas(conocidas, ruta, os.environ)
        if desconocidas:
            raise ValueError(
                f"variable(s) que el programa no conoce: {', '.join(desconocidas)}. "
                f"Las validas son: {', '.join(sorted(conocidas))}. Revisa si esta mal "
                f"escrita o es vieja (en {ruta or 'las variables del sistema'}); si es "
                "vieja, comentala con #."
            )
        return self
