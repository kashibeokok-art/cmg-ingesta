"""Tests de la descarga robusta (correcciones del 2026-10-08). Ninguno toca la red.

Cubre lo que el informe de busqueda y descarga encontro roto:
- P2: nombres que no se reconocian -> version equivocada (catalogo REAL de 1.765).
- P3: descarga no reanudable y sin reintentos (error A12).
- P5: empates entre versiones.
"""

import csv
import io
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

from cmg_ingesta.extract import coordinador_cmg as cen

FIXTURES = Path(__file__).parent.parent / "fixtures"
UP = "https://www.coordinador.cl/wp-content/uploads/2026/01/"


def zip_valido(texto: str = "x") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("contenido.txt", texto)
    return buf.getvalue()


class Respuesta:
    def __init__(
        self,
        status_code: int,
        texto: str = "",
        contenido: bytes = b"",
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.text = texto
        self.content = contenido
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class SesionGuion:
    """Responde siguiendo un guion: una respuesta (o una excepcion) por peticion."""

    def __init__(self, guion: list[Respuesta | Exception]):
        self.guion = list(guion)
        self.pedidos = 0

    def get(self, url: str, timeout: float = 30.0) -> Respuesta:
        self.pedidos += 1
        paso = self.guion.pop(0)
        if isinstance(paso, Exception):
            raise paso
        return paso


class SesionDict:
    """Responde desde un diccionario url -> respuesta; 404 para lo demas."""

    def __init__(self, respuestas: dict[str, Respuesta]):
        self.respuestas = respuestas
        self.pedidos: list[str] = []

    def get(self, url: str, timeout: float = 30.0) -> Respuesta:
        self.pedidos.append(url)
        return self.respuestas.get(url, Respuesta(404))


def sin_esperas(_s: float) -> None:
    """Para inyectar como `dormir`: los tests no duermen."""


# ================================================ catalogo real de nombres


def filas_reales() -> list[dict[str, str]]:
    with (FIXTURES / "nombres_reales.tsv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_ningun_nombre_publicado_queda_sin_reconocer() -> None:
    """REGRESION contra los 1.765 ZIP publicados entre 2024-08 y 2026-10.

    Con el lector anterior, 66 quedaban como "desconocido" y en 32 dias se elegia
    una version superada (en 5, un preliminar habiendo definitivo).
    """
    filas = filas_reales()
    assert len(filas) == 1765
    sin_reconocer = [
        f["nombre"] for f in filas if cen._info_nombre(f["nombre"])[0] == "desconocido"
    ]
    assert sin_reconocer == []


def test_todas_las_etiquetas_publicadas_se_leen() -> None:
    sin_leer = [f["etiqueta"] for f in filas_reales() if cen.info_etiqueta(f["etiqueta"]) is None]
    assert sin_leer == []


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("Antecedentes_CMG_Real_def-v2_250203.zip", ("def", 2, 0)),  # guion
        ("Antecedentes_CMG_Real_def-V2_240816.zip", ("def", 2, 0)),  # guion y mayuscula
        ("Antecedentes_CMG_Real_def_241008_V2.zip", ("def", 2, 0)),
        ("Antecedentes_CMG_Real_pre_260811_v2-1.zip", ("pre", 2, 1)),  # version y reemision
        ("Antecedentes_CMG_Real_pr_240907.zip", ("pre", 1, 0)),  # tipo abreviado
        ("Antecedentes_CMG_Real_prel_v2_250612.zip", ("pre", 2, 0)),  # tipo alargado
        ("Antecedentes_CMG_Real_pre_20250717.zip", ("pre", 1, 0)),  # fecha de 8 digitos
        ("Antecedentes_CMG_Real_def_25406.zip", ("def", 1, 0)),  # fecha de 5 digitos
        ("Antecedentes_CMG_Real_pre251116.zip", ("pre", 1, 0)),  # sin _ antes de la fecha
        ("Antecedentes_CMG_Real_pre-v2_250220_.zip", ("pre", 2, 0)),  # _ sobrante
        ("Antecedentes_CMG_Real_pre_251230_v2_.zip", ("pre", 2, 0)),
        ("Antecedentes_CMG_Real_pre.zip", ("pre", 1, 0)),  # sin fecha
        ("Antecedentes_CMG_Real_def-v3_v2.zip", ("def", 3, 0)),  # dos versiones: la mayor
    ],
)
def test_formas_raras_reales(nombre: str, esperado: tuple[str, int, int]) -> None:
    assert cen._info_nombre(nombre) == esperado


@pytest.mark.parametrize(
    ("etiqueta", "esperado"),
    [
        ("Antecedentes Costo Marginal Real Preliminar", ("pre", 1)),
        ("Antecedentes Costo Marginal Real Definitivo", ("def", 1)),
        ("Antecedentes Costo Marginal Real Definitivo v2", ("def", 2)),
        ("Antecedentes Costo Marginal Real Definitivo – V2", ("def", 2)),
        ("Antecedentes Costo Marginal Real Preliminar v3", ("pre", 3)),
        ("Descargar ZIP", None),
    ],
)
def test_info_etiqueta(etiqueta: str, esperado: tuple[str, int] | None) -> None:
    assert cen.info_etiqueta(etiqueta) == esperado


