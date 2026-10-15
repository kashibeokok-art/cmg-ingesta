"""Interfaz de linea de comandos.

Es la UNICA capa que imprime y la unica que interactua con el usuario. Todo lo de
abajo (`domain`, `silver`, `gold`, `quality`, `reportes`) recibe datos y devuelve
datos.

Eso resuelve el peor anti-patron del programa anterior: `ingesta.py:234` pedia
confirmacion con `input()` **dentro** de la funcion de ingesta, lo que la volvia
imposible de testear y de automatizar.

Codigos de salida (exit codes), para que un Task Scheduler o un CI sepan que paso:
    0  todo bien
    1  el pedido estaba bien escrito, pero no se pudo cumplir (sitio caido, base
       vacia, barra sin datos...)
    2  el comando esta mal escrito: falta una opcion, una fecha no se entiende...
    3  termino, pero las validaciones encontraron algo que revisar (data/alertas/)

El 2 no es una eleccion libre: es el codigo que usa click (la libreria debajo de
typer) para los errores de uso que detecta ANTES de entrar a la funcion, y es la
convencion de los programas de consola. Antes los hallazgos tambien salian con 2,
y una tarea programada mal escrita se confundia con "hay hallazgos" (error A15).
Por eso los errores de uso que detecta el propio programa tambien salen con 2.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, NoReturn

import duckdb
import psycopg
import typer
from pydantic import ValidationError

from cmg_ingesta import conexion
from cmg_ingesta.config import Settings
from cmg_ingesta.domain import periodo
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import ingerir_cen, migrar
from cmg_ingesta.gold import bloques_mes, riesgo_nodal
from cmg_ingesta.menu import app as menu_app
from cmg_ingesta.menu.consola import Consola
from cmg_ingesta.publicar import postgres as pg
from cmg_ingesta.quality import cobertura, deriva
from cmg_ingesta.reportes import exportar
from cmg_ingesta.silver import leer

SALIDA_OK = 0
SALIDA_ERROR = 1
SALIDA_USO = 2  # el mismo que usa click para "Missing option", "not a valid float"...
SALIDA_CON_HALLAZGOS = 3

MENSAJE_RETOMAR = (
    "Lo ya descargado quedo registrado. Vuelve a correr el mismo comando para retomar: "
    "no se bajara de nuevo."
)

app = typer.Typer(
    help="Base de costos marginales por barra del SEN de Chile. Sin comando, abre el menu.",
    add_completion=False,
)


def _settings() -> Settings:
    """La configuracion. Un `.env` con una CMGI_* desconocida termina con mensaje (1)."""
    try:
        return Settings()
    except ValidationError as e:
        detalle = "; ".join(str(err["msg"]).removeprefix("Value error, ") for err in e.errors())
        typer.echo(f"Error en la configuracion: {detalle}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e


def _exigir_carpeta(cfg: Settings) -> None:
    """Para los comandos que escriben bajo data_dir sin abrir DuckDB (descarga, vigilancia)."""
    try:
        conexion.exigir_carpeta(cfg.data_dir)
        _exigir_propias(cfg)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e


def _exigir_propias(cfg: Settings) -> None:
    """Las carpetas de Silver y Bronze elegidas en el .env (CMGI_SILVER_DIR/BRONZE_DIR)."""
    if cfg.silver_dir is not None:
        conexion.exigir_carpeta_propia(cfg.silver_dir, "CMGI_SILVER_DIR")
    if cfg.bronze_dir is not None:
        conexion.exigir_carpeta_propia(cfg.bronze_dir, "CMGI_BRONZE_DIR")


def _silver(cfg: Settings) -> Path:
    """La carpeta de Silver. Si se eligio una (CMGI_SILVER_DIR) y no esta, termina (1)."""
    try:
        return conexion.ruta_silver(cfg.data_dir, cfg.silver_dir)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e


def _bronze(cfg: Settings) -> Path:
    """La carpeta de Bronze. Si se eligio una (CMGI_BRONZE_DIR) y no esta, termina (1)."""
    try:
        return conexion.ruta_bronze_cen(cfg.data_dir, cfg.bronze_dir)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e


def _conectar(cfg: Settings, crear: bool = False) -> duckdb.DuckDBPyConnection:
    """Abre DuckDB. Si la carpeta de datos no existe, termina con un mensaje claro (1).

    Va fuera del `try` de cada comando, por eso atrapa aqui su propio error.
    """
    try:
        _exigir_propias(cfg)
        return conexion.abrir(cfg.data_dir, crear=crear)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e


def _sesion() -> cen.Sesion:
    """Sesion HTTP real. Es una funcion aparte para que los tests la reemplacen."""
    return cen.nueva_sesion()


def _hoy() -> date:
    """Fecha de hoy. Aparte por lo mismo: los tests fijan el reloj."""
    return date.today()


def _cobertura(con: duckdb.DuckDBPyConnection, cfg: Settings) -> cobertura.Cobertura:
    """Que dias deberia tener la base y cuales tiene. Sin red, ~0,4 s."""
    return cobertura.calcular(
        cobertura.dias_en_base(con, _silver(cfg)),
        cen.leer_manifiesto(_bronze(cfg)),
        hoy=_hoy(),
        desde=cobertura.inicio(cfg.desde),
    )


def _uso(mensaje: str) -> NoReturn:
    """Termina por un comando mal escrito: mismo codigo que los errores de click."""
    typer.echo(mensaje, err=True)
    raise typer.Exit(SALIDA_USO)


def _fecha(texto: str) -> date:
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        _uso(f"Fecha invalida '{texto}'. Formato esperado: AAAA-MM-DD.")


def _validar_pausa(pausa: float) -> None:
    if pausa < cen.PAUSA_MINIMA:
        _uso(f"--pausa debe ser al menos {cen.PAUSA_MINIMA} s (el sitio es de un tercero).")


#: Un rango amplisimo: para saber si un periodo esta BIEN ESCRITO, sin importar
#: que meses tenga la base.
_TODO: tuple[periodo.Mes, periodo.Mes] = ((2000, 1), (2100, 12))


def _periodo(
    texto: str, primero: periodo.Mes, ultimo: periodo.Mes
) -> tuple[periodo.Mes, periodo.Mes]:
    """El periodo pedido. Mal escrito -> 2 (uso); bien escrito pero sin datos -> 1.

    `parsear_periodo` lanza ValueError en los dos casos. Para separarlos sin leer
    el mensaje de error, primero se lee contra un rango amplisimo: si falla ahi,
    el problema es como se escribio.
    """
    try:
        periodo.parsear_periodo(texto, *_TODO)
    except ValueError as e:
        _uso(f"Error: {e}")
    try:
        return periodo.parsear_periodo(texto, primero, ultimo)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e


def _informar(hallazgos: list[deriva.Hallazgo], cfg: Settings, titulo: str) -> None:
    """Resume los hallazgos en pantalla, escribe el reporte y decide el exit code."""
    rutas = deriva.escribir_reporte(hallazgos, conexion.ruta_alertas(cfg.data_dir), titulo)
    if rutas is None:
        typer.echo("Sin hallazgos: la fuente se ve igual que lo que el programa conoce.")
        return

    for h in sorted(hallazgos, key=lambda h: (deriva.ORDEN_SEVERIDAD[h["severidad"]], h["tipo"])):
        typer.echo(f"  [{h['severidad']:<7}] {h['tipo']:<26} {h['evidencia']}")
    typer.echo(f"\nReporte: {rutas[0]}")
    if deriva.hay_que_revisar(hallazgos):
        typer.echo("Hay avisos o criticos: revisar el reporte.", err=True)
        raise typer.Exit(SALIDA_CON_HALLAZGOS)


@app.callback(invoke_without_command=True)
def principal(contexto: typer.Context) -> None:
    """Sin subcomando abre el menu: es lo que pasa al hacer doble clic en el programa."""
    if contexto.invoked_subcommand is None:
        menu()


@app.command()
def menu() -> None:
    """Abre el menu interactivo, con busqueda de barras y seleccion multiple."""
    cfg = _settings()
    try:
        _exigir_propias(cfg)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    contexto = menu_app.Contexto(
        cfg=cfg,
        consola=Consola(),
        nueva_sesion=_sesion,
        hoy=_hoy,
        limpiar=menu_app.limpiar_pantalla,
    )
    try:
        menu_app.ejecutar(contexto)
    except KeyboardInterrupt:
        typer.echo("")


@app.command()
def estado() -> None:
    """Muestra que datos tiene la base."""
    cfg = _settings()
    base = _silver(cfg)
    if not leer.base_existe(base):
        typer.echo(f"La base esta vacia. Esperada en: {base}")
        typer.echo("Corre primero:  cmg migrar-historico <carpeta CMG_DB>")
        raise typer.Exit(SALIDA_ERROR)

    con = _conectar(cfg)
    try:
        res = leer.resumen_base(con, base)
        cob = _cobertura(con, cfg)
    finally:
        con.close()

    typer.echo(
        f"  periodo   : {res['desde']} a {res['hasta']}  "
        f"({res['meses_con_datos']} de {res['meses']} meses con datos)"
    )
    typer.echo(f"  barras    : {res['barras']:,}")
    typer.echo(f"  filas     : {res['filas']:,}")
    typer.echo(f"  ruta      : {base}")
    for linea in cobertura.lineas_estado(cob):
        typer.echo(linea)


@app.command()
def migrar_historico(
    origen: Annotated[
        Path,
        typer.Argument(help="Carpeta CMG_DB de la base antigua (formato anio=/mes=)."),
    ],
) -> None:
    """Migra el historico 2021-01 a 2024-07 desde CMG_DB, validando el calendario."""
    cfg = _settings()
    if not origen.is_dir():
        _uso(f"No existe la carpeta: {origen}")

    destino = _silver(cfg)
    con = _conectar(cfg, crear=True)  # la unica que construye la base desde cero
    try:
        reporte = migrar.migrar_historico(con, origen, destino, avisar=typer.echo)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    finally:
        con.close()

    tot = migrar.totales(reporte)
    typer.echo("")
    for clave, valor in tot.items():
        typer.echo(f"  {clave:<16} {valor:>14,}")

    hallazgos = tot["dias_mal"] + tot["horas_mal"] + tot["duplicados"] + tot["nulos"]
    if hallazgos or tot["hora_fantasma"]:
        typer.echo("")
        typer.echo("Las validaciones encontraron problemas. Detalle por mes:")
        for fila in reporte:
            det = fila["detalle_largo"]
            if len(det):
                typer.echo(f"\n{fila['anio']}-{fila['mes']:02d}:")
                typer.echo(det.to_string(index=False))
        raise typer.Exit(SALIDA_CON_HALLAZGOS)


@app.command()
def barras(
    buscar: Annotated[str, typer.Argument(help="Parte del nombre a buscar.")] = "",
    limite: Annotated[int, typer.Option(help="Cuantas mostrar.")] = 40,
) -> None:
    """Lista las barras de la base, filtrando por nombre."""
    cfg = _settings()
    base = _silver(cfg)
    con = _conectar(cfg)
    try:
        todas = leer.barras(con, base)
    finally:
        con.close()

    aguja = buscar.strip().upper()
    halladas = [b for b in todas if aguja in b.upper()] if aguja else todas
    typer.echo(f"{len(halladas):,} de {len(todas):,} barras")
    for b in halladas[:limite]:
        typer.echo(f"  {b}")
    if len(halladas) > limite:
        typer.echo(f"  ... y {len(halladas) - limite:,} mas")


@app.command()
def descargar(
    barra: Annotated[str, typer.Argument(help="Nombre exacto de la barra.")],
    periodo_texto: Annotated[
        str,
        typer.Option(
            "--periodo",
            help="'2025' | '2025-03' | '2025-03 a 2025-08' | 'ultimos 6' | vacio = todo",
        ),
    ] = "",
    formato: Annotated[
        str, typer.Option(help="excel, csv, parquet. Varios separados por coma.")
    ] = "excel",
) -> None:
    """Exporta el CMg quinceminutal de una barra, con el resumen por bloques."""
    formatos = tuple(f.strip() for f in formato.split(",") if f.strip())
    desconocidos = sorted(set(formatos) - set(exportar.FORMATOS))
    if not formatos or desconocidos:
        _uso(
            f"Error: formato no soportado: {desconocidos}. Validos: {', '.join(exportar.FORMATOS)}"
        )
    cfg = _settings()
    base = _silver(cfg)
    con = _conectar(cfg)
    try:
        primero, ultimo = leer.rango_disponible(con, base)
        desde, hasta = _periodo(periodo_texto, primero, ultimo)
        rutas = exportar.exportar_cmg(
            con,
            base,
            barra,
            desde,
            hasta,
            conexion.ruta_descargas(cfg.data_dir),
            formatos=formatos,  # type: ignore[arg-type]
        )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    finally:
        con.close()

    typer.echo(f"{barra}  {periodo.formatear(desde)} a {periodo.formatear(hasta)}")
    for r in rutas:
        typer.echo(f"  {r.stat().st_size / 1024 / 1024:>7.2f} MB  {r}")


@app.command()
def bloques(
    barra: Annotated[str, typer.Argument(help="Nombre exacto de la barra.")],
    periodo_texto: Annotated[str, typer.Option("--periodo")] = "",
) -> None:
    """Muestra en pantalla el promedio mensual por bloque de una barra."""
    cfg = _settings()
    base = _silver(cfg)
    con = _conectar(cfg)
    try:
        primero, ultimo = leer.rango_disponible(con, base)
        desde, hasta = _periodo(periodo_texto, primero, ultimo)
        df = bloques_mes.resumen_mensual(con, base, barra, desde, hasta)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    finally:
        con.close()

    if df.empty:
        typer.echo(f"No hay datos de {barra} en ese periodo.", err=True)
        raise typer.Exit(SALIDA_ERROR)
    typer.echo(df.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))


@app.command()
def riesgo(
    referencia: Annotated[str, typer.Argument(help="Barra de referencia.")],
    comparada: Annotated[str, typer.Argument(help="Barra a comparar.")],
    periodo_texto: Annotated[str, typer.Option("--periodo")] = "",
) -> None:
    """Riesgo nodal entre dos barras: diferencia de CMg en USD y en porcentaje."""
    cfg = _settings()
    base = _silver(cfg)
    con = _conectar(cfg)
    try:
        primero, ultimo = leer.rango_disponible(con, base)
        desde, hasta = _periodo(periodo_texto, primero, ultimo)
        df = riesgo_nodal.resumen_riesgo(con, base, referencia, comparada, desde, hasta)
        ceros, total = riesgo_nodal.intervalos_sin_porcentaje(con, base, referencia, desde, hasta)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    finally:
        con.close()

    if df.empty:
        typer.echo("No hay intervalos comparables en ese periodo.", err=True)
        raise typer.Exit(SALIDA_ERROR)

    typer.echo(f"riesgo = CMg({comparada}) - CMg({referencia})")
    typer.echo("positivo = la comparada es mas cara\n")
    typer.echo(df.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    if total:
        pct = 100 * ceros / total
        typer.echo(
            f"\nLa referencia vale 0 en {ceros:,} de {total:,} intervalos ({pct:.1f}%): "
            "ahi el porcentaje por intervalo no existe."
        )


@app.command()
def descargar_cen(
    desde: Annotated[str, typer.Option(help="Primer dia, AAAA-MM-DD.")],
    hasta: Annotated[str, typer.Option(help="Ultimo dia, AAAA-MM-DD. Vacio = ayer.")] = "",
    pausa: Annotated[float, typer.Option(help="Segundos entre peticiones (minimo 1).")] = 1.0,
    omitir_chequeo: Annotated[
        bool,
        typer.Option(help="Descargar aunque el sitio no calce con config/linea_base_cen.toml."),
    ] = False,
) -> None:
    """Descarga los ZIP del CMg real desde la pagina del Coordinador (Bronze).

    Primero compara el sitio con la linea base (config/linea_base_cen.toml): si
    algo estructural cambio, se detiene sin bajar nada. Es idempotente y
    reanudable. Mientras descarga revisa nombres, columnas y la forma del dia, y
    anota los enlaces que no reconoce en nombres_no_reconocidos.csv.
    """
    cfg = _settings()
    d1 = _fecha(desde)
    d2 = _fecha(hasta) if hasta else _hoy() - timedelta(days=1)
    if d1 > d2:
        _uso(f"--desde ({d1}) es posterior a --hasta ({d2}).")
    _validar_pausa(pausa)
    _exigir_carpeta(cfg)

    carpeta = _bronze(cfg)
    typer.echo(f"Descargando {d1} a {d2} en {carpeta}")
    try:
        nuevos, hallazgos = deriva.sincronizar_vigilando(
            d1, d2, carpeta, _sesion(), pausa=pausa, hoy=_hoy(), chequear=not omitir_chequeo
        )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    except cen.ErrorDescarga as e:
        typer.echo(f"El sitio no respondio: {e}", err=True)
        typer.echo(MENSAJE_RETOMAR, err=True)
        raise typer.Exit(SALIDA_ERROR) from e

    for n in nuevos:
        typer.echo(f"  + {n['nombre']}  ({n['bytes'] / 1024 / 1024:.1f} MB)")
    typer.echo(f"{len(nuevos)} archivo(s) nuevo(s).\n")
    _informar(hallazgos, cfg, f"Descarga CEN {d1} a {d2}")


@app.command()
def ingerir_pagina(
    forzar: Annotated[
        bool, typer.Option(help="Reescribir todos los meses aunque no hayan cambiado.")
    ] = False,
    mes: Annotated[
        list[str] | None,
        typer.Option(help="Solo este mes (AAAA-MM), aunque no haya cambiado. Se puede repetir."),
    ] = None,
) -> None:
    """Pasa a Silver los ZIP del Coordinador ya descargados (2024-08 en adelante).

    Incremental: solo reescribe los meses cuyos archivos cambiaron. Un dia con
    hallazgo critico no entra y se reporta. Con CMGI_CONSERVAR_ZIP=false en el
    .env, cada ZIP que queda en Silver se reemplaza por un testigo liviano.
    """
    try:
        meses = [periodo.parsear_mes(m) for m in mes] if mes else None
    except ValueError as e:
        _uso(f"Error: {e}")
    cfg = _settings()
    con = _conectar(cfg)
    try:
        reporte, hallazgos = ingerir_cen.ingerir_pagina(
            con,
            _bronze(cfg),
            _silver(cfg),
            conexion.ruta_staging(cfg.data_dir),
            avisar=typer.echo,
            forzar=forzar,
            conservar_zip=cfg.conservar_zip,
            meses=meses,
        )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    finally:
        con.close()

    if not reporte:
        typer.echo("Nada que ingerir: Silver ya refleja el Bronze.")
    for fila in reporte:
        for motivo in fila["omitidos"]:
            typer.echo(f"  [omitido] {motivo}")
        if len(fila["detalle_largo"]):
            typer.echo(f"\n{fila['anio']}-{fila['mes']:02d}, dias con largo incorrecto:")
            typer.echo(fila["detalle_largo"].to_string(index=False))

    if hallazgos:
        _informar(hallazgos, cfg, "Ingesta de la pagina a Silver")
    if ingerir_cen.hay_problemas(reporte):
        typer.echo("La ingesta encontro problemas: revisar arriba.", err=True)
        raise typer.Exit(SALIDA_CON_HALLAZGOS)


@app.command()
def publicar_sql(
    forzar: Annotated[
        bool, typer.Option(help="Volver a subir todos los meses aunque no hayan cambiado.")
    ] = False,
    quitar_anteriores: Annotated[
        bool,
        typer.Option(help="Antes de subir, borrar de la base los meses anteriores a CMGI_DESDE."),
    ] = False,
) -> None:
    """Sube Silver a PostgreSQL (base cmg_cen), solo los meses que cambiaron.

    Registra que documento hay en cada dia y, si un dia ya cargado cambia de
    version (pre a def, def a v2), cuanto cambio el CMg. Necesita CMGI_PG_URL en el
    .env, con el usuario cmg_carga. El esquema se crea antes con sql/cmg_cen.sql.
    Con CMGI_DESDE=AAAA-MM en el .env, solo sube de ese mes en adelante.
    """
    cfg = _settings()
    desde = None if cfg.desde is None else periodo.parsear_mes(cfg.desde)
    if quitar_anteriores and desde is None:
        _uso("--quitar-anteriores necesita CMGI_DESDE en el .env (por ejemplo 2023-01).")
    if cfg.pg_url is None:
        typer.echo(
            "Falta CMGI_PG_URL en el .env: la conexion a cmg_cen con el usuario cmg_carga, "
            "por ejemplo postgresql://cmg_carga:CLAVE@192.168.0.85:5433/cmg_cen",
            err=True,
        )
        raise typer.Exit(SALIDA_ERROR)
    try:
        conn = pg.conectar(cfg.pg_url.get_secret_value())
    except psycopg.OperationalError as e:
        # el mensaje de psycopg no trae la clave; la URL no se imprime
        typer.echo(f"No se pudo conectar a PostgreSQL: {str(e).strip()}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    con = _conectar(cfg)
    try:
        with conn:
            resultados = pg.publicar(
                con,
                conn,
                _silver(cfg),
                _bronze(cfg),
                conexion.ruta_staging(cfg.data_dir),
                forzar=forzar,
                avisar=typer.echo,
                desde=desde,
                quitar=quitar_anteriores,
            )
    except psycopg.errors.UndefinedTable as e:
        typer.echo("La base no tiene el esquema: corre antes sql/cmg_cen.sql.", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    except psycopg.Error as e:
        # conectada, pero la base rechazo una operacion: el mes en curso se deshizo entero
        typer.echo(
            f"La base rechazo una operacion: {str(e).strip()}\n"
            "El mes en curso no quedo a medias (se deshizo). Lo ya subido queda; para "
            "retomar, vuelve a correr el mismo comando.",
            err=True,
        )
        raise typer.Exit(SALIDA_ERROR) from e
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    finally:
        con.close()

    if resultados:
        filas = sum(r["filas"] for r in resultados)
        revisados = sum(r["revisiones"] for r in resultados)
        typer.echo("")
        typer.echo(
            f"{len(resultados)} mes(es) subido(s), {filas:,} filas; "
            f"{revisados} dia(s) con revision registrada."
        )


@app.command()
def vigilar_fuente(
    dias: Annotated[int, typer.Option(help="Cuantos dias recientes revisar en la web.")] = 7,
    pausa: Annotated[float, typer.Option(help="Segundos entre peticiones (minimo 1).")] = 1.0,
) -> None:
    """Revisa si el Coordinador cambio algo, sin descargar ZIP.

    Pensado para una tarea programada: exit code 3 si hay algo que revisar.
    Ademas revisa todo lo descargado: dias sin archivo y preliminares viejos.
    """
    cfg = _settings()
    if dias < 1:
        _uso("--dias debe ser al menos 1.")
    _validar_pausa(pausa)
    _exigir_carpeta(cfg)
    hoy = _hoy()
    try:
        hallazgos = deriva.vigilar_fuente(
            _sesion(),
            _bronze(cfg),
            hasta=hoy - timedelta(days=1),
            dias=dias,
            pausa=pausa,
            hoy=hoy,
        )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    except cen.ErrorDescarga as e:
        typer.echo(f"El sitio no respondio: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e

    # lo que solo Silver puede ver: huecos del Maestro y dias bajados sin ingerir
    con = _conectar(cfg)
    try:
        hallazgos += cobertura.revisar(_cobertura(con, cfg))
    finally:
        con.close()
    _informar(hallazgos, cfg, "Vigilancia de la fuente")


if __name__ == "__main__":
    app()
