"""Tests del extractor del sitio del Coordinador. Ninguno toca la red.

Los fixtures HTML son SINTETICOS (ver tests/fixtures/LEEME.md): desde este
entorno el sitio devuelve 403 con `cf-mitigated: challenge`. Verifican que el
parser cumpla las reglas documentadas, pero NO son regresion contra el sitio.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from cmg_ingesta.extract import coordinador_cmg as cen

FIXTURES = Path(__file__).parent.parent / "fixtures"


def html(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


# ============================================================ 2a) URLs


def test_url_dia_termina_en_el_slug_del_dia() -> None:
    url = cen.url_dia(date(2026, 9, 30))
    assert url.endswith("/30-septiembre-2026-costo-marginal-real/")


def test_url_dia_usa_la_raiz_sin_transferencias_economicas() -> None:
    """Las dos rutas resuelven; se usa la corta porque es la que el sitio enlaza."""
    assert "/mercados/documentos/costo-marginal-real/" in cen.url_dia(date(2026, 1, 15))


def test_url_2024_usa_el_sufijo_largo() -> None:
    """El slug de 2024 arrastra '-transferencias-economicas'. Verificado: 200 vs 404."""
    url = cen.url_dia(date(2024, 8, 15))
    assert url.endswith("/15-agosto-2024-costo-marginal-real-transferencias-economicas/")
    assert "2024-costo-marginal-real-transferencias-economicas" in url


def test_url_2025_y_2026_usan_el_sufijo_corto() -> None:
    assert cen.url_dia(date(2025, 8, 15)).endswith("/15-agosto-2025-costo-marginal-real/")
    assert cen.url_dia(date(2026, 8, 15)).endswith("/15-agosto-2026-costo-marginal-real/")


def test_año_sin_slug_conocido_revienta() -> None:
    """Fail fast: una URL mal construida da 404 y pareceria 'dia sin publicacion'."""
    with pytest.raises(ValueError, match="Slug desconocido"):
        cen.url_dia(date(2023, 1, 1))


def test_el_mensaje_de_error_dice_que_años_conoce() -> None:
    with pytest.raises(ValueError, match="2024"):
        cen.url_dia(date(2027, 1, 1))


def test_dia_de_un_digito_va_con_cero_adelante() -> None:
    assert cen.url_dia(date(2026, 1, 1)).endswith("/01-enero-2026-costo-marginal-real/")
    assert cen.url_dia(date(2026, 1, 9)).endswith("/09-enero-2026-costo-marginal-real/")


MESES_ESPERADOS = [
    (1, "enero"),
    (2, "febrero"),
    (3, "marzo"),
    (4, "abril"),
    (5, "mayo"),
    (6, "junio"),
    (7, "julio"),
    (8, "agosto"),
    (9, "septiembre"),
    (10, "octubre"),
    (11, "noviembre"),
    (12, "diciembre"),
]


@pytest.mark.parametrize(("mes", "nombre"), MESES_ESPERADOS)
def test_los_doce_meses_en_minuscula_y_sin_tilde(mes: int, nombre: str) -> None:
    assert cen.MESES[mes] == nombre
    assert nombre == nombre.lower()
    assert all(c not in nombre for c in "áéíóúÁÉÍÓÚ")
    assert f"{nombre}-2026" in cen.url_dia(date(2026, mes, 15))


def test_url_anio_y_url_mes() -> None:
    assert cen.url_anio(2026).endswith("/2026-costo-marginal-real/")
    assert cen.url_mes(2026, 10).endswith("/octubre-2026-costo-marginal-real/")


def test_dias_entre_es_inclusivo() -> None:
    dias = list(cen.dias_entre(date(2026, 9, 28), date(2026, 9, 30)))
    assert dias == [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]


def test_dias_entre_rango_invertido_revienta() -> None:
    with pytest.raises(ValueError, match="invertido"):
        list(cen.dias_entre(date(2026, 9, 30), date(2026, 9, 28)))


# ========================================================== 2b) parseo


def test_dia_con_dos_documentos() -> None:
    docs = cen.parsear_documentos(html("dia_2026-01-15.html"), date(2026, 1, 15))
    assert len(docs) == 2
    assert {d["nombre"] for d in docs} == {
        "Antecedentes_CMG_Real_def_260115.zip",
        "Antecedentes_CMG_Real_pre_260115.zip",
    }


def test_las_fechas_de_publicacion_no_se_cruzan() -> None:
    """Cada documento esta en su propio div HERMANO: find_parent no los mezcla."""
    docs = cen.parsear_documentos(html("dia_2026-01-15.html"), date(2026, 1, 15))
    por_tipo = {d["tipo"]: d for d in docs}
    assert por_tipo["def"]["fecha_publicacion"] == "2026-01-22"
    assert por_tipo["pre"]["fecha_publicacion"] == "2026-01-16"


def test_el_span_vacio_no_gana_sobre_el_que_tiene_contenido() -> None:
    """El primer span.documentos-Publicar-Fecha del bloque viene VACIO.

    Un find(class_=...) devolveria "". La fecha se lee por CONTENIDO con regex.
    """
    docs = cen.parsear_documentos(html("dia_2026-01-15.html"), date(2026, 1, 15))
    assert all(d["fecha_publicacion"] is not None for d in docs)
    assert all(d["fecha_publicacion"] != "" for d in docs)


def test_la_etiqueta_sale_del_title_del_span() -> None:
    docs = cen.parsear_documentos(html("dia_2026-01-15.html"), date(2026, 1, 15))
    por_tipo = {d["tipo"]: d for d in docs}
    assert por_tipo["def"]["etiqueta"] == "Antecedentes Costo Marginal Real Definitivo"
    assert por_tipo["pre"]["etiqueta"] == "Antecedentes Costo Marginal Real Preliminar"


def test_dia_con_un_solo_documento() -> None:
    docs = cen.parsear_documentos(html("dia_2026-10-05.html"), date(2026, 10, 5))
    assert len(docs) == 1
    assert docs[0]["nombre"] == "Antecedentes_CMG_Real_pre_261005.zip"
    assert docs[0]["tipo"] == "pre"
    assert docs[0]["fecha_publicacion"] == "2026-10-06"


def test_la_carpeta_de_uploads_es_el_mes_de_publicacion() -> None:
    """Datos del 30-sep publicados el 01-oct viven en uploads/2026/10/.

    El codigo NO debe asumir que la carpeta coincide con el mes del dato.
    """
    docs = cen.parsear_documentos(html("dia_2026-09-30.html"), date(2026, 9, 30))
    assert len(docs) == 1
    doc = docs[0]
    assert doc["fecha_operacion"] == "2026-09-30"  # septiembre
    assert "/uploads/2026/10/" in doc["url"]  # octubre
    assert doc["fecha_publicacion"] == "2026-10-01"
    assert "260930" in doc["nombre"]


def test_pagina_de_mes_no_tiene_documentos() -> None:
    docs = cen.parsear_documentos(html("mes_octubre-2026.html"), date(2026, 10, 1))
    assert docs == []


def test_la_pagina_de_mes_tiene_un_enlace_por_dia_en_el_dom() -> None:
    """En el texto crudo cada dia sale dos veces, pero la primera esta dentro
    de un comentario HTML. BeautifulSoup ve UNO solo."""
    crudo = html("mes_octubre-2026.html")
    assert crudo.count("05-octubre-2026-costo-marginal-real") == 2
    enlaces = cen.parsear_enlaces_hijos(crudo)
    del_dia_5 = [u for u in enlaces if "05-octubre" in u]
    assert len(del_dia_5) == 1


def test_html_sin_nada_relevante() -> None:
    assert cen.parsear_documentos("<html><body><p>nada</p></body></html>", date(2026, 1, 1)) == []


# -------------------------------------------------- tipo y version del nombre


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("Antecedentes_CMG_Real_def_260115.zip", ("def", 1, 0)),
        ("Antecedentes_CMG_Real_pre_260115.zip", ("pre", 1, 0)),
        # version ANTES de la fecha (real: def_v2_260707, de la lista del usuario)
        ("Antecedentes_CMG_Real_def_v2_260707.zip", ("def", 2, 0)),
        ("Antecedentes_CMG_Real_pre_v3_260115.zip", ("pre", 3, 0)),
        # version DESPUES de la fecha (real: visto en la pagina del 2026-09-06)
        ("Antecedentes_CMG_Real_pre_260906_v2.zip", ("pre", 2, 0)),
        # re-subida con sufijo -N de WordPress (real: pagina del 2026-04-04)
        ("Antecedentes_CMG_Real_pre_260404-1.zip", ("pre", 1, 1)),
        ("Antecedentes_CMG_Real_xxx_260115.zip", ("desconocido", 1, 0)),
        ("otra_cosa.zip", ("desconocido", 1, 0)),
    ],
)
def test_tipo_version_y_reemision_del_nombre(nombre: str, esperado: tuple[str, int, int]) -> None:
    assert cen._info_nombre(nombre) == esperado


def test_ningun_nombre_real_queda_desconocido() -> None:
    """Regresion: estos nombres los publico el sitio y el patron original fallaba."""
    reales = [
        "Antecedentes_CMG_Real_def_260404.zip",
        "Antecedentes_CMG_Real_pre_260404-1.zip",
        "Antecedentes_CMG_Real_def_260906.zip",
        "Antecedentes_CMG_Real_pre_260906.zip",
        "Antecedentes_CMG_Real_pre_260906_v2.zip",
        "Antecedentes_CMG_Real_def_v2_260707.zip",
    ]
    for nombre in reales:
        assert cen._info_nombre(nombre)[0] != "desconocido", nombre


def test_mejor_version_prefiere_def_y_la_mayor_version() -> None:
    def doc(tipo: str, version: int, reemision: int = 0) -> cen.Documento:
        return {
            "fecha_operacion": "2026-01-15",
            "etiqueta": "x",
            "tipo": tipo,
            "version": version,
            "reemision": reemision,
            "fecha_publicacion": None,
            "url": "u",
            "nombre": f"{tipo}_v{version}",
        }

    assert cen.mejor_version([]) is None

    # def le gana a pre
    elegido = cen.mejor_version([doc("pre", 1), doc("def", 1)])
    assert elegido is not None
    assert elegido["tipo"] == "def"

    # entre dos def, la version mayor
    elegido = cen.mejor_version([doc("def", 1), doc("def", 2)])
    assert elegido is not None
    assert elegido["version"] == 2

    # def v1 le gana a pre v2: primero manda el tipo, despues la version
    elegido = cen.mejor_version([doc("pre", 2), doc("def", 1)])
    assert elegido is not None
    assert elegido["tipo"] == "def"

    # a igual tipo y version, la re-subida (-1) es la mas reciente
    elegido = cen.mejor_version([doc("pre", 1, 0), doc("pre", 1, 1)])
    assert elegido is not None
    assert elegido["reemision"] == 1


# ==================================================== sesion falsa (sin red)


class SesionFalsa:
    """Responde desde un diccionario de rutas. No toca la red.

    Se pasa donde el codigo espera una `Sesion` (un `Protocol`): no hace falta
    heredar de nada, basta tener el metodo `get` con la forma correcta.
    """

    def __init__(self, paginas: dict[str, str], zips: dict[str, bytes] | None = None):
        self.paginas = paginas
        self.zips = zips or {}
        self.pedidos: list[str] = []

    def get(self, url: str, timeout: float = 30.0) -> "RespuestaFalsa":
        self.pedidos.append(url)
        if url in self.paginas:
            return RespuestaFalsa(200, texto=self.paginas[url])
        if url in self.zips:
            return RespuestaFalsa(200, contenido=self.zips[url])
        return RespuestaFalsa(404)


class RespuestaFalsa:
    def __init__(self, status_code: int, texto: str = "", contenido: bytes = b""):
        self.status_code = status_code
        self.text = texto
        self.content = contenido

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_listar_documentos_contra_sesion_falsa() -> None:
    dia = date(2026, 1, 15)
    sesion = SesionFalsa({cen.url_dia(dia): html("dia_2026-01-15.html")})
    docs = cen.listar_documentos(dia, sesion)
    assert len(docs) == 2


def test_un_404_es_un_dia_sin_publicacion_no_un_error() -> None:
    sesion = SesionFalsa({})
    assert cen.listar_documentos(date(2026, 1, 15), sesion) == []


def test_un_500_si_levanta_excepcion() -> None:
    """Un 500 no significa 'no hay datos': no se puede tragar."""

    class SesionRota:
        def get(self, url: str, timeout: float = 30.0) -> RespuestaFalsa:
            return RespuestaFalsa(500)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        cen.listar_documentos(date(2026, 1, 15), SesionRota())


# ============================================= 2c) idempotencia de sincronizar


ZIP_FALSO = b"PK\x03\x04contenido-de-prueba"


def armar_sesion(dia: date, fixture: str) -> SesionFalsa:
    """Una sesion que sirve la pagina del dia y los ZIP que esa pagina enlaza."""
    pagina = html(fixture)
    docs = cen.parsear_documentos(pagina, dia)
    return SesionFalsa(
        paginas={cen.url_dia(dia): pagina},
        zips={d["url"]: ZIP_FALSO + d["nombre"].encode() for d in docs},
    )


def test_sincronizar_descarga_y_escribe_manifiesto(tmp_path: Path) -> None:
    dia = date(2026, 1, 15)
    sesion = armar_sesion(dia, "dia_2026-01-15.html")

    nuevos = cen.sincronizar(dia, dia, tmp_path, sesion, pausa=1.0)

    assert len(nuevos) == 2
    assert (tmp_path / "Antecedentes_CMG_Real_def_260115.zip").exists()
    assert (tmp_path / "Antecedentes_CMG_Real_pre_260115.zip").exists()

    manifiesto = json.loads(cen.ruta_manifiesto(tmp_path).read_text(encoding="utf-8"))
    assert len(manifiesto) == 2
    for entrada in manifiesto.values():
        assert len(entrada["sha256"]) == 64
        assert entrada["bytes"] > 0


def test_el_sha256_del_manifiesto_es_el_del_archivo(tmp_path: Path) -> None:
    dia = date(2026, 10, 5)
    sesion = armar_sesion(dia, "dia_2026-10-05.html")
    cen.sincronizar(dia, dia, tmp_path, sesion, pausa=1.0)

    manifiesto = cen.leer_manifiesto(tmp_path)
    for nombre, entrada in manifiesto.items():
        en_disco = (tmp_path / nombre).read_bytes()
        assert entrada["sha256"] == cen.sha256_bytes(en_disco)


def test_segunda_corrida_no_re_descarga(tmp_path: Path) -> None:
    """IDEMPOTENCIA: el mismo rango dos veces no baja nada nuevo."""
    dia = date(2026, 1, 15)
    sesion = armar_sesion(dia, "dia_2026-01-15.html")

    primera = cen.sincronizar(dia, dia, tmp_path, sesion, pausa=1.0)
    pedidos_tras_la_primera = len(sesion.pedidos)

    segunda = cen.sincronizar(dia, dia, tmp_path, sesion, pausa=1.0)

    assert len(primera) == 2
    assert segunda == []
    # pidio la pagina otra vez (hay que ver si aparecio un v2) pero NINGUN zip
    assert len(sesion.pedidos) == pedidos_tras_la_primera + 1


def test_una_revision_v2_se_baja_y_no_borra_la_anterior(tmp_path: Path) -> None:
    """Un `_def_v2_` tiene otro nombre, asi que entra y conviven las dos."""
    dia = date(2026, 1, 15)
    sesion = armar_sesion(dia, "dia_2026-01-15.html")
    cen.sincronizar(dia, dia, tmp_path, sesion, pausa=1.0)

    # el sitio publica una revision: misma pagina mas un documento v2
    pagina_v2 = html("dia_2026-01-15.html").replace(
        "</div>\n\n</div>",
        """</div>

  <div style="border-top: 1px solid #E3E3E3;margin-right: 0px;">
    <span class="informes-estudio-Titulo"
          title="Antecedentes Costo Marginal Real Definitivo V2">x</span>
    <span class="documentos-Publicar-Fecha">Fecha de publicaci&oacute;n: 05/02/2026</span>
    <a href="https://www.coordinador.cl/wp-content/uploads/2026/02/Antecedentes_CMG_Real_def_v2_260115.zip"
       class="cen_btn cen_btn-primary">Descargar ZIP</a>
  </div>

