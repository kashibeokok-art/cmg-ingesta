"""Publicar Silver en PostgreSQL: la base cmg_cen que consulta la API.

El esquema esta en `sql/cmg_cen.sql`. Este modulo lo llena, mes a mes, con la
misma idea que la escritura de Silver:

1. **Solo lo que cambio.** Cada mes de Silver es un archivo; su sha256 queda en
   `cmg.carga_mes`. Si el archivo no cambio, el mes no se toca.
2. **Un mes entero, de una sola vez.** Los datos nuevos se cargan en una tabla
   aparte (`cmg.cuarto_AAAA_MM_nuevo`) mientras la API sigue leyendo la vieja.
   Recien con todo cargado y verificado se intercambian, en una transaccion corta.
3. **Gold se rehace en la misma transaccion** (`cmg.mes_barra`, `cmg.mes_bloque`):
   un resumen mensual nunca queda desfasado de sus cuartos de hora.
4. **Las revisiones quedan registradas.** Antes del intercambio se compara el mes
   viejo con el nuevo, dia por dia: si un dia cambio de version (pre -> def,
   def -> v2), se anota en `cmg.revision` cuantos cuartos cambiaron y cuanto.
5. **Que documento hay en cada dia.** Para los meses de la pagina se registra el
   ZIP del Coordinador que se uso (`cmg.dia`) y todos los que se descargaron
   (`cmg.documento`).

6. **Desde un mes en adelante** (opcional, `CMGI_DESDE`). Los meses anteriores
   de Silver no se suben, y `quitar_anteriores` borra de la base los que ya
   estaban (cuartos, Gold, dias, documentos y revisiones de esos meses).

La carga va por `COPY` (el modo masivo de PostgreSQL): DuckDB arma un CSV del mes
con los numeros de barra ya puestos, y PostgreSQL lo lee de una vez.
"""

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TypedDict

import duckdb
import psycopg
from psycopg import sql

from cmg_ingesta.config import RAIZ_PROYECTO
from cmg_ingesta.domain import esquema, periodo
from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import ingerir_cen, pagina_cen
from cmg_ingesta.silver import escribir

ESQUEMA_SQL = RAIZ_PROYECTO / "sql" / "cmg_cen.sql"

#: Los mismos numeros que la tabla cmg.origen del esquema.
ORIGEN_ID = {
    esquema.ORIGEN_MAESTRO: 1,
    esquema.ORIGEN_PAGINA_DEF: 2,
    esquema.ORIGEN_PAGINA_PRE: 3,
}

Avisar = Callable[[str], None]


class ResultadoMes(TypedDict):
    anio: int
    mes: int
    accion: str  # "nuevo" | "reemplazado"
    filas: int
    dias: int
    revisiones: int  # dias que ya estaban cargados y cambiaron


def _nada(_mensaje: str) -> None:
    """Callback por defecto: no avisa nada."""


# ------------------------------------------------------------------ apoyo


#: Sin consultas en paralelo. Una consulta paralela (agregados, crear el indice)
#: reparte el trabajo usando memoria compartida (`/dev/shm`), y el servidor de
#: cmg_cen la tiene chica (Docker da 64 MB por defecto): el 2026-10-15 la carga se
#: cayo con "could not resize shared memory segment ... No space left on device".
#: La carga ya es secuencial (mes a mes), asi que el costo es poco. El arreglo de
#: fondo es del servidor: mas `shm_size` en Docker.
SIN_PARALELO = "-c max_parallel_workers_per_gather=0 -c max_parallel_maintenance_workers=0"


def conectar(url: str) -> psycopg.Connection[tuple[object, ...]]:
    """Conexion en modo autocommit: cada bloque `with conn.transaction()` es explicito."""
    return psycopg.connect(url, autocommit=True, options=SIN_PARALELO)


def huella_archivo(ruta: Path) -> str:
    """sha256 del archivo de un mes de Silver: si no cambio, el mes no se vuelve a subir."""
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def archivo_mes(silver: Path, mes: Mes) -> Path:
    return escribir.ruta_particion(silver, mes[0], mes[1]) / escribir.NOMBRE_ARCHIVO


