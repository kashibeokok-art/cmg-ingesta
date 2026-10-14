"""Apertura de DuckDB con una configuracion unica para todo el programa.

El legado tenia DOS funciones distintas para abrir la conexion, con parametros
distintos (`core.abrir_duckdb` y `comun.conexion.abrir`). Eso significa que el
mismo dato se leia con limites de memoria distintos segun por donde entraras.
Aqui hay una sola.
"""

from pathlib import Path

import duckdb


def exigir_carpeta(data_dir: Path) -> None:
    """Lanza ValueError si la carpeta de datos no existe. NO la crea.

    La usan TODOS los comandos que escriben bajo `data_dir`, no solo los que abren
    DuckDB: si la ruta de CMGI_DATA_DIR esta mal escrita, una descarga de ~23 GB
    iria a parar a una carpeta nueva y equivocada sin avisar (A17). En un equipo
    nuevo, la carpeta se crea UNA vez a mano; el mensaje dice como.
    """
    if not data_dir.is_dir():
        raise ValueError(
            f"no existe la carpeta de datos: {data_dir}. Si la ruta esta mal, revisa "
            "CMGI_DATA_DIR en el .env. Si es la primera vez en este equipo, creala: "
            f'mkdir "{data_dir}"'
        )


def exigir_carpeta_propia(ruta: Path, variable: str) -> Path:
    """Una carpeta elegida en el .env (por ejemplo en la red) tiene que existir.

    Igual que `exigir_carpeta`: NO se crea. Si la red no esta conectada o la ruta
    esta mal escrita, crearla en silencio haria que una descarga o una ingesta
    fuera a parar a una carpeta nueva y equivocada.
    """
    if not ruta.is_dir():
        raise ValueError(
            f"no se encuentra la carpeta de {variable}: {ruta}. Si esta en la red, revisa "
            "que el equipo este conectado y que la ruta exista; si esta mal escrita, "
            "corrigela en el .env. El programa no la crea: si es nueva, creala una vez a mano."
        )
    return ruta


def abrir(
    data_dir: Path,
    threads: int = 4,
    memoria: str = "4GB",
    crear: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Una conexion en memoria, con el directorio de desborde dentro de `data_dir`.

    `temp_directory` importa: sin el, DuckDB (1.5) desborda a `.tmp`, una carpeta
    RELATIVA a donde se ejecuta el comando. Corriendo `cmg` desde C:\\ o desde una
    tarea programada sin carpeta de inicio (que arranca en C:\\Windows\\System32),
    escribiria ahi. Con bases de cientos de millones de filas eso puede llenar el
    disco de arranque. (Antes este texto decia "el temporal del sistema": error A16.)

    `data_dir` tiene que existir (`exigir_carpeta`), salvo con `crear=True`. Sin
    esa exigencia, una ruta mal escrita en CMGI_DATA_DIR hacia que el
    `mkdir(parents=True)` de abajo creara la carpeta equivocada en silencio, y el
    comando fallaba despues con un error de DuckDB que no decia nada de la ruta.
    Solo quien construye la base desde cero (`migrar-historico`) pasa `crear=True`.
    """
    if not crear:
        exigir_carpeta(data_dir)
    spill = data_dir / "_duckdb_tmp"
    spill.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute(f"PRAGMA threads={int(threads)}")
    con.execute(f"PRAGMA memory_limit='{memoria}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET temp_directory=?", [str(spill)])
    return con


def ruta_silver(data_dir: Path, propia: Path | None = None) -> Path:
    """Donde vive la capa Silver de CMg: `CMGI_SILVER_DIR` si se eligio, si no bajo data_dir."""
    if propia is not None:
        return exigir_carpeta_propia(propia, "CMGI_SILVER_DIR")
    return data_dir / "silver" / "cmg"


def ruta_descargas(data_dir: Path) -> Path:
    """Donde se escriben los exportes."""
    return data_dir / "descargas"


def ruta_bronze(data_dir: Path) -> Path:
    """Donde se dejan los archivos crudos tal como llegaron."""
    return data_dir / "bronze"


def ruta_bronze_cen(data_dir: Path, propia: Path | None = None) -> Path:
    """Los ZIP del Coordinador y su manifiesto: `CMGI_BRONZE_DIR` si se eligio."""
    if propia is not None:
        return exigir_carpeta_propia(propia, "CMGI_BRONZE_DIR")
    return ruta_bronze(data_dir) / "cen_cmg"


def ruta_staging(data_dir: Path) -> Path:
    """Archivos temporales de una ingesta (CSV extraidos de los ZIP). Se borran solos."""
    return data_dir / "_staging"


def ruta_alertas(data_dir: Path) -> Path:
    """Donde se dejan los reportes de validacion."""
    return data_dir / "alertas"
