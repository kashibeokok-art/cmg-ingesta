"""El menu interactivo: la cara del programa para el uso diario.

Reemplaza a `CMG_Build/src/CMG.py`, con el mismo estilo (numeros, busqueda por
texto, 'v' para volver) pero solo con lo que es CMg (CLAUDE.md §0).

Los comandos de `cli.py` siguen existiendo para automatizar (tareas programadas,
scripts). El menu es para una persona frente a la pantalla. Ninguno de los dos
tiene logica de negocio: los dos llaman a las mismas funciones de `silver`,
`gold`, `reportes`, `extract` y `quality`.

Cada flujo es una pequeña maquina de estados (state machine): una variable
`paso` dice en que pregunta se esta, y 'v' retrocede un paso sin perder lo ya
respondido. Es el mismo patron de `CMG_Build/src/descargar.py: ejecutar()`.
"""

import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from cmg_ingesta import conexion
from cmg_ingesta.config import Settings
from cmg_ingesta.domain import periodo
from cmg_ingesta.domain.periodo import Mes
from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import ingerir_cen
from cmg_ingesta.gold import bloques_mes
from cmg_ingesta.menu.consola import (
    Consola,
    Nav,
    elegir_una,
    elegir_varias,
    pedir_formatos,
    pedir_periodo,
)
from cmg_ingesta.quality import cobertura, deriva
from cmg_ingesta.reportes import exportar
from cmg_ingesta.reportes.exportar import Formato
from cmg_ingesta.silver import leer

ANCHO = 72


def limpiar_pantalla() -> None:
    os.system("cls" if os.name == "nt" else "clear")


@dataclass
class Contexto:
    """Todo lo que necesita un flujo del menu.

    La sesion HTTP y la fecha de hoy se inyectan por lo mismo que la consola: los
    tests no tocan la red ni dependen del dia en que se corren.
    """

    cfg: Settings
    consola: Consola
    nueva_sesion: Callable[[], cen.Sesion] = cen.nueva_sesion
    hoy: Callable[[], date] = date.today
    limpiar: Callable[[], None] = field(default=lambda: None)

    @property
    def silver(self) -> Path:
        return conexion.ruta_silver(self.cfg.data_dir)

    @property
    def bronze_cen(self) -> Path:
        return conexion.ruta_bronze_cen(self.cfg.data_dir)

    @property
    def descargas(self) -> Path:
        return conexion.ruta_descargas(self.cfg.data_dir)

    def abrir(self) -> duckdb.DuckDBPyConnection:
        return conexion.abrir(self.cfg.data_dir)


# ------------------------------------------------------------------- cabecera


def estado_base(ctx: Contexto) -> list[str]:
    """Las lineas de estado de la cabecera. Rapido: Parquet guarda los conteos."""
    lineas: list[str] = []
    manifiesto = cen.leer_manifiesto(ctx.bronze_cen)
    if leer.base_existe(ctx.silver):
        con = ctx.abrir()
        try:
            r = leer.resumen_base(con, ctx.silver)
            cob = _cobertura(ctx, con, manifiesto)
        finally:
            con.close()
        lineas.append(
            f"  Base CMg   : {r['desde']} a {r['hasta']}  |  {r['barras']:,} barras  |  "
            f"{r['filas']:,} registros"
        )
        faltan = cobertura.total_faltantes(cob)
        if faltan:
            tramos = [
                deriva.texto_rango(a, b)
                for dias in cob["faltantes"].values()
                for a, b in deriva.rangos(dias)
            ]
            resumen = ", ".join(tramos[:3]) + (" ..." if len(tramos) > 3 else "")
            lineas.append(f"  FALTAN     : {faltan:,} dia(s): {resumen}  (opcion 4)")
    else:
        lineas.append("  Base CMg   : (vacia)")

    if manifiesto:
        dias = {e["fecha_operacion"] for e in manifiesto.values()}
        lineas.append(
            f"  Pagina CEN : {len(manifiesto):,} ZIP descargados, {len(dias):,} dias, "
            f"ultimo {max(dias)}"
        )
    else:
        lineas.append("  Pagina CEN : nada descargado todavia (opcion 4)")
    return lineas


