"""Escritura particionada ATOMICA de la capa Silver.

El problema que resuelve: si el proceso muere a mitad de escribir un mes, el mes
NO puede quedar a medias. La base antigua de retiros tenia ese bug (carga con
`to_sql(append)` archivo por archivo, sin transaccion): si fallaba a la mitad, el
mes quedaba incompleto y la verificacion "¿existe ese mes?" lo saltaba para
siempre.

La solucion, que la base de CMg ya tenia bien y aqui se conserva:

    1. escribir TODO el mes en una carpeta temporal  (<destino>__tmp)
    2. recien entonces borrar el destino viejo
    3. mover la temporal a su lugar con os.replace

Si el proceso muere en el paso 1, el destino viejo sigue intacto y la temporal
queda huerfana (se limpia sola en la siguiente corrida).
"""

import shutil
from pathlib import Path

import duckdb

from cmg_ingesta.domain import esquema

SUFIJO_TMP = "__tmp"
NOMBRE_ARCHIVO = "data.parquet"


def ruta_particion(base: Path, anio: int, mes: int) -> Path:
    """La carpeta de un mes, en formato Hive: base/anio=2025/mes=3."""
    return base / f"anio={anio}" / f"mes={mes}"


def particion_existe(base: Path, anio: int, mes: int) -> bool:
    """True si ese mes ya tiene datos escritos."""
    carpeta = ruta_particion(base, anio, mes)
    return carpeta.is_dir() and any(carpeta.glob("*.parquet"))


def filas_en_particion(con: duckdb.DuckDBPyConnection, base: Path, anio: int, mes: int) -> int:
    """Cuantas filas tiene ese mes hoy. 0 si no existe."""
    if not particion_existe(base, anio, mes):
        return 0
    patron = _sql_str(ruta_particion(base, anio, mes) / "*.parquet")
    fila = con.execute(f"SELECT count(*) FROM read_parquet({patron})").fetchone()
    return int(fila[0]) if fila else 0


def limpiar_temporales(base: Path) -> int:
    """Borra las carpetas __tmp que quedaron de una corrida interrumpida.

    Se llama al inicio de cada ingesta. Devuelve cuantas borro.
    """
    if not base.is_dir():
        return 0
    borradas = 0
    for carpeta in base.rglob(f"*{SUFIJO_TMP}"):
        if carpeta.is_dir():
            shutil.rmtree(carpeta, ignore_errors=True)
            borradas += 1
    return borradas


def escribir_particion(
    con: duckdb.DuckDBPyConnection,
    consulta: str,
    base: Path,
    anio: int,
    mes: int,
) -> int:
    """Escribe el resultado de `consulta` como el mes (anio, mes), de forma atomica.

    `consulta` debe devolver exactamente las columnas de `esquema.columnas_archivo()`
    y SOLO las filas de ese mes: esta funcion no filtra, confia en la consulta.

    Reemplaza por completo lo que hubiera en ese mes. Para conservar lo anterior,
    la consulta tiene que incluirlo (ese es el modo "combinar", que se arma en la
    ingesta y no aqui).

    Devuelve la cantidad de filas escritas.
    """
    destino = ruta_particion(base, anio, mes)
    tmp = destino.with_name(destino.name + SUFIJO_TMP)

    # 1. temporal limpia
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    # 2. escribir TODO antes de tocar el destino
    columnas = esquema.sql_lista_columnas()
    con.execute(
        f"COPY (SELECT {columnas} FROM ({consulta}) ORDER BY barra, fecha, hora, minuto) "
        f"TO {_sql_str(tmp / NOMBRE_ARCHIVO)} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    fila = con.execute(
        f"SELECT count(*) FROM read_parquet({_sql_str(tmp / NOMBRE_ARCHIVO)})"
    ).fetchone()
    escritas = int(fila[0]) if fila else 0

    # 3. recien ahora se reemplaza
    if destino.is_dir():
        shutil.rmtree(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(destino)
    return escritas


def meses_escritos(base: Path) -> list[tuple[int, int]]:
    """Los meses que tiene la base hoy, ordenados."""
    if not base.is_dir():
        return []
    meses: list[tuple[int, int]] = []
    for carpeta in base.glob("anio=*/mes=*"):
        if not any(carpeta.glob("*.parquet")):
            continue
        try:
            anio = int(carpeta.parent.name.removeprefix("anio="))
            mes = int(carpeta.name.removeprefix("mes="))
        except ValueError:
            continue
        meses.append((anio, mes))
    return sorted(meses)


def _sql_str(ruta: Path) -> str:
    """Una ruta como literal SQL, con las comillas simples escapadas.

    Nunca interpolar una ruta directo en el SQL: un apostrofo en el nombre de una
    carpeta rompe la consulta (y en el peor caso permite inyeccion).
    """
    return "'" + str(ruta).replace("'", "''") + "'"