</div>""",
    )
    docs = cen.parsear_documentos(pagina_v2, dia)
    assert len(docs) == 3, "el fixture del v2 no se inyecto bien"

    sesion2 = SesionFalsa(
        paginas={cen.url_dia(dia): pagina_v2},
        zips={d["url"]: ZIP_FALSO + d["nombre"].encode() for d in docs},
    )
    nuevos = cen.sincronizar(dia, dia, tmp_path, sesion2, pausa=1.0)

    assert len(nuevos) == 1
    assert nuevos[0]["nombre"] == "Antecedentes_CMG_Real_def_v2_260115.zip"
    assert nuevos[0]["version"] == 2

    # las tres conviven, no se borro nada
    assert len(cen.leer_manifiesto(tmp_path)) == 3
    assert (tmp_path / "Antecedentes_CMG_Real_def_260115.zip").exists()
    assert (tmp_path / "Antecedentes_CMG_Real_def_v2_260115.zip").exists()


def test_un_dia_sin_publicacion_no_rompe_el_rango(tmp_path: Path) -> None:
    """Un 404 en medio del rango no detiene los otros dias."""
    sesion = armar_sesion(date(2026, 1, 15), "dia_2026-01-15.html")
    nuevos = cen.sincronizar(
        date(2026, 1, 14), date(2026, 1, 16), sesion=sesion, carpeta=tmp_path, pausa=1.0
    )
    assert len(nuevos) == 2


def test_pausa_menor_al_minimo_revienta(tmp_path: Path) -> None:
    """No se le pega a un sitio de terceros sin pausa."""
    sesion = SesionFalsa({})
    with pytest.raises(ValueError, match="menor que el minimo"):
        cen.sincronizar(date(2026, 1, 15), date(2026, 1, 15), tmp_path, sesion, pausa=0.1)


def test_manifiesto_inexistente_es_vacio(tmp_path: Path) -> None:
    assert cen.leer_manifiesto(tmp_path) == {}


def test_manifiesto_corrupto_se_trata_como_vacio(tmp_path: Path) -> None:
    """Mejor volver a descargar que abortar la sincronizacion."""
    cen.ruta_manifiesto(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    cen.ruta_manifiesto(tmp_path).write_text("{ esto no es json", encoding="utf-8")
    assert cen.leer_manifiesto(tmp_path) == {}


def test_el_manifiesto_se_escribe_de_forma_atomica(tmp_path: Path) -> None:
    """No queda un .tmp tras el exito."""
    dia = date(2026, 10, 5)
    sesion = armar_sesion(dia, "dia_2026-10-05.html")
    cen.sincronizar(dia, dia, tmp_path, sesion, pausa=1.0)
    assert list(tmp_path.glob("*.tmp")) == []
    assert cen.ruta_manifiesto(tmp_path).exists()


# ------------------------------------------------------------ desde_sugerido


def _entrada(dia: str, tipo: str) -> cen.EntradaManifiesto:
    return {
        "url": "",
        "nombre": f"{tipo}_{dia}.zip",
        "tipo": tipo,
        "version": 1,
        "reemision": 0,
        "fecha_operacion": dia,
        "fecha_publicacion": None,
        "sha256": "",
        "bytes": 0,
        "descargado_en": "",
    }


def _manifiesto(*entradas: cen.EntradaManifiesto) -> dict[str, cen.EntradaManifiesto]:
    return {e["nombre"]: e for e in entradas}


AYER = date(2026, 10, 6)


def test_desde_sugerido_sin_nada_descargado_es_el_backfill() -> None:
    assert cen.desde_sugerido({}, AYER) == cen.INICIO_FUENTE == date(2025, 1, 1)


def test_desde_sugerido_sigue_despues_del_ultimo_dia() -> None:
    m = _manifiesto(_entrada("2026-09-29", "def"), _entrada("2026-09-30", "def"))
    assert cen.desde_sugerido(m, AYER) == date(2026, 10, 1)


def test_desde_sugerido_vuelve_al_primer_dia_que_sigue_en_pre() -> None:
    """El def de un dia en pre puede haber llegado: hay que volver a mirarlo."""
    m = _manifiesto(
        _entrada("2026-09-20", "pre"),
        _entrada("2026-09-25", "pre"),
        _entrada("2026-09-25", "def"),  # este ya tiene definitivo
        _entrada("2026-09-30", "def"),
    )
    assert cen.desde_sugerido(m, AYER) == date(2026, 9, 20)


def test_desde_sugerido_nunca_pasa_de_ayer() -> None:
    m = _manifiesto(_entrada("2026-10-06", "def"))
    assert cen.desde_sugerido(m, AYER) == AYER