# ------------------------------------------------------------------- comunes


def _cobertura(
    ctx: Contexto, con: duckdb.DuckDBPyConnection, manifiesto: dict[str, cen.EntradaManifiesto]
) -> cobertura.Cobertura:
    return cobertura.calcular(cobertura.dias_en_base(con, ctx.silver), manifiesto, hoy=ctx.hoy())


def _rango_y_barras(ctx: Contexto, con: duckdb.DuckDBPyConnection) -> tuple[Mes, Mes, list[str]]:
    primero, ultimo = leer.rango_disponible(con, ctx.silver)
    return primero, ultimo, leer.barras(con, ctx.silver)


def _texto_rango(desde: Mes, hasta: Mes) -> str:
    return f"{periodo.formatear(desde)} a {periodo.formatear(hasta)}"


def _mostrar_rutas(ctx: Contexto, rutas: list[Path]) -> None:
    for r in rutas:
        ctx.consola.decir(f"    {r.stat().st_size / 1024 / 1024:>7.2f} MB  {r.name}")


def _tabla(df: pd.DataFrame) -> str:
    return df.to_string(index=False, float_format=lambda x: f"{x:,.2f}")


def _mostrar_hallazgos(ctx: Contexto, hallazgos: list[deriva.Hallazgo], titulo: str) -> None:
    """Igual que `cli._informar`, pero sin codigos de salida: aqui hay una persona."""
    rutas = deriva.escribir_reporte(hallazgos, conexion.ruta_alertas(ctx.cfg.data_dir), titulo)
    if rutas is None:
        ctx.consola.decir("  Sin hallazgos: la fuente se ve igual que lo que el programa conoce.")
        return
    orden = sorted(hallazgos, key=lambda h: (deriva.ORDEN_SEVERIDAD[h["severidad"]], h["tipo"]))
    for h in orden:
        ctx.consola.decir(f"  [{h['severidad']:<7}] {h['tipo']:<26} {h['evidencia']}")
    ctx.consola.decir(f"\n  Reporte: {rutas[0]}")
    if deriva.hay_que_revisar(hallazgos):
        ctx.consola.decir("  ATENCION: hay avisos o criticos, revisar el reporte.")


# ------------------------------------------------------------------- 1. descargar


def descargar(ctx: Contexto) -> None:
    """CMg de 15 minutos de una o varias barras, con resumen por bloques."""
    c = ctx.consola
    con = ctx.abrir()
    try:
        primero, ultimo, barras = _rango_y_barras(ctx, con)
        seleccion: list[str] = []
        rango: tuple[Mes, Mes] = (primero, ultimo)
        formatos: tuple[Formato, ...] = ("excel",)
        paso = "barras"
        while True:
            if paso == "barras":
                c.decir("\n  BARRAS A DESCARGAR")
                r_barras = elegir_varias(c, barras, "Barra", previas=seleccion)
                if r_barras is Nav.VOLVER or not r_barras:
                    c.decir("  Cancelado.")
                    return
                seleccion, paso = r_barras, "periodo"
            elif paso == "periodo":
                r_periodo = pedir_periodo(c, primero, ultimo)
                if r_periodo is None:
                    return
                if r_periodo is Nav.VOLVER:
                    paso = "barras"
                    continue
                rango, paso = r_periodo, "formatos"
            elif paso == "formatos":
                r_formatos = pedir_formatos(c)
                if r_formatos is None:
                    return
                if r_formatos is Nav.VOLVER:
                    paso = "periodo"
                    continue
                formatos, paso = r_formatos, "confirmar"
            else:
                prev = leer.resumen_barras(con, ctx.silver, seleccion, *rango)
                if prev.empty:
                    c.decir("  Ninguna de esas barras tiene datos en ese periodo.")
                    paso = "periodo"
                    continue
                c.decir(f"\n  Periodo {_texto_rango(*rango)}  |  formatos: {', '.join(formatos)}")
                c.decir(_tabla(prev))
                faltan = sorted(set(seleccion) - set(prev["barra"]))
                if faltan:
                    c.decir(f"  Sin datos en el periodo (se omiten): {', '.join(faltan)}")
                respuesta = c.preguntar("\n  [s] generar  [v] volver  [Enter] cancelar: ") or ""
                if respuesta.lower() in ("v", "volver"):
                    paso = "formatos"
                    continue
                if respuesta.lower() not in ("s", "si", "sí"):
                    c.decir("  Cancelado.")
                    return
                for barra in prev["barra"]:
                    rutas = exportar.exportar_cmg(
                        con, ctx.silver, barra, *rango, ctx.descargas, formatos=formatos
                    )
                    c.decir(f"  {barra}:")
                    _mostrar_rutas(ctx, rutas)
                c.decir(f"\n  Listo. Archivos en: {ctx.descargas.resolve()}")
                return
    finally:
        con.close()


