"""Una base cmg_cen de pruebas con datos publicados, para la app de consulta.

Igual que tests/publicar: necesita CMG_PRUEBA_PG (un PostgreSQL de PRUEBAS donde
el usuario sea administrador). Sin esa variable, los tests que usan `lectura` se
saltan. Cada sesion crea su propia base temporal y la borra al terminar.

Los datos tienen la forma que importa para los calculos:
- tres barras con valores distintos por bloque, dia y barra;
- ceros en parte del bloque B (el sol), para el % de riesgo y "cuartos en cero";
- el 2025-04-05 con 25 horas (la hora 24 = la segunda 23:00);
- marzo definitivo y abril preliminar, para los avisos de datos que pueden cambiar;
- documentos del Coordinador: el 2025-03-02 con su definitivo v1 Y su v2 (manda el
  v2), y el 2025-04-01 con su preliminar. Los demas dias no tienen documento.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import duckdb
import psycopg
import pytest

from cmg_ingesta.consulta import conexion
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import ingerir_cen, pagina_cen
from cmg_ingesta.publicar import postgres as pg
from cmg_ingesta.silver import escribir

BASE = os.environ.get("CMG_PRUEBA_PG")

BARRAS = ("A.BLANCAS_____013", "QUELLON_______013", "STA.ELVIRA____013")


def _silver_mes(
    con: duckdb.DuckDBPyConnection, silver: Path, anio: int, mes: int, dias: int, origen: str
) -> None:
    lista = ", ".join(f"('{b}', {i})" for i, b in enumerate(BARRAS))
    horas = "range(25)" if (anio, mes) == (2025, 4) else "range(24)"
    consulta = f"""
        WITH b(barra, n) AS (VALUES {lista})
        SELECT barra, make_date({anio}, {mes}, d) AS fecha,
               CAST(h AS UTINYINT) AS hora, CAST(m AS UTINYINT) AS minuto,
               CASE WHEN h BETWEEN 8 AND 17 THEN 'B' WHEN h BETWEEN 18 AND 22 THEN 'C'
                    ELSE 'A' END AS bloque,
               CAST(CASE WHEN h BETWEEN 11 AND 14 AND n <> 2 THEN 0
                         ELSE 20 + 3 * n + d + (CASE WHEN h BETWEEN 18 AND 22 THEN 40
                                                     WHEN h BETWEEN 8 AND 17 THEN 5
                                                     ELSE 15 END) + m / 15.0 END AS DOUBLE)
                   AS cmg_usd_mwh,
               (h = 24) AS es_hora_extra,
               CASE WHEN h < 24 THEN CAST(make_date({anio}, {mes}, d) AS TIMESTAMP)
                    + to_hours(h) + to_minutes(m) END AS fecha_hora,
               '{origen}' AS origen, now()::TIMESTAMP AS ingerido_en
        FROM b, (SELECT unnest({horas}) AS h), (SELECT unnest([0, 15, 30, 45]) AS m),
             (SELECT unnest(range(1, {dias + 1})) AS d)
        -- la hora 24 solo existe el dia de 25 horas
        WHERE h < 24 OR make_date({anio}, {mes}, d) = DATE '2025-04-05'
    """
    escribir.escribir_particion(con, consulta, silver, anio, mes)


def _documento(nombre: str, dia: str, tipo: str, version: int) -> cen.EntradaManifiesto:
    return {
        "url": "https://x/" + nombre, "nombre": nombre, "tipo": tipo, "version": version,
        "reemision": 0, "fecha_operacion": dia, "fecha_publicacion": "2025-05-10",
        "sha256": uuid.uuid4().hex * 2, "bytes": 1, "descargado_en": "2025-05-10T10:00:00",
    }  # fmt: skip


def _bronze(bronze: Path) -> None:
    """Un manifiesto con documentos, y la huella de "ya ingerido" que exige publicar."""
    docs = [
        _documento("Antecedentes_CMG_Real_def_250302.zip", "2025-03-02", "def", 1),
        _documento("Antecedentes_CMG_Real_def_v2_250302.zip", "2025-03-02", "def", 2),
        _documento("Antecedentes_CMG_Real_pre_250401.zip", "2025-04-01", "pre", 1),
    ]
    cen.guardar_manifiesto(bronze, {d["nombre"]: d for d in docs})
    elegidos = pagina_cen.elegir_por_dia(cen.leer_manifiesto(bronze))
    estado = {
        f"{a}-{m:02d}": ingerir_cen.huella(
            sorted((d, e) for d, e in elegidos.items() if (d.year, d.month) == (a, m))
        )
        for a, m in ((2025, 3), (2025, 4))
    }
    ingerir_cen.guardar_estado(bronze, estado)


@pytest.fixture(scope="session")
def datos_prueba(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    """(nombre de la base, carpeta Silver) con marzo y 10 dias de abril de 2025 publicados."""
    if not BASE:
        pytest.skip("sin CMG_PRUEBA_PG: no hay PostgreSQL de pruebas")
    nombre = f"cmg_consulta_{uuid.uuid4().hex[:10]}"
    tmp = tmp_path_factory.mktemp("consulta")
    silver = tmp / "silver"
    con = duckdb.connect()
    _silver_mes(con, silver, 2025, 3, 31, "pagina_cen_def")
    _silver_mes(con, silver, 2025, 4, 10, "pagina_cen_pre")
    (tmp / "bronze").mkdir()
    _bronze(tmp / "bronze")
    with psycopg.connect(f"{BASE} dbname=postgres", autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE \"{nombre}\" TEMPLATE template0 ENCODING 'UTF8'")
    try:
        with psycopg.connect(f"{BASE} dbname={nombre}", autocommit=True) as c:
            c.execute(pg.ESQUEMA_SQL.read_text(encoding="utf-8"))
        with pg.conectar(_url(nombre, "cmg_carga")) as carga:
            pg.publicar(con, carga, silver, tmp / "bronze", tmp / "staging")
        yield nombre, silver
    finally:
        con.close()
        with psycopg.connect(f"{BASE} dbname=postgres", autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')


def _url(base: str, usuario: str) -> str:
    sin_usuario = " ".join(p for p in str(BASE).split() if not p.startswith("user="))
    return f"{sin_usuario} dbname={base} user={usuario}"


def datos_conexion(base: str, usuario: str = "cmg_lectura") -> conexion.DatosConexion:
    partes = dict(p.split("=", 1) for p in str(BASE).split())
    return {
        "host": partes.get("host", "localhost"),
        "port": int(partes.get("port", "5432")),
        "dbname": base,
        "user": usuario,
        "password": "sin-clave-en-pruebas",  # el PostgreSQL de pruebas usa trust
    }


@pytest.fixture
def lectura(datos_prueba: tuple[str, Path]) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """Conexion como la de la app: usuario cmg_lectura, solo lectura."""
    with conexion.conectar(datos_conexion(datos_prueba[0])) as c:
        yield c


@pytest.fixture
def silver(datos_prueba: tuple[str, Path]) -> Path:
    return datos_prueba[1]
