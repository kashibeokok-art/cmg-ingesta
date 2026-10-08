"""Tests de la deteccion de revisiones por sitemap. Ninguno toca la red.

Los dos XML son REALES, capturados el 2026-10-08: el indice (826 sub-sitemaps de
documentos) y el sub-sitemap mas reciente, con 28 revisiones de CMg publicadas
el 2026-10-07.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.extract import sitemap_cen as sm

FIXTURES = Path(__file__).parent.parent / "fixtures"
INDICE = (FIXTURES / "sitemap_indice_2026-10-08.xml").read_text(encoding="utf-8")
SUB826 = (FIXTURES / "sitemap_documentos826_2026-10-08.xml").read_text(encoding="utf-8")
URL826 = "https://www.coordinador.cl/documentos-sitemap826.xml"


def utc(anio: int, mes: int, dia: int, hora: int = 0, minuto: int = 0) -> datetime:
    return datetime(anio, mes, dia, hora, minuto, tzinfo=UTC)


class Respuesta:
    def __init__(self, status_code: int, texto: str = ""):
        self.status_code = status_code
        self.text = texto
        self.content = texto.encode()
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None: ...


class Sesion:
    def __init__(self, respuestas: dict[str, str]):
        self.respuestas = respuestas
        self.pedidos: list[str] = []

    def get(self, url: str, timeout: float = 30.0) -> Respuesta:
        self.pedidos.append(url)
        texto = self.respuestas.get(url)
        return Respuesta(200, texto) if texto is not None else Respuesta(404)


# ------------------------------------------------------------------ parseo


def test_el_indice_real_tiene_826_sub_sitemaps_de_documentos() -> None:
    urls = [u for u, _ in sm.parsear_entradas(INDICE) if "documentos-sitemap" in u]
    assert len(urls) == 826


def test_solo_los_sub_sitemaps_modificados_despues_del_punto() -> None:
    subs = sm.subsitemaps_modificados(INDICE, utc(2026, 10, 6))
    assert subs[0] == URL826  # el mas nuevo primero
    assert len(subs) == 4


def test_nada_modificado_despues_del_ultimo_lastmod() -> None:
    assert sm.subsitemaps_modificados(INDICE, utc(2026, 10, 9)) == []


def test_las_revisiones_reales_del_2026_10_07() -> None:
    revisiones = sm.revisiones_en(SUB826, utc(2026, 10, 6))
    assert len(revisiones) == 28
    assert all(r["dia"] is not None for r in revisiones)
    agosto = {r["dia"] for r in revisiones if r["dia"] and r["dia"].startswith("2026-08")}
    assert len(agosto) == 21  # 20 definitivos v2 + 1 v3 (2026-08-09)
    assert {"2026-08-31", "2026-08-01"} <= agosto


def test_el_punto_de_partida_filtra_por_lastmod() -> None:
    assert sm.revisiones_en(SUB826, utc(2026, 10, 8)) == []


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        (
            "https://www.coordinador.cl/documentos/antecedentes-costo-marginal-real-definitivo-v2-260831/",
            date(2026, 8, 31),
        ),
        (
            "https://www.coordinador.cl/documentos/antecedentes-costo-marginal-real-preliminar-250101",
            date(2025, 1, 1),
        ),
        ("https://www.coordinador.cl/documentos/antecedentes-costo-marginal-real-sin-fecha/", None),
        ("https://www.coordinador.cl/documentos/otro-documento-260831/", None),
    ],
)
def test_dia_del_slug(url: str, esperado: date | None) -> None:
    assert sm.dia_del_slug(url) == esperado


def test_entradas_sin_cdata_tambien_se_leen() -> None:
    xml = "<url><loc>https://x/documentos-sitemap1.xml</loc><lastmod>2026-01-01T00:00:00+00:00</lastmod></url>"
    assert sm.parsear_entradas(xml) == [("https://x/documentos-sitemap1.xml", utc(2026, 1, 1))]


# ------------------------------------------------------------------ estado


def test_estado_ida_y_vuelta(tmp_path: Path) -> None:
    assert sm.leer_estado(tmp_path) is None
    sm.guardar_estado(tmp_path, utc(2026, 10, 8, 12, 30))
    assert sm.leer_estado(tmp_path) == utc(2026, 10, 8, 12, 30)


def test_estado_corrupto_es_none(tmp_path: Path) -> None:
    (tmp_path / sm.ESTADO).write_text("{roto", encoding="utf-8")
    assert sm.leer_estado(tmp_path) is None


def test_punto_de_partida_sin_nada_es_none(tmp_path: Path) -> None:
    assert sm.punto_de_partida(tmp_path, {}) is None


def test_punto_de_partida_es_la_primera_descarga(tmp_path: Path) -> None:
    def e(cuando: str) -> cen.EntradaManifiesto:
        return {
            "url": "", "nombre": cuando, "tipo": "def", "version": 1, "reemision": 0,
            "fecha_operacion": "2026-10-01", "fecha_publicacion": None, "sha256": "",
            "bytes": 0, "descargado_en": cuando,
        }  # fmt: skip

    m = {"a": e("2026-10-07T10:00:00"), "b": e("2026-10-06T09:00:00")}
    punto = sm.punto_de_partida(tmp_path, m)
    assert punto is not None
    esperado = datetime.fromisoformat("2026-10-06T09:00:00").astimezone(UTC)
    assert punto == esperado  # hora local -> UTC


def test_el_estado_guardado_manda_sobre_el_manifiesto(tmp_path: Path) -> None:
    sm.guardar_estado(tmp_path, utc(2026, 10, 1))
    assert sm.punto_de_partida(tmp_path, {}) == utc(2026, 10, 1)


# --------------------------------------------------------------------- red


def test_buscar_revisiones_pide_solo_lo_modificado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    sesion = Sesion({sm.SITEMAP: INDICE, URL826: SUB826})
    # desde el 2026-10-08 a las 13:00 solo cambio el 826 (lastmod 2026-10-08T14:01)
    busqueda = sm.buscar_revisiones(sesion, utc(2026, 10, 8, 13))
    assert sesion.pedidos == [sm.SITEMAP, URL826]
    assert busqueda["subsitemaps_consultados"] == 1
    assert busqueda["truncada"] is False


def test_buscar_revisiones_avisa_si_hay_demasiados(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    sesion = Sesion({sm.SITEMAP: INDICE, URL826: SUB826})
    busqueda = sm.buscar_revisiones(sesion, utc(2026, 10, 6), max_subsitemaps=1)
    assert busqueda["truncada"] is True
    assert busqueda["subsitemaps_consultados"] == 1


def test_sitemap_caido_es_error_descarga() -> None:
    with pytest.raises(cen.ErrorDescarga, match="404"):
        sm.buscar_revisiones(Sesion({}), utc(2026, 10, 6))
