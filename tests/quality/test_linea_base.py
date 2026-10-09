"""Tests del chequeo previo contra la linea base (config/linea_base_cen.toml).

Los HTML y el sitemap son REALES (fixtures): contra ellos el chequeo no debe
encontrar nada. Despues se les aplica un cambio puntual para ver que lo detecte.
"""

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import no_reconocidos, sitemap_cen
from cmg_ingesta.quality import deriva, linea_base

FIXTURES = Path(__file__).parent.parent / "fixtures"
INDICE = (FIXTURES / "indice_anios.html").read_text(encoding="utf-8")
REFERENCIA = (FIXTURES / "dia_2026-01-15.html").read_text(encoding="utf-8")
SITEMAP = (FIXTURES / "sitemap_indice_2026-10-08.xml").read_text(encoding="utf-8")
DIA_REF = date(2026, 1, 15)


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


def sitio(referencia: str = REFERENCIA, extra: dict[str, Respuesta] | None = None) -> Sesion:
    return Sesion(
        {
            cen.INDICE: Respuesta(200, INDICE),
            cen.url_dia(DIA_REF): Respuesta(200, referencia),
            sitemap_cen.SITEMAP: Respuesta(200, SITEMAP),
            **(extra or {}),
        }
    )


def tipos(h: list[deriva.Hallazgo]) -> set[str]:
    return {x["tipo"] for x in h}


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)


# ------------------------------------------------------------ contra lo real


def test_la_linea_base_del_proyecto_se_carga() -> None:
    base = linea_base.cargar()
    assert base["dia_referencia"] == DIA_REF
    assert len(base["documentos"]) == 2


def test_el_sitio_real_no_tiene_diferencias() -> None:
    sesion = sitio()
    assert linea_base.chequeo_previo(sesion) == []
    assert len(sesion.pedidos) == 3  # indice, dia de referencia, sitemap


# ------------------------------------------------- cambios estructurales


def test_falta_una_marca_del_html_es_critico() -> None:
    h = linea_base.chequeo_previo(sitio(REFERENCIA.replace("informes-estudio-Titulo", "titulo")))
    assert "base_marcador_ausente" in tipos(h)
    assert linea_base.detiene(h)


def test_un_documento_conocido_que_desaparece_es_critico() -> None:
    sin_def = REFERENCIA.replace("Antecedentes_CMG_Real_def_260115.zip", "otro.pdf")
    h = linea_base.chequeo_previo(sitio(sin_def))
    assert "base_documento_ausente" in tipos(h)
    assert linea_base.detiene(h)


def test_el_dia_de_referencia_inaccesible_es_critico() -> None:
    sesion = sitio()
    del sesion.respuestas[cen.url_dia(DIA_REF)]
    h = linea_base.chequeo_previo(sesion)
    assert "base_dia_referencia_inaccesible" in tipos(h)


def test_el_indice_sin_un_anio_es_critico() -> None:
    sin_2024 = INDICE.replace("2024-costo-marginal-real", "2023-costo-marginal-real")
    sesion = sitio()
    sesion.respuestas[cen.INDICE] = Respuesta(200, sin_2024)
    assert "base_indice_sin_anio" in tipos(linea_base.chequeo_previo(sesion))


# ------------------------------------------------ cambios no estructurales


def test_una_revision_nueva_en_la_referencia_es_solo_informativa() -> None:
    """Los documentos se revisan con el tiempo: un v2 nuevo no detiene nada."""
    v2 = (
        '<div style="border-top: 1px solid #E3E3E3;"><span class="informes-estudio-Titulo" '
        'title="Antecedentes Costo Marginal Real Definitivo v2">x</span>'
        '<span class="documentos-Publicar-Fecha">Fecha de publicaci&oacute;n: 05/03/2026</span>'
        '<a href="https://www.coordinador.cl/wp-content/uploads/2026/03/'
        'Antecedentes_CMG_Real_def_260115_v2.zip">Descargar ZIP</a></div>'
    )
    # el fixture real no trae </body>: el bloque nuevo va al final
    h = linea_base.chequeo_previo(sitio(REFERENCIA + v2))
    assert [(x["severidad"], x["tipo"]) for x in h] == [("info", "base_revision_en_referencia")]
    assert not linea_base.detiene(h)


def test_una_fecha_de_publicacion_cambiada_es_aviso() -> None:
    h = linea_base.chequeo_previo(sitio(REFERENCIA.replace("22/01/2026", "23/01/2026")))
    assert [x["severidad"] for x in h if x["tipo"] == "base_fecha_publicacion_distinta"] == [
        "aviso"
    ]
    assert not linea_base.detiene(h)


def test_sitemap_caido_es_aviso_y_no_detiene() -> None:
    sesion = sitio()
    del sesion.respuestas[sitemap_cen.SITEMAP]
    h = linea_base.chequeo_previo(sesion)
    assert tipos(h) == {"base_sitemap_inaccesible"}
    assert not linea_base.detiene(h)


# ----------------------------------------------- integracion con la descarga


def test_si_el_sitio_cambio_no_se_descarga_nada(tmp_path: Path) -> None:
    dia = date(2026, 1, 14)
    zip_url = (
        "https://www.coordinador.cl/wp-content/uploads/2026/01/Antecedentes_CMG_Real_def_260114.zip"
    )
    sesion = sitio(REFERENCIA.replace("documentos-Publicar-Fecha", "fecha-nueva"))
    sesion.respuestas[cen.url_dia(dia)] = Respuesta(200, f'<a href="{zip_url}">ZIP</a>')
    nuevos, h = deriva.sincronizar_vigilando(dia, dia, tmp_path, sesion, pausa=1.0)
    assert nuevos == []
    assert "descarga_detenida" in tipos(h)
    assert deriva.descarga_detenida(h)
    assert cen.url_dia(dia) not in sesion.pedidos  # ni siquiera se pidio el dia


def test_la_descarga_registra_los_enlaces_no_reconocidos(tmp_path: Path) -> None:
    dia = date(2026, 1, 14)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.txt", "x")
    raro = "https://www.coordinador.cl/wp-content/uploads/2026/01/Antecedentes_CMG_Real_def_260114_final.zip"
    pagina = (
        '<div><span class="informes-estudio-Titulo" title="Antecedentes Costo Marginal Real '
        'Definitivo">x</span> Fecha de publicaci&oacute;n: 21/01/2026 '
        f'<a href="{raro}">ZIP</a></div>'
    )
    sesion = sitio(
        extra={
            cen.url_dia(dia): Respuesta(200, pagina),
            raro: Respuesta(200, contenido=buf.getvalue()),
        }
    )
    _, h = deriva.sincronizar_vigilando(dia, dia, tmp_path, sesion, pausa=1.0)

    assert "nombres_no_reconocidos_nuevos" in tipos(h)
    registro = no_reconocidos.leer(tmp_path)
    assert list(registro) == [raro]
    assert registro[raro]["motivo"] == "forma_desconocida"
    assert registro[raro]["etiqueta"] == "Antecedentes Costo Marginal Real Definitivo"
