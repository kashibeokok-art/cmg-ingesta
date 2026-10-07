"""Tests de la deteccion de cambios en la fuente. Ninguno toca la red.

Tres clases de test:
- LINEA BASE: sobre HTML y estructuras reales, no debe haber hallazgos. Un
  vigilante con falsas alarmas termina ignorado.
- CASOS DE CAMBIO: se altera una cosa a la vez y se verifica que se detecte con
  la severidad correcta.
- AUTO-ACTUALIZACION: un año nuevo en el indice se adopta solo.
"""

import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.quality import deriva

FIXTURES = Path(__file__).parent.parent / "fixtures"


def html(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def tipos(hallazgos: list[deriva.Hallazgo]) -> set[str]:
    return {h["tipo"] for h in hallazgos}


def severidad_de(hallazgos: list[deriva.Hallazgo], tipo: str) -> str:
    return next(h["severidad"] for h in hallazgos if h["tipo"] == tipo)


class RespuestaFalsa:
    def __init__(self, status_code: int, texto: str = "", contenido: bytes = b""):
        self.status_code = status_code
        self.text = texto
        self.content = contenido

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class SesionFalsa:
    """Responde desde diccionarios. Cumple el Protocol `Sesion` sin heredar."""

    def __init__(self, paginas: dict[str, str], zips: dict[str, bytes] | None = None):
        self.paginas = paginas
        self.zips = zips or {}
        self.pedidos: list[str] = []

    def get(self, url: str, timeout: float = 30.0) -> RespuestaFalsa:
        self.pedidos.append(url)
        if url in self.paginas:
            return RespuestaFalsa(200, texto=self.paginas[url])
        if url in self.zips:
            return RespuestaFalsa(200, contenido=self.zips[url])
        return RespuestaFalsa(404)


# ====================================================================== slugs


def test_el_indice_real_da_los_mismos_slugs_que_sufijo() -> None:
    """Linea base: lo que se lee del sitio hoy coincide con lo que estaba fijo."""
    assert cen.parsear_slugs_anio(html("indice_anios.html")) == cen.SUFIJO


def test_revisar_slugs_sin_cambios() -> None:
    assert deriva.revisar_slugs(dict(cen.SUFIJO), dict(cen.SUFIJO)) == []


def test_un_año_nuevo_se_informa_como_adoptado() -> None:
    sitio = {**cen.SUFIJO, 2027: "-costo-marginal-real"}
    h = deriva.revisar_slugs(sitio, dict(cen.SUFIJO))
    assert tipos(h) == {"año_nuevo"}
    assert severidad_de(h, "año_nuevo") == "info"


def test_un_slug_cambiado_es_aviso() -> None:
    sitio = {**cen.SUFIJO, 2025: "-costo-marginal-real-otra-cosa"}
    h = deriva.revisar_slugs(sitio, dict(cen.SUFIJO))
    assert tipos(h) == {"slug_cambiado"}
    assert severidad_de(h, "slug_cambiado") == "aviso"


def test_un_año_que_desaparece_del_indice_es_aviso() -> None:
    sitio = {k: v for k, v in cen.SUFIJO.items() if k != 2024}
    h = deriva.revisar_slugs(sitio, dict(cen.SUFIJO))
    assert tipos(h) == {"año_ya_no_listado"}


def test_un_indice_sin_años_es_critico() -> None:
    h = deriva.revisar_slugs({}, dict(cen.SUFIJO))
    assert severidad_de(h, "indice_sin_años") == "critico"


#: El indice real mas un enlace de 2027, agregado como ENLACE completo. (Reemplazar
#: un texto del HTML no sirve: la primera aparicion de un slug puede estar en el
#: menu o en texto suelto, fuera de un href, y el enlace no se veria.)
INDICE_2027 = html("indice_anios.html") + (
    '<a href="https://www.coordinador.cl/mercados/documentos/transferencias-economicas/'
    'costo-marginal-real/2027-costo-marginal-real" class="cen_btn cen_btn-primary">'
    "Ver documentos</a>"
)


def test_no_consulta_el_indice_si_conoce_todos_los_años(tmp_path: Path) -> None:
    sesion = SesionFalsa({})
    slugs, h = deriva.actualizar_slugs(sesion, tmp_path, [2025, 2026])
    assert sesion.pedidos == []
    assert h == []
    assert slugs[2026] == cen.SUFIJO[2026]


def test_un_año_desconocido_se_adopta_solo_desde_el_indice(tmp_path: Path) -> None:
    """AUTO-ACTUALIZACION: el 1 de enero de 2027 no hay que tocar codigo."""
    sesion = SesionFalsa({cen.INDICE: INDICE_2027})
    slugs, h = deriva.actualizar_slugs(sesion, tmp_path, [2027])

    assert sesion.pedidos == [cen.INDICE]
    assert slugs[2027] == "-costo-marginal-real"
    assert tipos(h) == {"año_nuevo"}
    # y la URL se construye sin tocar SUFIJO
    assert cen.url_dia(date(2027, 1, 15), slugs).endswith("/15-enero-2027-costo-marginal-real/")


def test_el_slug_adoptado_queda_guardado(tmp_path: Path) -> None:
    deriva.actualizar_slugs(SesionFalsa({cen.INDICE: INDICE_2027}), tmp_path, [2027])

    # una segunda corrida ya no consulta el indice
    otra = SesionFalsa({})
    slugs, _ = deriva.actualizar_slugs(otra, tmp_path, [2027])
    assert otra.pedidos == []
    assert slugs[2027] == "-costo-marginal-real"


def test_forzar_consulta_el_indice_aunque_conozca_todo(tmp_path: Path) -> None:
    sesion = SesionFalsa({cen.INDICE: html("indice_anios.html")})
    deriva.actualizar_slugs(sesion, tmp_path, [2026], forzar=True)
    assert sesion.pedidos == [cen.INDICE]


def test_sin_slugs_un_año_desconocido_sigue_reventando() -> None:
    """Si nadie actualizo los slugs, url_dia sigue fallando fuerte, como antes."""
    with pytest.raises(ValueError, match="Slug desconocido"):
        cen.url_dia(date(2027, 1, 15))


# ============================================================ pagina de dia

PAGINAS_REALES = [
    ("dia_2026-01-15.html", date(2026, 1, 15)),
    ("dia_2026-10-05.html", date(2026, 10, 5)),
    ("dia_2026-09-30.html", date(2026, 9, 30)),
]


@pytest.mark.parametrize(("fixture", "dia"), PAGINAS_REALES)
def test_las_paginas_reales_no_generan_hallazgos(fixture: str, dia: date) -> None:
    """LINEA BASE: el sitio tal como esta hoy no dispara ninguna alarma."""
    pagina = html(fixture)
    assert deriva.revisar_pagina_dia(dia, pagina, cen.parsear_documentos(pagina, dia)) == []


def _pagina_con(href: str, fecha: str = "Fecha de publicaci&oacute;n: 22/01/2026") -> str:
    return f"""
    <div style="border-top: 1px solid #E3E3E3;">
      <span class="informes-estudio-Titulo" title="Algo">Algo</span>
      <span class="documentos-Publicar-Fecha">{fecha}</span>
      <a href="{href}" class="cen_btn cen_btn-primary">Descargar ZIP</a>
    </div>"""


UP = "https://www.coordinador.cl/wp-content/uploads/2026/01/"


def test_un_nombre_no_catalogado_se_avisa() -> None:
    dia = date(2026, 1, 15)
    pagina = _pagina_con(UP + "Antecedentes_CMG_Real_def_260115_final.zip")
    h = deriva.revisar_pagina_dia(dia, pagina, cen.parsear_documentos(pagina, dia))
    assert severidad_de(h, "nombre_no_catalogado") == "aviso"
    assert "parece 'def'" in next(x["detalle"] for x in h if x["tipo"] == "nombre_no_catalogado")


def test_una_fecha_de_publicacion_que_no_se_encuentra_se_avisa() -> None:
    """Es la señal de que cambio el markup del bloque."""
    dia = date(2026, 1, 15)
    pagina = _pagina_con(
        UP + "Antecedentes_CMG_Real_def_260115.zip", fecha="Publicado el 22-01-2026"
    )
    h = deriva.revisar_pagina_dia(dia, pagina, cen.parsear_documentos(pagina, dia))
    assert "fecha_publicacion_no_encontrada" in tipos(h)


def test_zip_en_la_pagina_sin_ningun_documento_cmg_es_critico() -> None:
    """El producto cambio de nombre: el filtro 'CMG_Real' ya no lo encuentra."""
    dia = date(2026, 1, 15)
    pagina = _pagina_con(UP + "Antecedentes_CostoMarginal_def_260115.zip")
    h = deriva.revisar_pagina_dia(dia, pagina, cen.parsear_documentos(pagina, dia))
    assert severidad_de(h, "pagina_con_zip_sin_documentos") == "critico"


def test_un_zip_extra_que_no_es_cmg_se_informa() -> None:
    dia = date(2026, 1, 15)
    pagina = _pagina_con(UP + "Antecedentes_CMG_Real_def_260115.zip") + _pagina_con(
        UP + "Otro_Producto_260115.zip"
    )
    h = deriva.revisar_pagina_dia(dia, pagina, cen.parsear_documentos(pagina, dia))
    assert tipos(h) == {"archivo_no_cmg_en_pagina"}
    assert severidad_de(h, "archivo_no_cmg_en_pagina") == "info"


def test_un_archivo_de_otra_fecha_en_la_pagina_se_avisa() -> None:
    dia = date(2026, 1, 15)
    pagina = _pagina_con(UP + "Antecedentes_CMG_Real_def_260116.zip")
    h = deriva.revisar_pagina_dia(dia, pagina, cen.parsear_documentos(pagina, dia))
    assert "fecha_del_nombre_distinta" in tipos(h)


# =================================================================== miembros

MIEMBROS_REALES = [
    "FPen_Mapeo_Barras.xlsx",
    "PromediosBarras_20260404_20260404_R.csv",
    "Barras_Subsistemas_20260404.xlsx",
    "CmgBarrasComparativo_20260404_20260404_15.csv",
    "CMgBarrasMinuto_20260404_20260404.zip",
    "Fpen_20260404.xlsx",
]


def test_los_miembros_reales_no_generan_hallazgos() -> None:
    assert deriva.revisar_miembros(MIEMBROS_REALES) == []


def test_los_miembros_de_un_pre_tambien_calzan() -> None:
    pre = [m.replace("_R.csv", "_P.csv") for m in MIEMBROS_REALES]
    assert deriva.revisar_miembros(pre) == []


def test_un_archivo_nuevo_en_el_zip_es_informativo() -> None:
    h = deriva.revisar_miembros([*MIEMBROS_REALES, "Nuevo_Reporte_20260404.csv"])
    assert tipos(h) == {"archivo_nuevo_en_zip"}
    assert severidad_de(h, "archivo_nuevo_en_zip") == "info"


def test_si_falta_el_comparativo_es_critico() -> None:
    sin = [m for m in MIEMBROS_REALES if not m.startswith("CmgBarrasComparativo")]
    h = deriva.revisar_miembros(sin)
    assert severidad_de(h, "falta_archivo_requerido") == "critico"


def test_si_falta_un_archivo_opcional_es_aviso() -> None:
    sin = [m for m in MIEMBROS_REALES if not m.startswith("Fpen_")]
    h = deriva.revisar_miembros(sin)
    assert severidad_de(h, "falta_archivo_opcional") == "aviso"


# ================================================================ encabezado

ESP = deriva.ENCABEZADO_COMPARATIVO
REQ = deriva.REQUERIDAS_COMPARATIVO


def test_encabezado_exacto() -> None:
    assert deriva.revisar_encabezado("x.csv", list(ESP), ESP, REQ) == []


def test_una_columna_nueva_es_aviso() -> None:
    h = deriva.revisar_encabezado("x.csv", [*ESP, "CMG_NUEVO[USD/MWh]"], ESP, REQ)
    assert tipos(h) == {"columnas_nuevas"}
    assert severidad_de(h, "columnas_nuevas") == "aviso"


def test_si_falta_una_requerida_es_critico() -> None:
    sin = [c for c in ESP if c != "CMG_REAL_DEF[USD/MWh]"]
    h = deriva.revisar_encabezado("x.csv", sin, ESP, REQ)
    assert severidad_de(h, "columna_requerida_ausente") == "critico"


def test_si_falta_una_no_requerida_es_aviso() -> None:
    sin = [c for c in ESP if c != "MODIFICADO"]
    h = deriva.revisar_encabezado("x.csv", sin, ESP, REQ)
    assert tipos(h) == {"columnas_eliminadas"}


def test_un_reordenamiento_es_solo_informativo() -> None:
    """La lectura es por nombre de columna, asi que reordenar no rompe nada."""
    h = deriva.revisar_encabezado("x.csv", list(reversed(ESP)), ESP, REQ)
    assert tipos(h) == {"columnas_reordenadas"}
    assert severidad_de(h, "columnas_reordenadas") == "info"


# ============================================================== forma del dia


def filas(
    dia: date, horas: list[int], barras: tuple[str, ...] = ("B1", "B2")
) -> list[tuple[str, int, str]]:
    f = dia.strftime("%Y%m%d")
    return [(f, h, b) for b in barras for h in horas for _ in range(4)]


def test_dia_normal_completo() -> None:
    d = date(2026, 6, 15)
    assert deriva.revisar_forma_dia(d, filas(d, list(range(24)))) == []


def test_dia_largo_con_la_hora_extra() -> None:
    d = date(2026, 4, 4)
    assert deriva.revisar_forma_dia(d, filas(d, list(range(25)))) == []


def test_dia_largo_sin_la_hora_extra_es_critico() -> None:
    """El defecto que tenia la API: si el archivo lo hiciera, se detecta."""
    d = date(2026, 4, 4)
    h = deriva.revisar_forma_dia(d, filas(d, list(range(24))))
    assert severidad_de(h, "falta_hora_extra") == "critico"


def test_dia_corto_con_la_hora_fantasma_es_conocido() -> None:
    d = date(2026, 9, 6)
    h = deriva.revisar_forma_dia(d, filas(d, list(range(24))))
    assert tipos(h) == {"hora_fantasma_presente"}
    assert severidad_de(h, "hora_fantasma_presente") == "info"


def test_dia_corto_sin_la_hora_fantasma_tambien_se_informa() -> None:
    """Si el CEN deja de publicarla, es un cambio de comportamiento (bueno)."""
    d = date(2026, 9, 6)
    h = deriva.revisar_forma_dia(d, filas(d, list(range(1, 24))))
    assert tipos(h) == {"hora_fantasma_ausente"}


def test_hora_en_base_1_es_critico() -> None:
    """Confundir base 0 y base 1 corre todo una hora sin ningun error."""
    d = date(2026, 6, 15)
    h = deriva.revisar_forma_dia(d, filas(d, list(range(1, 25))))
    assert severidad_de(h, "convencion_hora_base_1") == "critico"


def test_hora_en_base_1_el_dia_largo_tambien_se_detecta() -> None:
    d = date(2026, 4, 4)
    h = deriva.revisar_forma_dia(d, filas(d, list(range(1, 26))))
    assert "convencion_hora_base_1" in tipos(h) or "hora_fuera_de_rango" in tipos(h)


def test_el_dia_corto_correcto_no_se_confunde_con_base_1() -> None:
    """REGRESION: horas 1..23 el dia corto son CORRECTAS en base 0, no base 1.

    La primera version de la regla daba falsa alarma critica justo aqui.
    """
    d = date(2026, 9, 6)
    h = deriva.revisar_forma_dia(d, filas(d, list(range(1, 24))))
    assert "convencion_hora_base_1" not in tipos(h)


def test_fechas_de_otro_dia_es_critico() -> None:
    d = date(2026, 6, 15)
    otras = filas(date(2026, 6, 16), list(range(24)))
    h = deriva.revisar_forma_dia(d, otras)
    assert severidad_de(h, "fechas_inesperadas") == "critico"


def test_una_barra_incompleta_es_aviso() -> None:
    d = date(2026, 6, 15)
    datos = filas(d, list(range(24)), ("B1", "B2", "B3")) + []
    datos = [x for x in datos if not (x[2] == "B3" and x[1] == 5)]
    h = deriva.revisar_forma_dia(d, datos)
    assert tipos(h) == {"barras_con_otro_largo"}


def test_archivo_vacio_es_critico() -> None:
    h = deriva.revisar_forma_dia(date(2026, 6, 15), [])
    assert severidad_de(h, "archivo_vacio") == "critico"


# ======================================================================= ZIP


def armar_zip(
    ruta: Path,
    dia: date,
    horas: list[int],
    quitar: str | None = None,
    cabecera: tuple[str, ...] = ESP,
) -> Path:
    """Un ZIP con los miembros reales y un comparativo sintetico."""
    f = dia.strftime("%Y%m%d")
    lineas = [";".join(cabecera)]
    for barra in ("A.BLANCAS_____013", "STA.ELVIRA____013"):
        for h in horas:
            for m in (0, 15, 30, 45):
                lineas.append(f"{f};{h};{m};{barra};141;X;50.1;50.1;50.1;915.95;NO")
    miembros = {
        f"CmgBarrasComparativo_{f}_{f}_15.csv": "\n".join(lineas),
        f"PromediosBarras_{f}_{f}_R.csv": ";".join(deriva.ENCABEZADO_PROMEDIOS),
        f"CMgBarrasMinuto_{f}_{f}.zip": "",
        f"Fpen_{f}.xlsx": "",
        "FPen_Mapeo_Barras.xlsx": "",
        f"Barras_Subsistemas_{f}.xlsx": "",
    }
    with zipfile.ZipFile(ruta, "w") as zf:
        for nombre, contenido in miembros.items():
            if quitar and nombre.startswith(quitar):
                continue
            zf.writestr(nombre, contenido)
    return ruta


def test_un_zip_sano_no_genera_hallazgos(tmp_path: Path) -> None:
    d = date(2026, 6, 15)
    assert deriva.revisar_zip(armar_zip(tmp_path / "a.zip", d, list(range(24))), d) == []


def test_un_zip_del_dia_largo_sano(tmp_path: Path) -> None:
    d = date(2026, 4, 4)
    assert deriva.revisar_zip(armar_zip(tmp_path / "a.zip", d, list(range(25))), d) == []


def test_un_zip_corrupto_es_critico(tmp_path: Path) -> None:
    malo = tmp_path / "malo.zip"
    malo.write_bytes(b"esto no es un zip")
    h = deriva.revisar_zip(malo, date(2026, 6, 15))
    assert severidad_de(h, "zip_corrupto") == "critico"


def test_un_zip_sin_comparativo_es_critico(tmp_path: Path) -> None:
    d = date(2026, 6, 15)
    ruta = armar_zip(tmp_path / "a.zip", d, list(range(24)), quitar="CmgBarrasComparativo")
    assert severidad_de(deriva.revisar_zip(ruta, d), "falta_archivo_requerido") == "critico"


def test_si_falta_una_columna_requerida_no_se_evalua_la_forma(tmp_path: Path) -> None:
    """Sin HORA o BARRA no tiene sentido contar cuartos: se corta ahi."""
    d = date(2026, 6, 15)
    cab = tuple(c for c in ESP if c != "CMG_REAL_DEF[USD/MWh]")
    h = deriva.revisar_zip(armar_zip(tmp_path / "a.zip", d, list(range(24)), cabecera=cab), d)
    assert "columna_requerida_ausente" in tipos(h)
    assert "largo_de_dia_inesperado" not in tipos(h)


def test_un_zip_del_dia_largo_al_que_le_falta_la_hora_extra(tmp_path: Path) -> None:
    d = date(2026, 4, 4)
    h = deriva.revisar_zip(armar_zip(tmp_path / "a.zip", d, list(range(24))), d)
    assert severidad_de(h, "falta_hora_extra") == "critico"


# ============================================================== completitud


def entrada(dia: date, tipo: str = "def") -> cen.EntradaManifiesto:
    """Una fila del manifiesto con lo minimo que mira `revisar_completitud`."""
    nombre = f"CMG_Real_{tipo}_{dia:%y%m%d}.zip"
    return {
        "url": UP + nombre,
        "nombre": nombre,
        "tipo": tipo,
        "version": 0,
        "reemision": 0,
        "fecha_operacion": dia.isoformat(),
        "fecha_publicacion": None,
        "sha256": "x",
        "bytes": 1,
        "descargado_en": "2026-10-06T00:00:00",
    }


def manifiesto(*entradas: cen.EntradaManifiesto) -> dict[str, cen.EntradaManifiesto]:
    return {e["nombre"]: e for e in entradas}


HOY = date(2026, 10, 6)


def test_rangos_agrupa_dias_consecutivos() -> None:
    d = date(2026, 1, 1)
    dias = [d, date(2026, 1, 3), date(2026, 1, 2), date(2026, 1, 7)]
    assert deriva._rangos(dias) == [(d, date(2026, 1, 3)), (date(2026, 1, 7), date(2026, 1, 7))]


def test_completitud_todo_con_definitivo_no_avisa() -> None:
    dias = cen.dias_entre(date(2026, 9, 1), date(2026, 9, 10))
    m = manifiesto(*(entrada(d) for d in dias))
    assert deriva.revisar_completitud(m, date(2026, 9, 1), date(2026, 9, 10), HOY) == []


def test_un_hueco_de_varios_dias_es_un_solo_hallazgo() -> None:
    """Caso: la tarea programada no corrio una semana. Un aviso, no siete."""
    m = manifiesto(entrada(date(2026, 9, 1)), entrada(date(2026, 9, 9)))
    h = deriva.revisar_completitud(m, date(2026, 9, 1), date(2026, 9, 9), HOY)
    assert len(h) == 1
    assert h[0]["tipo"] == "dia_sin_registro"
    assert h[0]["severidad"] == "aviso"
    assert h[0]["evidencia"] == "2026-09-02 a 2026-09-08"
    assert "--desde 2026-09-02 --hasta 2026-09-08" in h[0]["accion"]


def test_dos_huecos_separados_son_dos_hallazgos() -> None:
    m = manifiesto(entrada(date(2026, 9, 1)), entrada(date(2026, 9, 3)), entrada(date(2026, 9, 5)))
    h = deriva.revisar_completitud(m, date(2026, 9, 1), date(2026, 9, 5), HOY)
    assert [x["evidencia"] for x in h] == ["2026-09-02", "2026-09-04"]


def test_los_dias_recientes_sin_archivo_no_avisan() -> None:
    """El preliminar sale al dia siguiente: ayer sin archivo es normal."""
    m = manifiesto(entrada(date(2026, 10, 1)))
    h = deriva.revisar_completitud(m, date(2026, 10, 1), HOY, HOY)
    # 10-02 y 10-03 tienen 4 y 3 dias (>= gracia); 10-04 a 10-06 todavia no.
    assert [x["evidencia"] for x in h] == ["2026-10-02 a 2026-10-03"]


def test_un_pre_reciente_no_avisa() -> None:
    dia = HOY - timedelta(days=deriva.DIAS_MAX_PRE)
    m = manifiesto(entrada(dia, "pre"))
    assert deriva.revisar_completitud(m, dia, dia, HOY) == []


def test_un_pre_viejo_sin_definitivo_avisa() -> None:
    dia = HOY - timedelta(days=deriva.DIAS_MAX_PRE + 1)
    m = manifiesto(entrada(dia, "pre"))
    h = deriva.revisar_completitud(m, dia, dia, HOY)
    assert tipos(h) == {"pre_sin_definitivo"}
    assert f"lleva {deriva.DIAS_MAX_PRE + 1} dias" in h[0]["detalle"]


def test_un_pre_que_ya_tiene_definitivo_no_avisa() -> None:
    """El manifiesto guarda ambos: el pre no se borra cuando llega el def."""
    dia = date(2026, 8, 1)
    m = manifiesto(entrada(dia, "pre"), entrada(dia, "def"))
    assert deriva.revisar_completitud(m, dia, dia, HOY) == []


def test_huecos_y_pres_viejos_se_reportan_por_separado() -> None:
    m = manifiesto(
        entrada(date(2026, 8, 1), "pre"),
        entrada(date(2026, 8, 2), "pre"),
        entrada(date(2026, 8, 4)),
    )
    h = deriva.revisar_completitud(m, date(2026, 8, 1), date(2026, 8, 4), HOY)
    assert [(x["tipo"], x["evidencia"]) for x in h] == [
        ("dia_sin_registro", "2026-08-03"),
        ("pre_sin_definitivo", "2026-08-01 a 2026-08-02"),
    ]


def test_los_umbrales_se_pueden_ajustar() -> None:
    dia = date(2026, 10, 1)
    m = manifiesto(entrada(dia, "pre"))
    assert deriva.revisar_completitud(m, dia, dia, HOY) == []
    h = deriva.revisar_completitud(m, dia, dia, HOY, dias_max_pre=2)
    assert tipos(h) == {"pre_sin_definitivo"}


# ============================================================ orquestacion


def test_hay_que_revisar() -> None:
    info = deriva._h("info", "x", "", "", "")
    aviso = deriva._h("aviso", "x", "", "", "")
    assert not deriva.hay_que_revisar([])
    assert not deriva.hay_que_revisar([info])
    assert deriva.hay_que_revisar([info, aviso])


def test_reporte_vacio_no_escribe_nada(tmp_path: Path) -> None:
    assert deriva.escribir_reporte([], tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_reporte_escribe_md_y_json_ordenados(tmp_path: Path) -> None:
    hallazgos = [
        deriva._h("info", "b", "detalle b", "ev", "nada"),
        deriva._h("critico", "a", "detalle a", "ev", "arreglar"),
    ]
    rutas = deriva.escribir_reporte(hallazgos, tmp_path)
    assert rutas is not None
    md, js = rutas
    datos = json.loads(js.read_text(encoding="utf-8"))
    assert [d["severidad"] for d in datos] == ["critico", "info"]
    texto = md.read_text(encoding="utf-8")
    assert "1 critico(s)" in texto
    assert texto.index("[CRITICO]") < texto.index("[INFO]")


def test_sincronizar_vigilando_detecta_y_descarga_igual(tmp_path: Path) -> None:
    """Un nombre no catalogado se reporta, pero el archivo NO se pierde."""
    dia = date(2026, 1, 15)
    url_zip = UP + "Antecedentes_CMG_Real_def_260115_final.zip"
    sesion = SesionFalsa(
        paginas={cen.url_dia(dia): _pagina_con(url_zip)},
        zips={url_zip: b"PK\x03\x04no-es-un-zip-de-verdad"},
    )
    nuevos, h = deriva.sincronizar_vigilando(dia, dia, tmp_path, sesion, pausa=1.0)

    assert [n["nombre"] for n in nuevos] == ["Antecedentes_CMG_Real_def_260115_final.zip"]
    assert (tmp_path / "Antecedentes_CMG_Real_def_260115_final.zip").exists()
    assert "nombre_no_catalogado" in tipos(h)
    assert "zip_corrupto" in tipos(h)  # y ademas se inspecciono el contenido


def test_vigilar_fuente_avisa_si_no_hay_publicaciones(tmp_path: Path) -> None:
    """Frescura: varios dias seguidos sin documentos es señal de que algo cambio."""
    sesion = SesionFalsa({cen.INDICE: html("indice_anios.html")})
    h = deriva.vigilar_fuente(sesion, tmp_path, hasta=date(2026, 6, 15), dias=2, pausa=1.0)
    assert "sin_publicaciones_recientes" in tipos(h)


def test_vigilar_fuente_no_descarga_zip(tmp_path: Path) -> None:
    dia = date(2026, 1, 15)
    sesion = SesionFalsa(
        {cen.INDICE: html("indice_anios.html"), cen.url_dia(dia): html("dia_2026-01-15.html")}
    )
    deriva.vigilar_fuente(sesion, tmp_path, hasta=dia, dias=1, pausa=1.0)
    assert not any(p.endswith(".zip") for p in sesion.pedidos)


def test_vigilar_fuente_revisa_la_completitud_de_todo_el_manifiesto(tmp_path: Path) -> None:
    """Un hueco antiguo (fuera de los `dias` revisados en la web) igual se detecta."""
    m = manifiesto(entrada(date(2026, 1, 10)), entrada(date(2026, 1, 15)))
    cen.guardar_manifiesto(tmp_path, m)
    dia = date(2026, 1, 15)
    sesion = SesionFalsa(
        {cen.INDICE: html("indice_anios.html"), cen.url_dia(dia): html("dia_2026-01-15.html")}
    )
    h = deriva.vigilar_fuente(sesion, tmp_path, hasta=dia, dias=1, pausa=1.0, hoy=HOY)
    sin_registro = [x for x in h if x["tipo"] == "dia_sin_registro"]
    assert [x["evidencia"] for x in sin_registro] == ["2026-01-11 a 2026-01-14"]


def test_sincronizar_vigilando_avisa_un_dia_que_el_cen_no_publico(tmp_path: Path) -> None:
    """La pagina existe pero no tiene documentos: queda para revision manual."""
    dia = date(2026, 1, 15)
    sesion = SesionFalsa({cen.url_dia(dia): "<html><body>sin documentos</body></html>"})
    nuevos, h = deriva.sincronizar_vigilando(dia, dia, tmp_path, sesion, pausa=1.0, hoy=HOY)
    assert nuevos == []
    assert "dia_sin_registro" in tipos(h)


def test_sincronizar_vigilando_informa_el_avance(tmp_path: Path) -> None:
    """Una linea por dia y una por ZIP: en un backfill la pantalla no queda muda."""
    dia = date(2026, 1, 15)
    url_zip = UP + "Antecedentes_CMG_Real_def_260115.zip"
    sesion = SesionFalsa(
        paginas={cen.url_dia(dia): _pagina_con(url_zip)},
        zips={url_zip: b"PK\x03\x04no-es-un-zip-de-verdad"},
    )
    lineas: list[str] = []
    deriva.sincronizar_vigilando(dia, dia, tmp_path, sesion, pausa=1.0, avisar=lineas.append)

    assert any("2026-01-15" in linea and "1 archivo" in linea for linea in lineas)
    assert any("+ Antecedentes_CMG_Real_def_260115.zip" in linea for linea in lineas)
