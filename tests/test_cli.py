"""Tests del CLI con `CliRunner`, que invoca los comandos sin lanzar un proceso.

Se verifica el CODIGO DE SALIDA ademas del texto: es lo que un Task Scheduler o
un CI leen para saber si la corrida sirvio.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from cmg_ingesta import cli
from cmg_ingesta.extract import coordinador_cmg as cen

Sembrar = Callable[..., None]
ArmarZipCen = Callable[..., Path]
runner = CliRunner()


@pytest.fixture(autouse=True)
def entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Aisla cada test: data_dir propio y sin variables CMGI_ del entorno real."""
    import os

    for clave in list(os.environ):
        if clave.startswith("CMGI_"):
            monkeypatch.delenv(clave, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CMGI_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def con_base(con: duckdb.DuckDBPyConnection, entorno: Path, sembrar: Sembrar) -> Path:
    """Una base Silver con un mes, en la ruta que el CLI va a leer."""
    silver = entorno / "data" / "silver" / "cmg"
    sembrar(con, silver, 2024, 6, "BARRA_1")
    sembrar(con, silver, 2024, 7, "BARRA_2")
    return silver


# ----------------------------------------------------------------- estado


def test_estado_sin_base_falla_y_explica(entorno: Path) -> None:
    r = runner.invoke(cli.app, ["estado"])
    assert r.exit_code == cli.SALIDA_ERROR
    assert "vacia" in r.stdout
    assert "cmg migrar-historico" in r.stdout


def test_estado_con_base(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["estado"])
    assert r.exit_code == cli.SALIDA_OK
    assert "2024-06" in r.stdout
    assert "2024-07" in r.stdout


def test_estado_muestra_los_dias_y_meses_que_faltan(
    con_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESION (2026-10-08): `estado` decia el periodo de punta a punta y callaba los huecos.

    `con_base` tiene un solo dia de junio y uno de julio de 2024.
    """
    monkeypatch.setattr(cli, "_hoy", lambda: date(2024, 8, 4))  # se espera hasta el 2024-08-01
    r = runner.invoke(cli.app, ["estado"])
    assert r.exit_code == cli.SALIDA_OK
    assert "(2 de 2 meses con datos)" in r.stdout
    assert "faltantes" in r.stdout and "del Maestro" in r.stdout
    assert "2024-06-02 a 2024-06-30" in r.stdout
    assert "meses sin ningun dato" in r.stdout  # 2021-01 .. 2024-05 y 2024-08


# ----------------------------------------------------------------- barras


def test_barras_lista_todas(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["barras"])
    assert r.exit_code == cli.SALIDA_OK
    assert "BARRA_1" in r.stdout
    assert "BARRA_2" in r.stdout


def test_barras_filtra(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["barras", "barra_1"])
    assert r.exit_code == cli.SALIDA_OK
    assert "BARRA_1" in r.stdout
    assert "BARRA_2" not in r.stdout


def test_barras_filtro_sin_resultados(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["barras", "zzz"])
    assert r.exit_code == cli.SALIDA_OK
    assert "0 de 2" in r.stdout


# ---------------------------------------------------------------- bloques


def test_bloques_muestra_la_tabla(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["bloques", "BARRA_1"])
    assert r.exit_code == cli.SALIDA_OK
    for columna in ("A", "B", "C", "Solar", "NoSolar", "Total"):
        assert columna in r.stdout


def test_bloques_barra_inexistente(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["bloques", "NO_EXISTE"])
    assert r.exit_code == cli.SALIDA_ERROR


def test_bloques_periodo_invalido(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["bloques", "BARRA_1", "--periodo", "ayer"])
    assert r.exit_code == cli.SALIDA_USO
    assert "no reconocido" in r.stderr


def test_bloques_periodo_fuera_de_rango(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["bloques", "BARRA_1", "--periodo", "2019"])
    assert r.exit_code == cli.SALIDA_ERROR


# -------------------------------------------------------------- descargar


def test_descargar_escribe_el_archivo(con_base: Path, entorno: Path) -> None:
    r = runner.invoke(cli.app, ["descargar", "BARRA_1", "--formato", "csv"])
    assert r.exit_code == cli.SALIDA_OK
    archivos = list((entorno / "data" / "descargas").glob("*.csv"))
    assert len(archivos) == 2  # la serie y los bloques


def test_descargar_formato_invalido(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["descargar", "BARRA_1", "--formato", "pdf"])
    assert r.exit_code == cli.SALIDA_USO
    assert "no soportado" in r.stderr


# ----------------------------------------------------------------- riesgo


def test_riesgo_entre_dos_barras(
    con: duckdb.DuckDBPyConnection, entorno: Path, sembrar: Sembrar
) -> None:
    """Las dos barras tienen que estar en el MISMO mes para que haya cruce."""
    silver = entorno / "data" / "silver" / "cmg"
    consulta = """
        SELECT b.barra, DATE '2024-06-01' AS fecha,
               CAST(h AS UTINYINT) AS hora, CAST(0 AS UTINYINT) AS minuto,
               'A' AS bloque, CAST(b.valor AS DOUBLE) AS cmg_usd_mwh,
               false AS es_hora_extra, CAST(NULL AS TIMESTAMP) AS fecha_hora,
               'maestro_cmg_db' AS origen, CAST('2026-10-06' AS TIMESTAMP) AS ingerido_en
        FROM (SELECT unnest(range(24)) AS h) a,
             (SELECT * FROM (VALUES ('REF', 50.0), ('COMP', 60.0)) t(barra, valor)) b
    """
    from cmg_ingesta.silver import escribir

    escribir.escribir_particion(con, consulta, silver, 2024, 6)

    r = runner.invoke(cli.app, ["riesgo", "REF", "COMP"])
    assert r.exit_code == cli.SALIDA_OK
    assert "riesgo = CMg(COMP) - CMg(REF)" in r.stdout
    assert "10.000" in r.stdout  # 60 - 50


def test_riesgo_sin_interseccion(con_base: Path) -> None:
    """BARRA_1 esta en junio y BARRA_2 en julio: no hay intervalos comunes."""
    r = runner.invoke(cli.app, ["riesgo", "BARRA_1", "BARRA_2"])
    assert r.exit_code == cli.SALIDA_ERROR
    assert "comparables" in r.stderr


# ------------------------------------------------------- migrar-historico


def test_migrar_origen_inexistente(entorno: Path) -> None:
    r = runner.invoke(cli.app, ["migrar-historico", str(entorno / "no_existe")])
    assert r.exit_code == cli.SALIDA_USO


def test_migrar_sin_meses_en_el_tramo(entorno: Path) -> None:
    """Una carpeta que existe pero sin meses de 2021-2024."""
    vacia = entorno / "vieja"
    (vacia / "anio=2025" / "mes=1").mkdir(parents=True)
    r = runner.invoke(cli.app, ["migrar-historico", str(vacia)])
    assert r.exit_code == cli.SALIDA_ERROR
    assert "no se encontro" in r.stderr


def test_migrar_con_hallazgos_sale_con_codigo_2(
    con: duckdb.DuckDBPyConnection, entorno: Path
) -> None:
    """Un mes con la hora fantasma de septiembre: termina, pero avisa con exit 2."""
    vieja = entorno / "vieja"
    carpeta = vieja / "anio=2023" / "mes=9"
    carpeta.mkdir(parents=True)
    salida = str(carpeta / "data.parquet").replace("'", "''")
    con.execute(f"""
        COPY (
            SELECT 'BARRA_X' AS barra, DATE '2023-09-03' AS fecha,
                   CAST(h AS UTINYINT) AS hora, CAST(m AS UTINYINT) AS minuto,
                   'A' AS bloque, CAST(0.0 AS FLOAT) AS cmg_usd_mwh,
                   CAST(NULL AS TIMESTAMP) AS fecha_hora
            FROM (SELECT unnest(range(24)) AS h) t,
                 (SELECT unnest([0, 15, 30, 45]) AS m) u
        ) TO '{salida}' (FORMAT PARQUET)
    """)

    r = runner.invoke(cli.app, ["migrar-historico", str(vieja)])
    assert r.exit_code == cli.SALIDA_CON_HALLAZGOS
    assert "validaciones encontraron problemas" in r.stdout


def test_ayuda_lista_los_comandos() -> None:
    r = runner.invoke(cli.app, ["--help"])
    assert r.exit_code == cli.SALIDA_OK
    for comando in ("estado", "barras", "descargar", "bloques", "riesgo", "vigilar-fuente"):
        assert comando in r.stdout


# ------------------------------------------------ descargar-cen / vigilar-fuente
#
# Sin red: `cli._sesion` se reemplaza por una sesion falsa que responde desde un
# diccionario, y `cli._hoy` por una fecha fija. `time.sleep` se anula para que la
# pausa obligatoria de 1 s no haga lentos los tests.

FIXTURES = Path(__file__).parent / "fixtures"
HOY = date(2026, 10, 6)
UP = "https://www.coordinador.cl/wp-content/uploads/2026/01/"


class Respuesta:
    def __init__(self, status_code: int, texto: str = "", contenido: bytes = b""):
        self.status_code = status_code
        self.text = texto
        self.content = contenido
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Sesion:
    def __init__(self, respuestas: dict[str, str | bytes]):
        self.respuestas = respuestas
        self.pedidos: list[str] = []

    def get(self, url: str, timeout: float = 30.0) -> Respuesta:
        self.pedidos.append(url)
        r = self.respuestas.get(url)
        if isinstance(r, bytes):
            return Respuesta(200, contenido=r)
        if isinstance(r, str):
            return Respuesta(200, texto=r)
        return Respuesta(404)


def pagina(*hrefs: str) -> str:
    """Una pagina de dia con la estructura real: un <div> por documento."""

    def etiqueta(h: str) -> str:
        tipo = "Preliminar" if "_pre" in h else "Definitivo"
        return f"Antecedentes Costo Marginal Real {tipo}"

    bloques_html = "".join(
        f"""<div><span class="informes-estudio-Titulo" title="{etiqueta(h)}">x</span>
        <span class="documentos-Publicar-Fecha">Fecha de publicaci&oacute;n:
        22/01/2026</span><a href="{h}">Descargar ZIP</a></div>"""
        for h in hrefs
    )
    return f"<html><body>{bloques_html}</body></html>"


def sitio_de_referencia() -> dict[str, str | bytes]:
    """Lo que pide el chequeo previo: el sitio REAL capturado (fixtures), sin cambios."""
    from cmg_ingesta.extract import sitemap_cen

    return {
        cen.INDICE: (FIXTURES / "indice_anios.html").read_text(encoding="utf-8"),
        cen.url_dia(date(2026, 1, 15)): (FIXTURES / "dia_2026-01-15.html").read_text(
            encoding="utf-8"
        ),
        sitemap_cen.SITEMAP: (FIXTURES / "sitemap_indice_2026-10-08.xml").read_text(
            encoding="utf-8"
        ),
    }


@pytest.fixture
def sin_red(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, str | bytes]], Sesion]:
    """Devuelve una funcion que instala la sesion falsa en el CLI."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setattr(cli, "_hoy", lambda: HOY)

    def _instalar(respuestas: dict[str, str | bytes]) -> Sesion:
        sesion = Sesion({**sitio_de_referencia(), **respuestas})
        monkeypatch.setattr(cli, "_sesion", lambda: sesion)
        return sesion

    return _instalar


def test_descargar_cen_baja_al_bronze_y_sale_ok(
    entorno: Path, sin_red: Callable[..., Sesion], zip_cen: ArmarZipCen
) -> None:
    dia = date(2026, 1, 14)
    nombre = "Antecedentes_CMG_Real_def_260114.zip"
    contenido = zip_cen(entorno / "tmp.zip", dia).read_bytes()
    sin_red({cen.url_dia(dia): pagina(UP + nombre), UP + nombre: contenido})

    r = runner.invoke(cli.app, ["descargar-cen", "--desde", "2026-01-14", "--hasta", "2026-01-14"])

    assert r.exit_code == cli.SALIDA_OK, r.output
    assert (entorno / "data" / "bronze" / "cen_cmg" / nombre).exists()
    assert "1 archivo(s) nuevo(s)" in r.stdout
    assert not (entorno / "data" / "alertas").exists()  # sin hallazgos no hay reporte


def test_descargar_cen_dos_veces_no_vuelve_a_bajar(
    entorno: Path, sin_red: Callable[..., Sesion], zip_cen: ArmarZipCen
) -> None:
    dia = date(2026, 1, 14)
    nombre = "Antecedentes_CMG_Real_def_260114.zip"
    contenido = zip_cen(entorno / "tmp.zip", dia).read_bytes()
    sesion = sin_red({cen.url_dia(dia): pagina(UP + nombre), UP + nombre: contenido})
    args = ["descargar-cen", "--desde", "2026-01-14", "--hasta", "2026-01-14"]

    runner.invoke(cli.app, args)
    r = runner.invoke(cli.app, args)

    assert "0 archivo(s) nuevo(s)" in r.stdout
    assert sesion.pedidos.count(UP + nombre) == 1


def test_descargar_cen_un_dia_sin_publicar_sale_con_codigo_2(
    entorno: Path, sin_red: Callable[..., Sesion]
) -> None:
    """La revision manual se pide con exit 2 y un reporte en data/alertas."""
    dia = date(2026, 1, 14)
    sin_red({cen.url_dia(dia): pagina()})

    r = runner.invoke(cli.app, ["descargar-cen", "--desde", "2026-01-14", "--hasta", "2026-01-14"])

    assert r.exit_code == cli.SALIDA_CON_HALLAZGOS
    assert "dia_sin_registro" in r.stdout
    reportes = list((entorno / "data" / "alertas").glob("deriva_*.md"))
    assert len(reportes) == 1


def test_descargar_cen_hasta_por_omision_es_ayer(
    entorno: Path, sin_red: Callable[..., Sesion]
) -> None:
    sin_red({})
    r = runner.invoke(cli.app, ["descargar-cen", "--desde", "2026-10-05"])
    assert "2026-10-05 a 2026-10-05" in r.stdout


def test_descargar_cen_fecha_invalida(sin_red: Callable[..., Sesion]) -> None:
    sin_red({})
    r = runner.invoke(cli.app, ["descargar-cen", "--desde", "15-01-2026"])
    assert r.exit_code == cli.SALIDA_USO
    assert "AAAA-MM-DD" in r.stderr


def test_descargar_cen_rango_al_reves(sin_red: Callable[..., Sesion]) -> None:
    sin_red({})
    r = runner.invoke(cli.app, ["descargar-cen", "--desde", "2026-02-01", "--hasta", "2026-01-01"])
    assert r.exit_code == cli.SALIDA_USO
    assert "posterior" in r.stderr


def test_descargar_cen_pausa_menor_a_un_segundo_se_rechaza(
    sin_red: Callable[..., Sesion],
) -> None:
    sin_red({})
    r = runner.invoke(
        cli.app,
        ["descargar-cen", "--desde", "2026-10-01", "--hasta", "2026-10-01", "--pausa", "0.1"],
    )
    assert r.exit_code == cli.SALIDA_USO
    assert "pausa" in r.stderr


def test_descargar_e_ingerir_deja_el_mes_consultable(
    entorno: Path, sin_red: Callable[..., Sesion], zip_cen: ArmarZipCen
) -> None:
    """El flujo completo: pagina -> Bronze -> Silver -> `cmg estado`."""
    dia = date(2026, 1, 14)
    nombre = "Antecedentes_CMG_Real_def_260114.zip"
    contenido = zip_cen(entorno / "tmp.zip", dia).read_bytes()
    sin_red({cen.url_dia(dia): pagina(UP + nombre), UP + nombre: contenido})
    runner.invoke(cli.app, ["descargar-cen", "--desde", "2026-01-14", "--hasta", "2026-01-14"])

    r = runner.invoke(cli.app, ["ingerir-pagina"])
    assert r.exit_code == cli.SALIDA_OK, r.output
    assert "2026-01:" in r.stdout

    r = runner.invoke(cli.app, ["estado"])
    assert "2026-01" in r.stdout

    r = runner.invoke(cli.app, ["ingerir-pagina"])
    assert "sin cambios" in r.stdout


def test_ingerir_pagina_sin_bronze_explica(entorno: Path) -> None:
    r = runner.invoke(cli.app, ["ingerir-pagina"])
    assert r.exit_code == cli.SALIDA_ERROR
    assert "descargar-cen" in r.stderr


def test_vigilar_fuente_sin_publicaciones_sale_con_codigo_2(
    entorno: Path, sin_red: Callable[..., Sesion]
) -> None:
    indice = (FIXTURES / "indice_anios.html").read_text(encoding="utf-8")
    sesion = sin_red({cen.INDICE: indice})

    r = runner.invoke(cli.app, ["vigilar-fuente", "--dias", "2"])

    assert r.exit_code == cli.SALIDA_CON_HALLAZGOS
    assert "sin_publicaciones_recientes" in r.stdout
    assert not any(p.endswith(".zip") for p in sesion.pedidos)


def test_vigilar_fuente_dias_invalido(sin_red: Callable[..., Sesion]) -> None:
    sin_red({})
    r = runner.invoke(cli.app, ["vigilar-fuente", "--dias", "0"])
    assert r.exit_code == cli.SALIDA_USO


# ----------------------------------------------------------------- menu


@pytest.fixture
def sin_limpiar(monkeypatch: pytest.MonkeyPatch) -> None:
    from cmg_ingesta.menu import app as menu_app

    monkeypatch.setattr(menu_app, "limpiar_pantalla", lambda: None)


@pytest.mark.usefixtures("sin_limpiar")
def test_sin_comando_abre_el_menu(con_base: Path) -> None:
    """Doble clic en el programa = menu, no la ayuda de los comandos."""
    r = runner.invoke(cli.app, [], input="0\n")
    assert r.exit_code == cli.SALIDA_OK
    assert "COSTOS MARGINALES POR BARRA" in r.stdout
    assert "[1]  Descargar CMg quinceminutal" in r.stdout


@pytest.mark.usefixtures("sin_limpiar")
def test_menu_descarga_de_punta_a_punta(con_base: Path, entorno: Path) -> None:
    respuestas = ["1", "barra", "todas", "", "", "", "s", "", "0"]
    r = runner.invoke(cli.app, ["menu"], input="\n".join(respuestas) + "\n")
    assert r.exit_code == cli.SALIDA_OK
    assert sorted(p.name for p in (entorno / "data" / "descargas").glob("*.xlsx")) == [
        "CMg_BARRA_1_2024-06_a_2024-07.xlsx",
        "CMg_BARRA_2_2024-06_a_2024-07.xlsx",
    ]


@pytest.mark.usefixtures("sin_limpiar")
def test_los_comandos_siguen_funcionando_sin_menu(con_base: Path) -> None:
    r = runner.invoke(cli.app, ["estado"])
    assert r.exit_code == cli.SALIDA_OK
    assert "COSTOS MARGINALES" not in r.stdout


def test_descargar_cen_con_el_sitio_caido_explica_como_retomar(
    entorno: Path, sin_red: Callable[..., Sesion], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un 503 persistente: exit 1, mensaje claro y sin traceback."""

    class Caido:
        def get(self, url: str, timeout: float = 30.0) -> Respuesta:
            return Respuesta(503)

    sin_red({})
    monkeypatch.setattr(cli, "_sesion", lambda: Caido())
    r = runner.invoke(cli.app, ["descargar-cen", "--desde", "2026-01-14", "--hasta", "2026-01-14"])
    assert r.exit_code == cli.SALIDA_ERROR
    assert "no respondio" in r.stderr
    assert "retomar" in r.stderr
    assert r.exception is None or isinstance(r.exception, SystemExit)


# ------------------------------------------------------- codigos de salida


def test_los_cuatro_codigos_son_distintos() -> None:
    """REGRESION A15: los hallazgos salian con 2, el mismo codigo de los errores de click."""
    codigos = [cli.SALIDA_OK, cli.SALIDA_ERROR, cli.SALIDA_USO, cli.SALIDA_CON_HALLAZGOS]
    assert codigos == [0, 1, 2, 3]


def test_falta_una_opcion_obligatoria_es_error_de_uso(entorno: Path) -> None:
    """Lo detecta click antes de entrar a la funcion, y sale con 2."""
    r = runner.invoke(cli.app, ["descargar-cen"])
    assert r.exit_code == cli.SALIDA_USO
    assert r.exit_code != cli.SALIDA_CON_HALLAZGOS


def test_una_opcion_con_tipo_invalido_es_error_de_uso(entorno: Path) -> None:
    r = runner.invoke(cli.app, ["descargar-cen", "--desde", "2026-10-01", "--pausa", "abc"])
    assert r.exit_code == cli.SALIDA_USO


def test_un_comando_inexistente_es_error_de_uso(entorno: Path) -> None:
    assert runner.invoke(cli.app, ["comando-que-no-existe"]).exit_code == cli.SALIDA_USO


def test_periodo_bien_escrito_pero_sin_datos_no_es_error_de_uso(con_base: Path) -> None:
    """'2019' se entiende; lo que pasa es que la base no tiene 2019: eso es 1, no 2."""
    r = runner.invoke(cli.app, ["bloques", "BARRA_1", "--periodo", "2019"])
    assert r.exit_code == cli.SALIDA_ERROR
