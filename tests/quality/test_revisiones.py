"""Integracion de las revisiones por sitemap con la descarga y la vigilancia.

Escenario REAL simplificado: el Bronze tiene el 2026-08-31 (definitivo v1) bajado
el 2026-10-06. El 2026-10-07 el Coordinador publica el definitivo v2 de ese dia.
Una descarga de "ayer" tiene que traerlo sola, aunque el 31 de agosto este fuera
del rango pedido.
"""

import io
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import sitemap_cen as sm
from cmg_ingesta.quality import deriva

UP = "https://www.coordinador.cl/wp-content/uploads/2026/10/"
DIA_REVISADO = date(2026, 8, 31)
AYER = date(2026, 10, 7)
SUB = "https://www.coordinador.cl/documentos-sitemap826.xml"


def zip_valido(texto: str = "x") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("contenido.txt", texto)
    return buf.getvalue()


class Respuesta:
    def __init__(self, status_code: int, texto: str = "", contenido: bytes = b""):
        self.status_code = status_code
        self.text = texto
        self.content = contenido or texto.encode()
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None: ...


class Sesion:
    def __init__(self, respuestas: dict[str, Respuesta]):
        self.respuestas = respuestas
        self.pedidos: list[str] = []

    def get(self, url: str, timeout: float = 30.0) -> Respuesta:
        self.pedidos.append(url)
        return self.respuestas.get(url, Respuesta(404))


def bloque(nombre: str, etiqueta: str) -> str:
    return (
        f'<div><span class="informes-estudio-Titulo" title="{etiqueta}">x</span> '
        f'Fecha de publicaci&oacute;n: 07/10/2026 <a href="{UP}{nombre}">ZIP</a></div>'
    )


def sitemaps(lastmod: str) -> dict[str, Respuesta]:
    indice = f"<sitemap><loc>{SUB}</loc><lastmod>{lastmod}</lastmod></sitemap>"
    doc = (
        "<url><loc>https://www.coordinador.cl/documentos/"
        "antecedentes-costo-marginal-real-definitivo-v2-260831/</loc>"
        f"<lastmod>{lastmod}</lastmod></url>"
    )
    return {sm.SITEMAP: Respuesta(200, indice), SUB: Respuesta(200, doc)}


def bronze_con_v1(carpeta: Path) -> None:
    """El 31 de agosto ya bajado (v1) el 2026-10-06."""
    nombre = "Antecedentes_CMG_Real_def_260831.zip"
    (carpeta / nombre).write_bytes(zip_valido())
    entrada: cen.EntradaManifiesto = {
        "url": UP + nombre, "nombre": nombre, "tipo": "def", "version": 1, "reemision": 0,
        "fecha_operacion": "2026-08-31", "fecha_publicacion": "2026-09-09", "sha256": "",
        "bytes": 1, "descargado_en": "2026-10-06T10:00:00",
    }  # fmt: skip
    cen.guardar_manifiesto(carpeta, {nombre: entrada})


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)


def test_la_descarga_de_ayer_trae_sola_la_revision_de_agosto(tmp_path: Path) -> None:
    bronze_con_v1(tmp_path)
    v2 = "Antecedentes_CMG_Real_def_260831_v2.zip"
    sesion = Sesion(
        {
            **sitemaps("2026-10-07T18:53:50+00:00"),
            cen.url_dia(AYER): Respuesta(200, "<html></html>"),
            cen.url_dia(DIA_REVISADO): Respuesta(
                200,
                bloque(
                    "Antecedentes_CMG_Real_def_260831.zip",
                    "Antecedentes Costo Marginal Real Definitivo",
                )
                + bloque(v2, "Antecedentes Costo Marginal Real Definitivo v2"),
            ),
            UP + v2: Respuesta(200, contenido=zip_valido("v2")),
        }
    )
    nuevos, h = deriva.sincronizar_vigilando(
        AYER, AYER, tmp_path, sesion, pausa=1.0, hoy=AYER, chequear=False
    )

    assert [n["nombre"] for n in nuevos] == [v2]
    assert nuevos[0]["version"] == 2
    assert "revisiones_detectadas" in {x["tipo"] for x in h}
    # el punto de partida avanzo: la proxima corrida no vuelve a pedir este sub-sitemap
    estado = sm.leer_estado(tmp_path)
    assert estado is not None and estado > datetime(2026, 10, 7, 18, 53, 50, tzinfo=UTC)


def test_sin_nada_descargado_solo_fija_la_linea_base(tmp_path: Path) -> None:
    """La primera descarga ya trae lo vigente: no hay revisiones que buscar."""
    sesion = Sesion({cen.url_dia(AYER): Respuesta(200, "<html></html>")})
    deriva.sincronizar_vigilando(AYER, AYER, tmp_path, sesion, pausa=1.0, hoy=AYER, chequear=False)
    assert sm.SITEMAP not in sesion.pedidos
    assert sm.leer_estado(tmp_path) is not None


def test_sitemap_caido_avisa_y_no_avanza_el_punto(tmp_path: Path) -> None:
    bronze_con_v1(tmp_path)
    sesion = Sesion({cen.url_dia(AYER): Respuesta(200, "<html></html>")})
    _, h = deriva.sincronizar_vigilando(
        AYER, AYER, tmp_path, sesion, pausa=1.0, hoy=AYER, chequear=False
    )
    assert "sitemap_no_disponible" in {x["tipo"] for x in h}
    assert sm.leer_estado(tmp_path) is None  # se reintenta la proxima vez


def test_vigilar_fuente_reporta_revisiones_pendientes(tmp_path: Path) -> None:
    bronze_con_v1(tmp_path)
    indice = (Path(__file__).parent.parent / "fixtures" / "indice_anios.html").read_text(
        encoding="utf-8"
    )
    sesion = Sesion({**sitemaps("2026-10-07T18:53:50+00:00"), cen.INDICE: Respuesta(200, indice)})
    h = deriva.vigilar_fuente(sesion, tmp_path, hasta=AYER, dias=1, pausa=1.0, hoy=AYER)
    pendientes = [x for x in h if x["tipo"] == "revisiones_pendientes"]
    assert len(pendientes) == 1
    assert pendientes[0]["evidencia"] == "2026-08-31"
    assert not any(p.endswith(".zip") for p in sesion.pedidos)  # vigilar no descarga


def test_el_umbral_del_definitivo_es_25_dias() -> None:
    """Medido: el definitivo original tarda hasta 23 dias (771 casos reales)."""
    assert deriva.DIAS_MAX_PRE == 25