def nombre_particion(mes: Mes) -> str:
    return f"cuarto_{mes[0]:04d}_{mes[1]:02d}"


def limites(mes: Mes) -> tuple[date, date]:
    """[primer dia del mes, primer dia del mes siguiente)."""
    anio, m = mes
    return date(anio, m, 1), date(anio + (m == 12), 1 if m == 12 else m + 1, 1)


def _lit(ruta: Path) -> str:
    return "'" + str(ruta).replace("'", "''") + "'"


# ------------------------------------------------------------- que subir


def meses_cargados(conn: psycopg.Connection[tuple[object, ...]]) -> dict[Mes, str]:
    filas = conn.execute("SELECT anio, mes, huella FROM cmg.carga_mes").fetchall()
    return {(int(str(a)), int(str(m))): str(h) for a, m, h in filas}


def meses_pendientes(
    silver: Path, cargados: dict[Mes, str], forzar: bool = False, desde: Mes | None = None
) -> list[tuple[Mes, str]]:
    """Los meses de Silver (desde `desde`, si se da) cuyo archivo no esta cargado tal cual."""
    pendientes: list[tuple[Mes, str]] = []
    for mes in escribir.meses_escritos(silver):
        if desde is not None and periodo.indice(mes) < periodo.indice(desde):
            continue
        huella = huella_archivo(archivo_mes(silver, mes))
        if forzar or cargados.get(mes) != huella:
            pendientes.append((mes, huella))
    return pendientes


_PARTICION = re.compile(r"^cuarto_(\d{4})_(\d{2})$")


def meses_en_la_base(conn: psycopg.Connection[tuple[object, ...]]) -> list[Mes]:
    """Los meses que tienen particion conectada a cmg.cuarto (lo que ve la API)."""
    filas = conn.execute(
        """
        SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = 'cmg.cuarto'::regclass
        """
    ).fetchall()
    meses = [m for (nombre,) in filas if (m := _PARTICION.match(str(nombre)))]
    return sorted((int(m.group(1)), int(m.group(2))) for m in meses)


def quitar_anteriores(
    conn: psycopg.Connection[tuple[object, ...]], desde: Mes, avisar: Avisar = _nada
) -> list[Mes]:
    """Borra de la base todo lo anterior a `desde`. Devuelve los meses quitados.

    Una sola transaccion: o se quitan todos o ninguno. Se borran los cuartos
    (la particion entera, no fila por fila), el Gold, los dias, los documentos y
    las revisiones de esos meses, y las barras que quedan sin ningun dato.
    """
    d1 = date(desde[0], desde[1], 1)
    meses = [m for m in meses_en_la_base(conn) if periodo.indice(m) < periodo.indice(desde)]
    meses = sorted(
        set(meses) | {m for m in meses_cargados(conn) if periodo.indice(m) < periodo.indice(desde)}
    )
    with conn.transaction():
        for mes in meses:
            nombre = nombre_particion(mes)
            existe = conn.execute("SELECT to_regclass(%s)", (f"cmg.{nombre}",)).fetchone()
            if existe and existe[0]:
                particion = sql.Identifier("cmg", nombre)
                conn.execute(
                    sql.SQL("ALTER TABLE cmg.cuarto DETACH PARTITION {}").format(particion)
                )
                conn.execute(sql.SQL("DROP TABLE {}").format(particion))
            # lo que haya quedado de una carga cortada justo antes del intercambio
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier("cmg", nombre + "_nuevo"))
            )
        for tabla in ("mes_barra", "mes_bloque", "dia", "revision", "documento"):
            conn.execute(
                sql.SQL("DELETE FROM {} WHERE fecha < %s").format(sql.Identifier("cmg", tabla)),
                (d1,),
            )
        conn.execute("DELETE FROM cmg.carga_mes WHERE make_date(anio, mes, 1) < %s", (d1,))
        # cada mes con cuartos tiene su Gold, asi que basta mirar mes_barra
        conn.execute(
            """
            DELETE FROM cmg.barra b
            WHERE NOT EXISTS (SELECT 1 FROM cmg.mes_barra g WHERE g.barra_id = b.barra_id)
            """
        )
    for mes in meses:
        avisar(f"{periodo.formatear(mes)}: quitado de la base")
    return meses


