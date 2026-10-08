"""Interfaz de linea de comandos.

Es la UNICA capa que imprime y la unica que interactua con el usuario. Todo lo de
abajo (`domain`, `silver`, `gold`, `quality`, `reportes`) recibe datos y devuelve
datos.

Eso resuelve el peor anti-patron del programa anterior: `ingesta.py:234` pedia
confirmacion con `input()` **dentro** de la funcion de ingesta, lo que la volvia
imposible de testear y de automatizar.

Codigos de salida (exit codes), para que un Task Scheduler o un CI sepan que paso:
    0  todo bien
    1  error de uso o del programa
    2  termino, pero las validaciones encontraron problemas
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import duckdb
import typer

from cmg_ingesta import conexion
from cmg_ingesta.config import Settings
from cmg_ingesta.domain import periodo
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import ingerir_cen, migrar
from cmg_ingesta.gold import bloques_mes, riesgo_nodal
from cmg_ingesta.menu import app as menu_app
from cmg_ingesta.menu.consola import Consola
from cmg_ingesta.quality import cobertura, deriva
from cmg_ingesta.reportes import exportar
from cmg_ingesta.silver import leer

SALIDA_OK = 0
SALIDA_ERROR = 1
SALIDA_CON_HALLAZGOS = 2

MENSAJE_RETOMAR = (
    "Lo ya descargado quedo registrado. Vuelve a correr el mismo comando para retomar: "
    "no se bajara de nuevo."
)

app = typer.Typer(
    help="Base de costos marginales por barra del SEN de Chile. Sin comando, abre el menu.",
    add_completion=False,
)


def _settings() -> Settings:
    return Settings()


def _abrir(cfg: Settings) -> tuple[object, Path]:
    con = conexion.abrir(cfg.data_dir)
    return con, conexion.ruta_silver(cfg.data_dir)


def _sesion() -> cen.Sesion:
    """Sesion HTTP real. Es una funcion aparte para que los tests la reemplacen."""
    return cen.nueva_sesion()


def _hoy() -> date:
    """Fecha de hoy. Aparte por lo mismo: los tests fijan el reloj."""
    return date.today()


def _cobertura(con: duckdb.DuckDBPyConnection, cfg: Settings) -> cobertura.Cobertura:
    """Que dias deberia tener la base y cuales tiene. Sin red, ~0,4 s."""
    return cobertura.calcular(
        cobertura.dias_en_base(con, conexion.ruta_silver(cfg.data_dir)),
        cen.leer_manifiesto(conexion.ruta_bronze_cen(cfg.data_dir)),
        hoy=_hoy(),
    )


def _fecha(texto: str) -> date:
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError as e:
        typer.echo(f"Fecha invalida '{texto}'. Formato esperado: AAAA-MM-DD.", err=True)
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
    contexto = menu_app.Contexto(
        cfg=_settings(),
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
    base = conexion.ruta_silver(cfg.data_dir)
    if not leer.base_existe(base):
        typer.echo(f"La base esta vacia. Esperada en: {base}")
        typer.echo("Corre primero:  cmg migrar-historico <carpeta CMG_DB>")
        raise typer.Exit(SALIDA_ERROR)

    con = conexion.abrir(cfg.data_dir)
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
        typer.echo(f"No existe la carpeta: {origen}", err=True)
        raise typer.Exit(SALIDA_ERROR)

    destino = conexion.ruta_silver(cfg.data_dir)
    con = conexion.abrir(cfg.data_dir)
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
    base = conexion.ruta_silver(cfg.data_dir)
    con = conexion.abrir(cfg.data_dir)
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
    cfg = _settings()
    base = conexion.ruta_silver(cfg.data_dir)
    con = conexion.abrir(cfg.data_dir)
    try:
        primero, ultimo = leer.rango_disponible(con, base)
        desde, hasta = periodo.parsear_periodo(periodo_texto, primero, ultimo)
        formatos = tuple(f.strip() for f in formato.split(",") if f.strip())
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
    base = conexion.ruta_silver(cfg.data_dir)
    con = conexion.abrir(cfg.data_dir)
    try:
        primero, ultimo = leer.rango_disponible(con, base)
        desde, hasta = periodo.parsear_periodo(periodo_texto, primero, ultimo)
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
    base = conexion.ruta_silver(cfg.data_dir)
    con = conexion.abrir(cfg.data_dir)
    try:
        primero, ultimo = leer.rango_disponible(con, base)
        desde, hasta = periodo.parsear_periodo(periodo_texto, primero, ultimo)
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
) -> None:
    """Descarga los ZIP del CMg real desde la pagina del Coordinador (Bronze).

    Es idempotente: lo ya descargado no se vuelve a bajar. Mientras descarga
    revisa si el Coordinador cambio nombres, columnas o la forma del dia, y al
    final avisa de dias sin archivo o que siguen solo con preliminar.
    """
    cfg = _settings()
    d1 = _fecha(desde)
    d2 = _fecha(hasta) if hasta else _hoy() - timedelta(days=1)
    if d1 > d2:
        typer.echo(f"--desde ({d1}) es posterior a --hasta ({d2}).", err=True)
        raise typer.Exit(SALIDA_ERROR)

    carpeta = conexion.ruta_bronze_cen(cfg.data_dir)
    typer.echo(f"Descargando {d1} a {d2} en {carpeta}")
    try:
        nuevos, hallazgos = deriva.sincronizar_vigilando(
            d1, d2, carpeta, _sesion(), pausa=pausa, hoy=_hoy()
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
) -> None:
    """Pasa a Silver los ZIP del Coordinador ya descargados (2024-08 en adelante).

    Incremental: solo reescribe los meses cuyos archivos cambiaron. Un dia con
    hallazgo critico no entra y se reporta.
    """
    cfg = _settings()
    con = conexion.abrir(cfg.data_dir)
    try:
        reporte, hallazgos = ingerir_cen.ingerir_pagina(
            con,
            conexion.ruta_bronze_cen(cfg.data_dir),
            conexion.ruta_silver(cfg.data_dir),
            conexion.ruta_staging(cfg.data_dir),
            avisar=typer.echo,
            forzar=forzar,
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
def vigilar_fuente(
    dias: Annotated[int, typer.Option(help="Cuantos dias recientes revisar en la web.")] = 7,
    pausa: Annotated[float, typer.Option(help="Segundos entre peticiones (minimo 1).")] = 1.0,
) -> None:
    """Revisa si el Coordinador cambio algo, sin descargar ZIP.

    Pensado para una tarea programada: exit code 2 si hay algo que revisar.
    Ademas revisa todo lo descargado: dias sin archivo y preliminares viejos.
    """
    cfg = _settings()
    if dias < 1:
        typer.echo("--dias debe ser al menos 1.", err=True)
        raise typer.Exit(SALIDA_ERROR)
    hoy = _hoy()
    try:
        hallazgos = deriva.vigilar_fuente(
            _sesion(),
            conexion.ruta_bronze_cen(cfg.data_dir),
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
    con = conexion.abrir(cfg.data_dir)
    try:
        hallazgos += cobertura.revisar(_cobertura(con, cfg))
    finally:
        con.close()
    _informar(hallazgos, cfg, "Vigilancia de la fuente")


if __name__ == "__main__":
    app()