# ------------------------------------------------------------------- 2. bloques


def ver_bloques(ctx: Contexto) -> None:
    """Promedio mensual por bloque de una barra, o el total de varias lado a lado."""
    c = ctx.consola
    con = ctx.abrir()
    try:
        primero, ultimo, barras = _rango_y_barras(ctx, con)
        seleccion: list[str] = []
        while True:
            c.decir("\n  Una barra = detalle A/B/C/Solar/NoSolar.  Varias = comparacion del total.")
            r_barras = elegir_varias(c, barras, "Barra", previas=seleccion)
            if r_barras is Nav.VOLVER or not r_barras:
                return
            seleccion = r_barras
            r_periodo = pedir_periodo(c, primero, ultimo)
            if r_periodo is None:
                return
            if r_periodo is Nav.VOLVER:
                continue
            if len(seleccion) == 1:
                df = bloques_mes.resumen_mensual(con, ctx.silver, seleccion[0], *r_periodo)
                df = df.drop(columns=["fecha"])
            else:
                df = bloques_mes.comparar_barras(con, ctx.silver, seleccion, *r_periodo)
                df = df.drop(columns=["fecha"])
            if df.empty:
                c.decir("  No hay datos en ese periodo.")
                continue
            c.decir(f"\n  USD/MWh, {_texto_rango(*r_periodo)}")
            c.decir(_tabla(df))
            return
    finally:
        con.close()


# ------------------------------------------------------------------- 3. riesgo


def riesgo(ctx: Contexto) -> None:
    """Riesgo nodal de una barra de referencia contra una o varias barras."""
    c = ctx.consola
    con = ctx.abrir()
    try:
        primero, ultimo, barras = _rango_y_barras(ctx, con)
        referencia: str = ""
        comparadas: list[str] = []
        rango: tuple[Mes, Mes] = (primero, ultimo)
        formatos: tuple[Formato, ...] = ("excel",)
        paso = "referencia"
        while True:
            if paso == "referencia":
                c.decir("\n  BARRA DE REFERENCIA (contra la que se compara)")
                r_ref = elegir_una(c, barras, "Referencia")
                if r_ref is None or r_ref is Nav.VOLVER:
                    c.decir("  Cancelado.")
                    return
                referencia, paso = r_ref, "comparadas"
                c.decir(f"  Referencia: {referencia}")
            elif paso == "comparadas":
                c.decir("\n  BARRA(S) A COMPARAR")
                otras = [b for b in barras if b != referencia]
                r_comp = elegir_varias(c, otras, "Comparada", previas=comparadas)
                if r_comp is Nav.VOLVER:
                    paso = "referencia"
                    continue
                if not r_comp:
                    c.decir("  Cancelado.")
                    return
                comparadas, paso = r_comp, "periodo"
            elif paso == "periodo":
                r_periodo = pedir_periodo(c, primero, ultimo)
                if r_periodo is None:
                    return
                if r_periodo is Nav.VOLVER:
                    paso = "comparadas"
                    continue
                rango, paso = r_periodo, "formatos"
            elif paso == "formatos":
                r_formatos = pedir_formatos(c)
                if r_formatos is None:
                    return
                if r_formatos is Nav.VOLVER:
                    paso = "periodo"
                    continue
                formatos, paso = r_formatos, "confirmar"
            else:
                c.decir(f"\n  riesgo = CMg(comparada) - CMg({referencia})")
                c.decir("  positivo = la comparada es mas cara")
                c.decir(
                    f"  {len(comparadas)} comparada(s), {_texto_rango(*rango)}, "
                    f"formatos: {', '.join(formatos)}"
                )
                respuesta = c.preguntar("\n  [s] generar  [v] volver  [Enter] cancelar: ") or ""
                if respuesta.lower() in ("v", "volver"):
                    paso = "formatos"
                    continue
                if respuesta.lower() not in ("s", "si", "sí"):
                    c.decir("  Cancelado.")
                    return
                for comp in comparadas:
                    try:
                        rutas = exportar.exportar_riesgo(
                            con, ctx.silver, referencia, comp, *rango, ctx.descargas, formatos
                        )
                    except ValueError as e:
                        c.decir(f"  {comp}: {e}")
                        continue
                    c.decir(f"  {comp}:")
                    _mostrar_rutas(ctx, rutas)
                c.decir(f"\n  Listo. Archivos en: {ctx.descargas.resolve()}")
                return
    finally:
        con.close()