def test_clasificar_tipo_de_la_etiqueta_y_version_mayor() -> None:
    """Los dos tipos de choque reales entre nombre y etiqueta."""
    # 13 casos: la etiqueta omite el v2
    assert cen.clasificar_documento(
        "Antecedentes_CMG_Real_def_241201_v2.zip", "Antecedentes Costo Marginal Real Definitivo"
    ) == ("def", 2, 0)
    # 1 caso: el pre v3 se nombro "def"; la secuencia de publicacion le da la razon a la etiqueta
    assert cen.clasificar_documento(
        "Antecedentes_CMG_Real_def-v3_250225.zip",
        "Antecedentes Costo Marginal Real Preliminar v3",
    ) == ("pre", 3, 0)


def test_clasificar_sin_etiqueta_usa_el_nombre() -> None:
    assert cen.clasificar_documento("Antecedentes_CMG_Real_pre_260404-1.zip", "x") == ("pre", 1, 1)


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("Antecedentes_CMG_Real_def_260115.zip", date(2026, 1, 15)),
        ("Antecedentes_CMG_Real_pre_20250717.zip", date(2025, 7, 17)),
        ("Antecedentes_CMG_Real_def_25406.zip", None),  # 5 digitos: no se adivina
        ("Antecedentes_CMG_Real_pre_2507010.zip", None),  # 7 digitos
        ("Antecedentes_CMG_Real_pre.zip", None),
    ],
)
def test_fecha_del_nombre(nombre: str, esperado: date | None) -> None:
    assert cen.fecha_del_nombre(nombre) == esperado


def documentos_por_dia() -> dict[str, list[cen.Documento]]:
    por_dia: dict[str, list[cen.Documento]] = defaultdict(list)
    for f in filas_reales():
        tipo, version, reemision = cen.clasificar_documento(f["nombre"], f["etiqueta"])
        por_dia[f["dia"]].append(
            {
                "fecha_operacion": f["dia"],
                "etiqueta": f["etiqueta"],
                "tipo": tipo,
                "version": version,
                "reemision": reemision,
                "fecha_publicacion": f["publicado"] or None,
                "url": "",
                "nombre": f["nombre"],
            }
        )
    return por_dia


def test_los_peores_casos_reales_eligen_bien() -> None:
    por_dia = documentos_por_dia()
    # antes: el PRELIMINAR pre_241121, porque no reconocia def-v2_241112
    elegido = cen.mejor_version(por_dia["2024-11-12"])
    assert elegido is not None and elegido["nombre"] == "Antecedentes_CMG_Real_def-v2_241112.zip"
    # dos "definitivo v3" el mismo dia: gana el publicado despues (2026-08-25)
    elegido = cen.mejor_version(por_dia["2025-02-25"])
    assert elegido is not None and elegido["nombre"] == "Antecedentes_CMG_Real_def-v3_v2.zip"


def test_ningun_dia_real_termina_con_preliminar_si_hay_definitivo() -> None:
    for docs in documentos_por_dia().values():
        elegido = cen.mejor_version(docs)
        assert elegido is not None
        if any(d["tipo"] == "def" for d in docs):
            assert elegido["tipo"] == "def", [d["nombre"] for d in docs]


def test_clave_version_desempata_por_fecha_de_publicacion() -> None:
    def doc(pub: str | None) -> cen.Documento:
        return {
            "fecha_operacion": "2025-02-25",
            "etiqueta": "",
            "tipo": "def",
            "version": 3,
            "reemision": 0,
            "fecha_publicacion": pub,
            "url": "",
            "nombre": str(pub),
        }

    elegido = cen.mejor_version([doc("2026-08-17"), doc("2026-08-25"), doc(None)])
    assert elegido is not None and elegido["fecha_publicacion"] == "2026-08-25"


# ============================================================ reintentos


def test_pedir_reintenta_429_y_5xx_con_espera_creciente() -> None:
    esperas: list[float] = []
    sesion = SesionGuion([Respuesta(503), Respuesta(429), Respuesta(200, "ok")])
    assert cen.pedir(sesion, "u", dormir=esperas.append).status_code == 200
    assert esperas == [2.0, 4.0]


def test_pedir_respeta_retry_after() -> None:
    esperas: list[float] = []
    sesion = SesionGuion([Respuesta(429, headers={"Retry-After": "17"}), Respuesta(200, "ok")])
    cen.pedir(sesion, "u", dormir=esperas.append)
    assert esperas == [17.0]


def test_pedir_reintenta_una_conexion_cortada() -> None:
    sesion = SesionGuion([ConnectionError("reset by peer"), Respuesta(200, "ok")])
    assert cen.pedir(sesion, "u", dormir=sin_esperas).status_code == 200


