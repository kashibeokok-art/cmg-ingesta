"""Apertura de DuckDB con una configuracion unica para todo el programa.

El legado tenia DOS funciones distintas para abrir la conexion, con parametros
distintos (`core.abrir_duckdb` y `comun.conexion.abrir`). Eso significa que el
mismo dato se leia con limites de memoria distintos segun por donde entraras.
Aqui hay una sola.
"""

from pathlib import Path

import duckdb


def abrir(
    data_dir: Path,
    threads: int = 4,
    memoria: str = "4GB",
) -> duckdb.DuckDBPyConnection:
    """Una conexion en memoria, con el directorio de desborde dentro de `data_dir`.

    `temp_directory` importa: sin el, DuckDB desborda al temporal del sistema, que
    en Windows suele estar en el disco C. Con bases de cientos de millones de
    filas eso puede llenar el disco de arranque.
    """
    spill = data_dir / "_duckdb_tmp"
    spill.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute(f"PRAGMA threads={int(threads)}")
    con.execute(f"PRAGMA memory_limit='{memoria}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET temp_directory=?", [str(spill)])
    return con


def ruta_silver(data_dir: Path) -> Path:
    """Donde vive la capa Silver de CMg."""
    return data_dir / "silver" / "cmg"


def ruta_descargas(data_dir: Path) -> Path:
    """Donde se escriben los exportes."""
    return data_dir / "descargas"


def ruta_bronze(data_dir: Path) -> Path:
    """Donde se dejan los archivos crudos tal como llegaron."""
    return data_dir / "bronze"


def ruta_bronze_cen(data_dir: Path) -> Path:
    """Los ZIP del Coordinador y su manifiesto, tal como se descargaron."""
    return ruta_bronze(data_dir) / "cen_cmg"


def ruta_staging(data_dir: Path) -> Path:
    """Archivos temporales de una ingesta (CSV extraidos de los ZIP). Se borran solos."""
    return data_dir / "_staging"


def ruta_alertas(data_dir: Path) -> Path:
    """Donde se dejan los reportes de validacion."""
    return data_dir / "alertas"