# ------------------------------------------------------------------- 4. actualizar


def _leer_fecha(texto: str) -> date | None:
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


def actualizar(ctx: Contexto) -> None:
    """Descarga lo nuevo de la pagina del CEN y lo pasa a la base."""
    c = ctx.consola
    hoy = ctx.hoy()
    ayer = hoy - timedelta(days=1)
    sugerido = cen.desde_sugerido(cen.leer_manifiesto(ctx.bronze_cen), ayer)

    c.decir("\n  ACTUALIZAR DESDE LA PAGINA DEL COORDINADOR")
    c.decir(f"  Sugerido: desde {sugerido} hasta {ayer} (lo nuevo + los dias que siguen en 'pre').")
    while True:
        texto = c.preguntar(f"  Desde (AAAA-MM-DD, Enter = {sugerido}, v volver): ")
        if texto is None or texto.lower() in ("v", "volver"):
            return
        desde = _leer_fecha(texto) if texto else sugerido
        if desde is None:
            c.decir("  Fecha invalida. Formato: AAAA-MM-DD.")
            continue
        if desde < cen.INICIO_FUENTE:
            c.decir(f"  Antes de {cen.INICIO_FUENTE} los datos vienen del Maestro, no de la web.")
            continue
        if desde > ayer:
            c.decir(f"  El ultimo dia publicable es {ayer}.")
            continue
        break

    dias = (ayer - desde).days + 1
    c.decir(f"\n  Se revisaran {dias:,} dia(s), con al menos 1 s de pausa por peticion.")
    if dias > 31:
        c.decir(
            f"  Es una descarga grande: del orden de {dias * 2 / 60:.0f} min y "
            f"{dias * 25 / 1024:.1f} GB. Se puede interrumpir con Ctrl+C: al retomar "
            "no se vuelve a bajar lo ya bajado."
        )
    if not c.confirmar("  ¿Continuar?"):
        c.decir("  Cancelado.")
        return

    c.decir("")
    try:
        nuevos, hallazgos = deriva.sincronizar_vigilando(
            desde, ayer, ctx.bronze_cen, ctx.nueva_sesion(), hoy=hoy, avisar=c.decir
        )
    except cen.ErrorDescarga as e:
        c.decir(f"\n  El sitio no respondio: {e}")
        c.decir("  Lo ya descargado quedo registrado. Vuelve a elegir esta opcion para retomar.")
        return
    if deriva.descarga_detenida(hallazgos):
        c.decir("\n  El sitio cambio respecto de la linea base: no se descargo nada.")
        _mostrar_hallazgos(ctx, hallazgos, "Chequeo previo desde el menu")
        return
    c.decir(f"\n  {len(nuevos)} archivo(s) nuevo(s). Pasando a la base...")

    con = ctx.abrir()
    try:
        reporte, hallazgos_ingesta = ingerir_cen.ingerir_pagina(
            con,
            ctx.bronze_cen,
            ctx.silver,
            conexion.ruta_staging(ctx.cfg.data_dir),
            avisar=c.decir,
        )
    finally:
        con.close()

    if not reporte:
        c.decir("  La base ya estaba al dia.")
    for fila in reporte:
        for motivo in fila["omitidos"]:
            c.decir(f"  [omitido] {motivo}")
    c.decir("")
    _mostrar_hallazgos(ctx, hallazgos + hallazgos_ingesta, "Actualizacion desde el menu")
    if ingerir_cen.hay_problemas(reporte):
        c.decir("  La ingesta encontro problemas: revisar arriba.")