def documentos_del_mes(
    bronze: Path, mes: Mes
) -> tuple[list[cen.EntradaManifiesto], dict[date, cen.EntradaManifiesto]]:
    """(todos los ZIP descargados del mes, el elegido por dia), desde el Bronze.

    Si el Silver de ese mes no corresponde al Bronze actual (se bajo algo y no se
    ingirio), se detiene: si no, la base diria que un dia usa un documento cuyos
    datos todavia no estan en Silver.
    """
    manifiesto = cen.leer_manifiesto(bronze)
    todos = [
        e
        for e in manifiesto.values()
        if date.fromisoformat(e["fecha_operacion"]).replace(day=1) == date(mes[0], mes[1], 1)
    ]
    elegidos = {
        d: e for d, e in pagina_cen.elegir_por_dia(manifiesto).items() if (d.year, d.month) == mes
    }
    if elegidos:
        clave = f"{mes[0]}-{mes[1]:02d}"
        ingerido = ingerir_cen.leer_estado(bronze).get(clave)
        if ingerido != ingerir_cen.huella(sorted(elegidos.items())):
            raise ValueError(
                f"el Silver de {clave} no refleja lo descargado (hay archivos sin ingerir). "
                "Corre primero: cmg ingerir-pagina"
            )
    return todos, elegidos


# ----------------------------------------------------------------- barras


def asegurar_barras(
    conn: psycopg.Connection[tuple[object, ...]], nombres: list[str]
) -> dict[str, int]:
    """El numero de cada barra, agregando las nuevas.

    Primero se leen las que ya existen y SOLO se insertan las que faltan. Un
    `INSERT ... ON CONFLICT DO NOTHING` de todas gastaria un numero de la
    secuencia por cada barra en cada carga, y `smallint` (max 32.767) se agotaria
    en unos 20 meses publicados.
    """
    consulta = "SELECT nombre, barra_id FROM cmg.barra WHERE nombre = ANY(%s)"
    existentes = {str(n): int(str(i)) for n, i in conn.execute(consulta, (nombres,)).fetchall()}
    faltan = sorted(set(nombres) - set(existentes))
    if faltan:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO cmg.barra (nombre) VALUES (%s)", [(n,) for n in faltan])
        for n, i in conn.execute(consulta, (faltan,)).fetchall():
            existentes[str(n)] = int(str(i))
    return existentes


def _usd_por_dia(con: duckdb.DuckDBPyConnection, archivo: Path) -> dict[date, float]:
    """El tipo de cambio de cada dia del mes de Silver (vacio si el archivo no lo trae)."""
    trae = {
        str(f[0])
        for f in con.execute(f"DESCRIBE SELECT * FROM read_parquet({_lit(archivo)})").fetchall()
    }
    if "usd_clp" not in trae:
        return {}
    filas = con.execute(
        f"SELECT fecha, max(usd_clp) FROM read_parquet({_lit(archivo)}) "
        "WHERE usd_clp IS NOT NULL GROUP BY fecha"
    ).fetchall()
    return {f: float(u) for f, u in filas}


