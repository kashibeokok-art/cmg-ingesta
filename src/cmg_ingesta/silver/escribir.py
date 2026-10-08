"""Escritura particionada ATOMICA de la capa Silver.

El problema que resuelve: si el proceso muere a mitad de escribir un mes, el mes
NO puede quedar a medias. La base antigua de retiros tenia ese bug (carga con
`to_sql(append)` archivo por archivo, sin transaccion): si fallaba a la mitad, el
mes quedaba incompleto y la verificacion "¿existe ese mes?" lo saltaba para
siempre.

La solucion:

    1. escribir TODO el mes en una carpeta temporal       (<destino>__tmp)
    2. apartar el mes viejo, renombrandolo               (<destino>__old)
    3. poner la temporal en su lugar, renombrandola
    4. recien ahora borrar el mes viejo

En ningun momento el mes queda sin datos: si falla el paso 3, el viejo vuelve a su
lugar. Una version anterior borraba el viejo y DESPUES renombraba; en Windows el
renombre fallo (antivirus/indexador) y el mes quedo vacio (errores_verificados B9).

Si el proceso muere entre pasos, `limpiar_temporales` lo arregla en la siguiente
corrida: borra las `__tmp` y devuelve a su lugar un `__old` cuyo mes falte.
"""

import shutil
import time
from pathlib import Path

import duckdb

from cmg_ingesta.domain import esquema

SUFIJO_TMP = "__tmp"
SUFIJO_VIEJO = "__old"
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
    """Arregla lo que dejo una corrida interrumpida. Devuelve cuantas carpetas toco.

    - `__tmp`: escritura a medias, se borra.
    - `__old`: el mes viejo apartado. Si el mes falta, vuelve a su lugar (el proceso
      murio entre los renombres); si el mes esta, sobra y se borra.

    Se llama al inicio de cada ingesta.
    """
    if not base.is_dir():
        return 0
    tocadas = 0
    for carpeta in list(base.rglob(f"*{SUFIJO_TMP}")):
        if carpeta.is_dir():
            shutil.rmtree(carpeta, ignore_errors=True)
            tocadas += 1
    for carpeta in list(base.rglob(f"*{SUFIJO_VIEJO}")):
        if not carpeta.is_dir():
            continue
        mes = carpeta.with_name(carpeta.name.removesuffix(SUFIJO_VIEJO))
        if mes.is_dir():
            shutil.rmtree(carpeta, ignore_errors=True)
        else:
            _renombrar(carpeta, mes)
        tocadas += 1
    return tocadas


def _renombrar(origen: Path, destino: Path, intentos: int = 10, espera: float = 0.3) -> None:
    """Renombra una carpeta, reintentando si Windows la tiene bloqueada.

    El `PermissionError` lo provoca otro proceso (antivirus, indexador, OneDrive)
    que tiene un archivo abierto ese instante. Dura poco: se espera un poco mas en
    cada intento. Si igual falla, la excepcion sube.
    """
    for intento in range(intentos):
        try:
            origen.replace(destino)
            return
        except PermissionError:
            if intento == intentos - 1:
                raise
            time.sleep(espera * (intento + 1))


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

    # 3. intercambio: el mes nunca queda sin datos
    viejo = destino.with_name(destino.name + SUFIJO_VIEJO)
    shutil.rmtree(viejo, ignore_errors=True)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.is_dir():
        _renombrar(destino, viejo)  # si falla, el destino sigue intacto
    try:
        _renombrar(tmp, destino)
    except OSError:
        if viejo.is_dir():
            _renombrar(viejo, destino)  # se devuelve el mes viejo
        raise
    shutil.rmtree(viejo, ignore_errors=True)
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
