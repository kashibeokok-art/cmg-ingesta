"""La conexion de la app a cmg_cen: un archivo `conexion.ini` junto al ejecutable.

    [postgres]
    host = 192.168.0.85
    port = 5433
    dbname = cmg_cen
    user = cmg_lectura
    password = ...

Por que un archivo y no la clave dentro del programa: si la clave cambia, se
edita el archivo en la carpeta compartida y nadie tiene que recompilar. El
usuario es `cmg_lectura`, que solo puede leer: quien abra el archivo ve la base,
pero no la puede modificar.

La conexion se abre ademas en modo SOLO LECTURA (`default_transaction_read_only`):
aunque alguien pusiera en el archivo un usuario que escribe, la app no podria.
"""

import configparser
import os
import sys
from pathlib import Path
from typing import TypedDict

import psycopg

NOMBRE_ARCHIVO = "conexion.ini"

#: Variable de entorno para usar otro archivo (pruebas, o una segunda base).
VARIABLE = "CMG_CONSULTA_INI"

#: Cuanto puede tardar una consulta antes de cortarse (milisegundos).
TIEMPO_MAXIMO_MS = 180_000


class DatosConexion(TypedDict):
    host: str
    port: int
    dbname: str
    user: str
    password: str


def carpeta_del_programa() -> Path:
    """Junto al .exe cuando esta empaquetado; la raiz del proyecto en desarrollo."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def ruta_ini() -> Path:
    propia = os.environ.get(VARIABLE)
    return Path(propia) if propia else carpeta_del_programa() / NOMBRE_ARCHIVO


def leer_ini(ruta: Path) -> DatosConexion:
    """Los datos de conexion. ValueError con un mensaje para el usuario si algo falta."""
    if not ruta.is_file():
        raise ValueError(
            f"No se encuentra {ruta.name} en {ruta.parent}. Pidele a quien administra la "
            "base que lo deje junto al programa."
        )
    ini = configparser.ConfigParser()
    ini.read(ruta, encoding="utf-8")
    if "postgres" not in ini:
        raise ValueError(f"{ruta.name} no tiene la seccion [postgres].")
    s = ini["postgres"]
    faltan = [k for k in ("host", "dbname", "user", "password") if not s.get(k, "").strip()]
    if faltan:
        raise ValueError(f"{ruta.name}: falta {', '.join(faltan)} en [postgres].")
    try:
        puerto = int(s.get("port", "5432"))
    except ValueError as e:
        raise ValueError(f"{ruta.name}: el puerto debe ser un numero.") from e
    return {
        "host": s["host"].strip(),
        "port": puerto,
        "dbname": s["dbname"].strip(),
        "user": s["user"].strip(),
        "password": s["password"].strip(),
    }


def conectar(datos: DatosConexion) -> psycopg.Connection[tuple[object, ...]]:
    """Conexion de solo lectura, con limite de tiempo por consulta."""
    return psycopg.connect(
        host=datos["host"],
        port=datos["port"],
        dbname=datos["dbname"],
        user=datos["user"],
        password=datos["password"],
        connect_timeout=10,
        application_name="cmg-consulta",
        options=(f"-c default_transaction_read_only=on -c statement_timeout={TIEMPO_MAXIMO_MS}"),
        autocommit=True,
    )