def asegurar_columnas(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Agrega a una base ya creada las columnas nuevas del esquema (cmg_carga es el dueno).

    Asi una base creada antes del 2026-10-15 recibe `cmg.dia.usd_clp` sin que el
    administrador tenga que volver a correr sql/cmg_cen.sql.
    """
    conn.execute("ALTER TABLE cmg.dia ADD COLUMN IF NOT EXISTS usd_clp double precision")


# ------------------------------------------------------------------ cargar


def _csv_del_mes(
    con: duckdb.DuckDBPyConnection, archivo: Path, barras: dict[str, int], destino: Path
) -> int:
    """Escribe el mes en CSV, ya con barra_id y origen_id. Devuelve cuantas filas."""
    con.execute("CREATE OR REPLACE TEMP TABLE _barra (nombre VARCHAR, barra_id SMALLINT)")
    con.executemany("INSERT INTO _barra VALUES (?, ?)", list(barras.items()))
    origen = " ".join(f"WHEN '{o}' THEN {i}" for o, i in ORIGEN_ID.items())
    con.execute(f"""
        COPY (
            SELECT b.barra_id, s.fecha, s.hora, s.minuto, s.bloque, s.cmg_usd_mwh,
                   CASE s.origen {origen} END AS origen_id
            FROM read_parquet({_lit(archivo)}) s
            JOIN _barra b ON b.nombre = s.barra
        ) TO {_lit(destino)} (FORMAT csv, HEADER false)
    """)
    fila = con.execute(f"SELECT count(*) FROM read_parquet({_lit(archivo)})").fetchone()
    esperadas = int(fila[0]) if fila else 0
    fila = con.execute(f"SELECT count(*) FROM read_csv({_lit(destino)}, header=false)").fetchone()
    escritas = int(fila[0]) if fila else 0
    if escritas != esperadas:
        raise ValueError(f"{archivo}: {esperadas} filas en Silver pero {escritas} en el CSV")
    return escritas


def _cargar_tabla_nueva(
    conn: psycopg.Connection[tuple[object, ...]], mes: Mes, csv: Path
) -> sql.Identifier:
    """Crea cmg.cuarto_AAAA_MM_nuevo y la llena. La API no la ve hasta el intercambio."""
    nuevo = sql.Identifier("cmg", nombre_particion(mes) + "_nuevo")
    d1, d2 = limites(mes)
    with conn.transaction():
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(nuevo))
        conn.execute(
            sql.SQL(
                "CREATE TABLE {} (LIKE cmg.cuarto INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
            ).format(nuevo)
        )
        with (
            conn.cursor() as cur,
            cur.copy(
                sql.SQL(
                    "COPY {} (barra_id, fecha, hora, minuto, bloque, cmg_usd_mwh, origen_id) "
                    "FROM STDIN (FORMAT csv)"
                ).format(nuevo)
            ) as copy,
            csv.open("rb") as f,
        ):
            for bloque in iter(lambda: f.read(1 << 20), b""):
                copy.write(bloque)
        # el rango le ahorra a ATTACH revisar fila por fila; la clave se crea al
        # final porque construir el indice una vez es mas rapido que mantenerlo
        conn.execute(
            sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} CHECK (fecha >= {} AND fecha < {})").format(
                nuevo,
                sql.Identifier(nombre_particion(mes) + "_rango"),
                sql.Literal(d1),
                sql.Literal(d2),
            )
        )
        conn.execute(
            sql.SQL(
                "ALTER TABLE {} ADD CONSTRAINT {} PRIMARY KEY (barra_id, fecha, hora, minuto)"
            ).format(nuevo, sql.Identifier(nombre_particion(mes) + "_nuevo_pkey"))
        )
    return nuevo


def _comparar_dias(
    conn: psycopg.Connection[tuple[object, ...]], viejo: sql.Identifier, nuevo: sql.Identifier
) -> dict[date, tuple[int, int, int, float | None, float | None]]:
    """Por dia: (origen nuevo, cuartos comparados, distintos, dif max, dif promedio)."""
    filas = conn.execute(
        sql.SQL("""
            SELECT n.fecha,
                   min(n.origen_id),
                   count(*),
                   count(*) FILTER (WHERE n.cmg_usd_mwh <> o.cmg_usd_mwh),
                   max(abs(n.cmg_usd_mwh - o.cmg_usd_mwh)),
                   avg(abs(n.cmg_usd_mwh - o.cmg_usd_mwh))
                       FILTER (WHERE n.cmg_usd_mwh <> o.cmg_usd_mwh)
            FROM {} n JOIN {} o USING (barra_id, fecha, hora, minuto)
            GROUP BY n.fecha
        """).format(nuevo, viejo)
    ).fetchall()
    return {
        f: (int(str(o)), int(str(c)), int(str(d)), _flotante(mx), _flotante(pr))
        for f, o, c, d, mx, pr in filas
        if isinstance(f, date)
    }


def _flotante(x: object) -> float | None:
    return None if x is None else float(str(x))


def _recalcular_gold(
    conn: psycopg.Connection[tuple[object, ...]], particion: sql.Identifier, primer_dia: date
) -> None:
    """Rehace el Gold de UN mes desde su particion de cuartos.

    Misma regla que `gold/bloques_mes.py` (un test exige que den lo mismo): todo
    sale de los cuartos de hora, NUNCA de promediar promedios. Como cada cuarto
    dura 15 minutos, un `avg` sobre los cuartos de un bloque ya es el promedio
    ponderado por tiempo; y `no_solar` promedia los cuartos de A y C JUNTOS.
    """
    conn.execute("DELETE FROM cmg.mes_barra WHERE fecha = %s", (primer_dia,))
    conn.execute("DELETE FROM cmg.mes_bloque WHERE fecha = %s", (primer_dia,))
    conn.execute(
        sql.SQL("""
            INSERT INTO cmg.mes_barra (barra_id, fecha, cuartos, a, b, c, no_solar, total)
            SELECT barra_id, {}, count(*),
                   avg(cmg_usd_mwh) FILTER (WHERE bloque = 'A'),
                   avg(cmg_usd_mwh) FILTER (WHERE bloque = 'B'),
                   avg(cmg_usd_mwh) FILTER (WHERE bloque = 'C'),
                   avg(cmg_usd_mwh) FILTER (WHERE bloque IN ('A', 'C')),
                   avg(cmg_usd_mwh)
            FROM {}
            GROUP BY barra_id
        """).format(sql.Literal(primer_dia), particion)
    )
    conn.execute(
        sql.SQL("""
            INSERT INTO cmg.mes_bloque (barra_id, fecha, bloque, cuartos, promedio,
                                        desv_estandar, minimo, maximo, en_cero)
            SELECT barra_id, {}, bloque, count(*), avg(cmg_usd_mwh),
                   stddev_samp(cmg_usd_mwh), min(cmg_usd_mwh), max(cmg_usd_mwh),
                   count(*) FILTER (WHERE cmg_usd_mwh = 0)
            FROM {}
            GROUP BY barra_id, bloque
        """).format(sql.Literal(primer_dia), particion)
    )


def publicar_mes(
    con: duckdb.DuckDBPyConnection,
    conn: psycopg.Connection[tuple[object, ...]],
    silver: Path,
    bronze: Path,
    staging: Path,
    mes: Mes,
    huella: str,
    ahora: datetime,
) -> ResultadoMes:
    """Sube UN mes de Silver y registra sus revisiones. Ver el docstring del modulo."""
    archivo = archivo_mes(silver, mes)
    todos, elegidos = documentos_del_mes(bronze, mes)

    # un mes con filas sin clave no se sube: se diria en la base algo que no es
    nulas = con.execute(
        f"SELECT count(*) FROM read_parquet({_lit(archivo)}) "
        "WHERE barra IS NULL OR fecha IS NULL OR hora IS NULL OR minuto IS NULL"
    ).fetchone()
    if nulas and nulas[0]:
        raise ValueError(
            f"el Silver de {mes[0]}-{mes[1]:02d} tiene {nulas[0]:,} filas sin barra, fecha u "
            f"hora. Rehazlo y vuelve a publicar:  cmg ingerir-pagina --mes {mes[0]}-{mes[1]:02d}"
        )
    fila_nombres = con.execute(
        f"SELECT list(DISTINCT barra) FROM read_parquet({_lit(archivo)})"
    ).fetchone()
    barras = asegurar_barras(conn, sorted(fila_nombres[0]) if fila_nombres else [])
    usd = _usd_por_dia(con, archivo)

    staging.mkdir(parents=True, exist_ok=True)
    csv = staging / f"{nombre_particion(mes)}.csv"
    try:
        filas = _csv_del_mes(con, archivo, barras, csv)
        nuevo = _cargar_tabla_nueva(conn, mes, csv)
    finally:
        csv.unlink(missing_ok=True)

    nombre = nombre_particion(mes)
    viejo = sql.Identifier("cmg", nombre)
    existe = conn.execute("SELECT to_regclass(%s)", (f"cmg.{nombre}",)).fetchone()
    habia = bool(existe and existe[0])
    d1, d2 = limites(mes)

    with conn.transaction():
        # --- documentos: todos los ZIP del mes (para "que publico el CEN")
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO cmg.documento (nombre, fecha, tipo, version, reemision, publicado,
                                           descargado_en, sha256, bytes, url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (nombre) DO UPDATE SET
                    fecha = EXCLUDED.fecha, tipo = EXCLUDED.tipo, version = EXCLUDED.version,
                    reemision = EXCLUDED.reemision, publicado = EXCLUDED.publicado,
                    descargado_en = EXCLUDED.descargado_en, sha256 = EXCLUDED.sha256,
                    bytes = EXCLUDED.bytes, url = EXCLUDED.url
                """,
                [
                    (
                        e["nombre"],
                        e["fecha_operacion"],
                        e["tipo"],
                        e["version"],
                        e["reemision"],
                        e["fecha_publicacion"],
                        e["descargado_en"] or None,
                        e["sha256"],
                        e["bytes"],
                        e["url"],
                    )
                    for e in todos
                ],
            )

        # --- revisiones: dias que ya estaban cargados y cambiaron
        revisiones = 0
        if habia:
            antes = {
                f: (int(str(o)), None if d is None else str(d))
                for f, o, d in conn.execute(
                    "SELECT fecha, origen_id, documento FROM cmg.dia "
                    "WHERE fecha >= %s AND fecha < %s",
                    (d1, d2),
                ).fetchall()
                if isinstance(f, date)
            }
            for dia, (origen, cuartos, distintos, dif_max, dif_prom) in sorted(
                _comparar_dias(conn, viejo, nuevo).items()
            ):
                origen_antes, doc_antes = antes.get(dia, (None, None))
                doc = elegidos.get(dia)
                doc_despues = doc["nombre"] if doc else None
                if distintos == 0 and origen == origen_antes and doc_despues == doc_antes:
                    continue
                conn.execute(
                    """
                    INSERT INTO cmg.revision (fecha, origen_antes, origen_despues,
                        documento_antes, documento_despues, publicado, detectado_en,
                        cuartos, cuartos_distintos, dif_max_usd, dif_prom_usd)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        dia,
                        origen_antes,
                        origen,
                        doc_antes,
                        doc_despues,
                        doc["fecha_publicacion"] if doc else None,
                        ahora,
                        cuartos,
                        distintos,
                        dif_max,
                        dif_prom,
                    ),
                )
                revisiones += 1

        # --- el intercambio: corto, la tabla nueva ya esta completa y con su clave
        if habia:
            conn.execute(sql.SQL("ALTER TABLE cmg.cuarto DETACH PARTITION {}").format(viejo))
            conn.execute(sql.SQL("DROP TABLE {}").format(viejo))
        conn.execute(sql.SQL("ALTER TABLE {} RENAME TO {}").format(nuevo, sql.Identifier(nombre)))
        conn.execute(
            sql.SQL("ALTER TABLE {} RENAME CONSTRAINT {} TO {}").format(
                viejo, sql.Identifier(nombre + "_nuevo_pkey"), sql.Identifier(nombre + "_pkey")
            )
        )
        conn.execute(
            sql.SQL(
                "ALTER TABLE cmg.cuarto ATTACH PARTITION {} FOR VALUES FROM ({}) TO ({})"
            ).format(viejo, sql.Literal(d1), sql.Literal(d2))
        )
        conn.execute(
            sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                viejo, sql.Identifier(nombre + "_rango")
            )
        )

        # --- que hay cargado en cada dia
        dias = conn.execute(
            sql.SQL("SELECT fecha, min(origen_id) FROM {} GROUP BY fecha").format(viejo)
        ).fetchall()
        conn.execute("DELETE FROM cmg.dia WHERE fecha >= %s AND fecha < %s", (d1, d2))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO cmg.dia (fecha, origen_id, documento, cargado_en, usd_clp) "
                "VALUES (%s, %s, %s, %s, %s)",
                [
                    (
                        f,
                        o,
                        elegidos[f]["nombre"] if isinstance(f, date) and f in elegidos else None,
                        ahora,
                        usd.get(f) if isinstance(f, date) else None,
                    )
                    for f, o in dias
                ],
            )
        # --- Gold del mes, desde los cuartos recien conectados (misma transaccion)
        _recalcular_gold(conn, viejo, d1)

        preliminares = sum(1 for _, o in dias if o == ORIGEN_ID[esquema.ORIGEN_PAGINA_PRE])
        conn.execute(
            """
            INSERT INTO cmg.carga_mes (anio, mes, huella, filas, cargado_en, dias, dias_preliminar)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (anio, mes) DO UPDATE SET
                huella = EXCLUDED.huella, filas = EXCLUDED.filas,
                cargado_en = EXCLUDED.cargado_en, dias = EXCLUDED.dias,
                dias_preliminar = EXCLUDED.dias_preliminar
            """,
            (mes[0], mes[1], huella, filas, ahora, len(dias), preliminares),
        )

    return {
        "anio": mes[0],
        "mes": mes[1],
        "accion": "reemplazado" if habia else "nuevo",
        "filas": filas,
        "dias": len(dias),
        "revisiones": revisiones,
    }


def publicar(
    con: duckdb.DuckDBPyConnection,
    conn: psycopg.Connection[tuple[object, ...]],
    silver: Path,
    bronze: Path,
    staging: Path,
    forzar: bool = False,
    avisar: Avisar = _nada,
    desde: Mes | None = None,
    quitar: bool = False,
) -> list[ResultadoMes]:
    """Sube a PostgreSQL los meses de Silver que cambiaron. Devuelve uno por mes subido.

    Con `desde`, solo de ese mes en adelante. Con `quitar` (y `desde`), antes de
    subir borra de la base lo anterior; sin `quitar`, solo avisa que esta ahi.
    """
    if desde is not None:
        if quitar:
            quitar_anteriores(conn, desde, avisar)
        else:
            viejos = [
                m for m in meses_en_la_base(conn) if periodo.indice(m) < periodo.indice(desde)
            ]
            if viejos:
                avisar(
                    f"Aviso: la base tiene {len(viejos)} mes(es) anteriores a "
                    f"{periodo.formatear(desde)} ({periodo.formatear(viejos[0])} a "
                    f"{periodo.formatear(viejos[-1])}). No se tocan; para borrarlos: "
                    "cmg publicar-sql --quitar-anteriores"
                )
    asegurar_columnas(conn)
    pendientes = meses_pendientes(silver, meses_cargados(conn), forzar, desde)
    if not pendientes:
        avisar("La base ya refleja Silver: nada que subir.")
    resultados: list[ResultadoMes] = []
    for i, (mes, huella) in enumerate(pendientes, start=1):
        r = publicar_mes(con, conn, silver, bronze, staging, mes, huella, datetime.now(UTC))
        resultados.append(r)
        extra = f", {r['revisiones']} dia(s) revisado(s)" if r["revisiones"] else ""
        avisar(
            f"[{i}/{len(pendientes)}] {periodo.formatear(mes)}: {r['filas']:>10,} filas, "
            f"{r['dias']} dias ({r['accion']}{extra})"
        )
    return resultados