# ------------------------------------------------------------------- 5. vigilar


def vigilar(ctx: Contexto) -> None:
    """Revisa si el Coordinador cambio algo, sin descargar ZIP."""
    c = ctx.consola
    hoy = ctx.hoy()
    c.decir("\n  Revisando la pagina del Coordinador (ultimos 7 dias)...")
    hallazgos = deriva.vigilar_fuente(
        ctx.nueva_sesion(), ctx.bronze_cen, hasta=hoy - timedelta(days=1), hoy=hoy
    )
    con = ctx.abrir()
    try:
        hallazgos += cobertura.revisar(_cobertura(ctx, con, cen.leer_manifiesto(ctx.bronze_cen)))
    finally:
        con.close()
    _mostrar_hallazgos(ctx, hallazgos, "Vigilancia desde el menu")


# ------------------------------------------------------------------- menu


@dataclass(frozen=True)
class Opcion:
    clave: str
    texto: str
    accion: Callable[[Contexto], None]
    #: Las consultas no tienen sentido con la base vacia.
    requiere_base: bool = True


OPCIONES: tuple[Opcion, ...] = (
    Opcion("1", "Descargar CMg quinceminutal (una o varias barras)", descargar),
    Opcion("2", "Ver promedios por bloque en pantalla", ver_bloques),
    Opcion("3", "Calcular riesgo nodal entre barras", riesgo),
    Opcion("4", "Actualizar la base desde la pagina del Coordinador", actualizar, False),
    Opcion("5", "Revisar si el Coordinador cambio algo", vigilar, False),
)


def ejecutar(ctx: Contexto) -> None:
    """El bucle principal. Sale con 0, Enter vacio en la entrada cerrada, o Ctrl+C."""
    c = ctx.consola
    estado = estado_base(ctx)
    while True:
        ctx.limpiar()
        c.decir("=" * ANCHO)
        c.decir("COSTOS MARGINALES POR BARRA - SEN".center(ANCHO))
        c.decir("=" * ANCHO)
        for linea in estado:
            c.decir(linea)
        c.decir("-" * ANCHO)
        for o in OPCIONES:
            c.decir(f"   [{o.clave}]  {o.texto}")
        c.decir("   [0]  Salir")
        c.decir("-" * ANCHO)

        eleccion = c.preguntar("  Opcion: ")
        if eleccion is None or eleccion == "0":
            return
        opcion = next((o for o in OPCIONES if o.clave == eleccion), None)
        if opcion is None:
            c.decir("  Opcion no valida.")
        elif opcion.requiere_base and not leer.base_existe(ctx.silver):
            c.decir("  La base esta vacia. Usa la opcion 4 para traer datos.")
        else:
            try:
                opcion.accion(ctx)
            except KeyboardInterrupt:
                c.decir("\n  Interrumpido. Lo ya descargado queda guardado.")
            except ValueError as e:
                c.decir(f"\n  Error: {e}")
            except Exception:
                c.decir("\n  Ocurrio un error inesperado:")
                c.decir(traceback.format_exc())
            if opcion.clave == "4":
                estado = estado_base(ctx)

        if c.preguntar("\n  Enter para volver al menu...") is None:
            return