def test_pedir_se_rinde_y_dice_por_que() -> None:
    sesion = SesionGuion([Respuesta(502)] * cen.REINTENTOS)
    with pytest.raises(cen.ErrorDescarga, match=r"HTTP 502 \(tras 4 intentos\)"):
        cen.pedir(sesion, "u", dormir=sin_esperas)
    assert sesion.pedidos == cen.REINTENTOS


@pytest.mark.parametrize("codigo", [404, 403])
def test_pedir_no_reintenta_lo_que_esperar_no_arregla(codigo: int) -> None:
    sesion = SesionGuion([Respuesta(codigo)])
    assert cen.pedir(sesion, "u", dormir=sin_esperas).status_code == codigo
    assert sesion.pedidos == 1


# ================================================== verificacion del ZIP


def test_problema_del_zip() -> None:
    bueno = zip_valido("hola")
    assert cen.problema_del_zip(bueno) is None
    assert cen.problema_del_zip(bueno, str(len(bueno))) is None
    assert "anunciados" in (cen.problema_del_zip(bueno[:-5], str(len(bueno))) or "")
    assert cen.problema_del_zip(b"<html>error</html>") == "no es un ZIP valido"


def test_bajar_zip_reintenta_un_zip_truncado() -> None:
    bueno = zip_valido("hola")
    truncado = Respuesta(200, contenido=bueno[:-10], headers={"Content-Length": str(len(bueno))})
    sesion = SesionGuion([truncado, Respuesta(200, contenido=bueno)])
    assert cen.bajar_zip(sesion, "u", dormir=sin_esperas) == (bueno, "")


def test_bajar_zip_ignora_content_length_si_hay_compresion() -> None:
    """Con Content-Encoding, Content-Length es el tamaño comprimido: no se compara."""
    bueno = zip_valido("hola")
    r = Respuesta(
        200, contenido=bueno, headers={"Content-Length": "10", "Content-Encoding": "gzip"}
    )
    assert cen.bajar_zip(SesionGuion([r]), "u", dormir=sin_esperas) == (bueno, "")


def test_bajar_zip_que_sigue_mal_devuelve_el_motivo() -> None:
    sesion = SesionGuion([Respuesta(200, contenido=b"no-zip")] * cen.REINTENTOS)
    assert cen.bajar_zip(sesion, "u", dormir=sin_esperas) == (None, "no es un ZIP valido")


# ======================================================== reanudabilidad


def pagina_con(*zips: str) -> str:
    return "".join(
        '<div><span class="informes-estudio-Titulo" title="Antecedentes Costo Marginal Real '
        'Definitivo">x</span> Fecha de publicaci&oacute;n: 25/01/2026 '
        f'<a href="{z}">ZIP</a></div>'
        for z in zips
    )


def test_un_corte_a_mitad_no_obliga_a_bajar_de_nuevo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESION A12: antes el manifiesto se guardaba solo al final; un 503 en el
    tercer ZIP dejaba los dos primeros sin registrar y la segunda corrida los volvia
    a pedir. Ahora cada ZIP queda registrado apenas llega."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    dias = [date(2026, 1, 15), date(2026, 1, 16), date(2026, 1, 17)]
    zips = [UP + f"Antecedentes_CMG_Real_def_{d:%y%m%d}.zip" for d in dias]
    paginas = {
        cen.url_dia(d): Respuesta(200, pagina_con(z)) for d, z in zip(dias, zips, strict=True)
    }

    rota = SesionDict(
        {
            **paginas,
            zips[0]: Respuesta(200, contenido=zip_valido("a")),
            zips[1]: Respuesta(200, contenido=zip_valido("b")),
            zips[2]: Respuesta(503),
        }
    )
    with pytest.raises(cen.ErrorDescarga, match="503"):
        cen.sincronizar(dias[0], dias[-1], tmp_path, rota, pausa=1.0)
    nombres = [z.rsplit("/", 1)[-1] for z in zips]
    assert sorted(cen.leer_manifiesto(tmp_path)) == nombres[:2]

    sana = SesionDict({**paginas, **{z: Respuesta(200, contenido=zip_valido(z)) for z in zips}})
    nuevos = cen.sincronizar(dias[0], dias[-1], tmp_path, sana, pausa=1.0)
    assert [n["nombre"] for n in nuevos] == [nombres[2]]
    assert [p for p in sana.pedidos if p.endswith(".zip")] == [zips[2]]


def test_no_quedan_archivos_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    dia = date(2026, 1, 15)
    z = UP + "Antecedentes_CMG_Real_def_260115.zip"
    sesion = SesionDict(
        {cen.url_dia(dia): Respuesta(200, pagina_con(z)), z: Respuesta(200, contenido=zip_valido())}
    )
    cen.sincronizar(dia, dia, tmp_path, sesion, pausa=1.0)
    assert list(tmp_path.glob("*.part")) == []
    assert (tmp_path / "Antecedentes_CMG_Real_def_260115.zip").exists()


def test_el_filtro_de_archivos_no_distingue_mayusculas() -> None:
    dia = date(2026, 1, 15)
    docs = cen.parsear_documentos(pagina_con(UP + "Antecedentes_CMg_Real_def_260115.zip"), dia)
    assert len(docs) == 1
