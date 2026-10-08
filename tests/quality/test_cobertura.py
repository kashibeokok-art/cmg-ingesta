"""Tests de la cobertura de la base (quality/cobertura.py).

El primer test es el caso REAL que reporto el usuario el 2026-10-08: Silver con
2021-2024 completos mas 3 dias de octubre 2026, y nada de lo que hay entre medio.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb

from cmg_ingesta.extract import coordinador_cmg as cen
from cmg_ingesta.quality import cobertura

Sembrar = Callable[..., None]
HOY = date(2026, 10, 8)


def dias(desde: str, hasta: str) -> set[date]:
    return set(cen.dias_entre(date.fromisoformat(desde), date.fromisoformat(hasta)))


def entrada(dia: date, tipo: str = "pre") -> cen.EntradaManifiesto:
    nombre = f"CMG_Real_{tipo}_{dia:%y%m%d}.zip"
    return {
        "url": "",
        "nombre": nombre,
        "tipo": tipo,
        "version": 1,
        "reemision": 0,
        "fecha_operacion": dia.isoformat(),
        "fecha_publicacion": None,
        "sha256": "",
        "bytes": 0,
        "descargado_en": "",
    }


def manifiesto(*dias_: date) -> dict[str, cen.EntradaManifiesto]:
    return {e["nombre"]: e for e in (entrada(d) for d in dias_)}


MAESTRO = dias("2021-01-01", "2024-12-31")
RECIENTES = dias("2026-10-04", "2026-10-06")


def test_el_caso_real_del_usuario() -> None:
    m = manifiesto(*RECIENTES)
    cob = cobertura.calcular(MAESTRO | RECIENTES, m, HOY)

    assert cob["hasta"] == date(2026, 10, 5)  # hoy - 3 dias de gracia
    assert cob["faltantes"]["maestro"] == []
    assert cob["faltantes"]["sin_ingerir"] == []
    falta = cob["faltantes"]["sin_descargar"]
    assert (falta[0], falta[-1], len(falta)) == (date(2025, 1, 1), date(2026, 10, 3), 641)
    assert cob["meses_vacios"][0] == (2025, 1)
    assert cob["meses_vacios"][-1] == (2026, 9)
    assert len(cob["meses_vacios"]) == 21
    # octubre: esperados del 1 al 5; con datos el 4 y el 5 (el 6 cae fuera del rango)
    assert cob["meses_incompletos"] == [{"mes": (2026, 10), "con_datos": 2, "esperados": 5}]


def test_agregar_dias_recientes_no_esconde_el_hueco() -> None:
    """REGRESION: lo esperado no puede depender de lo que ya hay."""
    antes = cobertura.calcular(MAESTRO, {}, HOY)
    despues = cobertura.calcular(MAESTRO | RECIENTES, manifiesto(*RECIENTES), HOY)

    hueco = dias("2025-01-01", "2026-10-03")
    assert hueco <= set(antes["faltantes"]["sin_descargar"])
    assert hueco <= set(despues["faltantes"]["sin_descargar"])


def test_base_completa_no_tiene_faltantes() -> None:
    cob = cobertura.calcular(dias("2021-01-01", "2026-10-05"), {}, HOY)
    assert cobertura.total_faltantes(cob) == 0
    assert cob["meses_vacios"] == [] and cob["meses_incompletos"] == []
    assert "ninguno" in cobertura.lineas_estado(cob)[-1]


def test_los_ultimos_dias_no_se_exigen_todavia() -> None:
    """Ayer y anteayer sin datos es normal: el CEN publica con desfase."""
    cob = cobertura.calcular(dias("2021-01-01", "2026-10-05"), {}, HOY)
    assert date(2026, 10, 7) not in cob["faltantes"]["sin_descargar"]
    assert date(2026, 10, 6) not in cob["faltantes"]["sin_descargar"]


def test_un_dia_que_falta_en_el_maestro() -> None:
    cob = cobertura.calcular(MAESTRO - {date(2023, 4, 1)}, {}, date(2025, 1, 4))
    assert cob["faltantes"]["maestro"] == [date(2023, 4, 1)]
    h = cobertura.revisar(cob)
    assert [(x["tipo"], x["evidencia"]) for x in h] == [("dias_sin_datos_maestro", "2023-04-01")]
    assert "migrar-historico" in h[0]["accion"]


def test_un_dia_descargado_que_no_entro_a_la_base() -> None:
    """Hay ZIP en el Bronze pero no esta en Silver: falta ingerir, o tuvo un critico."""
    dia = date(2025, 1, 1)
    cob = cobertura.calcular(MAESTRO, manifiesto(dia), date(2025, 1, 4))
    assert cob["faltantes"]["sin_ingerir"] == [dia]
    h = cobertura.revisar(cob)
    assert [x["tipo"] for x in h] == ["dias_descargados_sin_ingerir"]
    assert "ingerir-pagina" in h[0]["accion"]


def test_revisar_no_repite_lo_que_ya_avisa_el_bronze() -> None:
    """`sin_descargar` lo reporta deriva como dia_sin_registro: aqui seria un duplicado."""
    cob = cobertura.calcular(MAESTRO, {}, HOY)
    assert cobertura.total_faltantes(cob) > 0
    assert cobertura.revisar(cob) == []


def test_los_huecos_se_agrupan_en_tramos() -> None:
    presentes = MAESTRO - dias("2022-03-01", "2022-03-31") - {date(2023, 7, 9)}
    cob = cobertura.calcular(presentes, {}, date(2025, 1, 4))
    h = cobertura.revisar(cob)
    assert [x["evidencia"] for x in h] == ["2022-03-01 a 2022-03-31", "2023-07-09"]


def test_rangos_de_meses() -> None:
    meses = [(2025, m) for m in range(1, 13)] + [(2026, 1), (2026, 5)]
    assert cobertura.rangos_de_meses(meses) == [((2025, 1), (2026, 1)), ((2026, 5), (2026, 5))]


def test_lineas_estado_muestran_los_tramos_y_los_meses_vacios() -> None:
    cob = cobertura.calcular(MAESTRO | RECIENTES, manifiesto(*RECIENTES), HOY)
    texto = "\n".join(cobertura.lineas_estado(cob))
    assert "faltantes : 641 dia(s)" in texto
    assert "2025-01-01 a 2026-10-03" in texto and "sin descargar" in texto
    assert "meses sin ningun dato : 21  (2025-01 a 2026-09)" in texto
    assert "2026-10 (2/5 dias)" in texto


def test_dias_en_base(con: duckdb.DuckDBPyConnection, tmp_path: Path, sembrar: Sembrar) -> None:
    assert cobertura.dias_en_base(con, tmp_path / "no_existe") == set()
    sembrar(con, tmp_path, 2024, 6, "BARRA_1", dias=3)
    assert cobertura.dias_en_base(con, tmp_path) == dias("2024-06-01", "2024-06-03")
