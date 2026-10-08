"""Tests de la escritura particionada atomica."""

import shutil
import time
from pathlib import Path

import duckdb
import pytest

from cmg_ingesta.silver import escribir


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def consulta_falsa(n: int, barra: str = "A.BLANCAS_____013", valor: float = 50.0) -> str:
    """Genera `n` filas con el esquema canonico, para no depender de datos reales."""
    return f"""
        SELECT
            '{barra}' AS barra,
            DATE '2025-03-01' AS fecha,
            CAST(i AS UTINYINT) AS hora,
            CAST(0 AS UTINYINT) AS minuto,
            'A' AS bloque,
            {valor} AS cmg_usd_mwh,
            false AS es_hora_extra,
            CAST(NULL AS TIMESTAMP) AS fecha_hora,
            'maestro_cmg_db' AS origen,
            CAST('2026-10-06 12:00:00' AS TIMESTAMP) AS ingerido_en
        FROM range({n}) AS t(i)
    """


def test_ruta_particion_es_formato_hive(tmp_path: Path) -> None:
    assert escribir.ruta_particion(tmp_path, 2025, 3) == tmp_path / "anio=2025" / "mes=3"


def test_escribe_y_cuenta(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    escritas = escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    assert escritas == 10
    assert escribir.particion_existe(tmp_path, 2025, 3)
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 10


def test_particion_inexistente(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    assert not escribir.particion_existe(tmp_path, 2025, 3)
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 0


def test_reescribir_reemplaza_no_acumula(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Idempotencia: escribir dos veces el mismo mes NO duplica."""
    escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 10


def test_reescribir_con_otros_datos_los_reemplaza(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    escribir.escribir_particion(con, consulta_falsa(10, valor=50.0), tmp_path, 2025, 3)
    escribir.escribir_particion(con, consulta_falsa(3, valor=99.0), tmp_path, 2025, 3)
    patron = str(escribir.ruta_particion(tmp_path, 2025, 3) / "*.parquet").replace("'", "''")
    filas = con.execute(
        f"SELECT count(*), max(cmg_usd_mwh) FROM read_parquet('{patron}')"
    ).fetchone()
    assert filas is not None
    assert filas[0] == 3
    assert filas[1] == pytest.approx(99.0)


def test_no_queda_carpeta_temporal_tras_el_exito(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    escribir.escribir_particion(con, consulta_falsa(5), tmp_path, 2025, 3)
    assert list(tmp_path.rglob(f"*{escribir.SUFIJO_TMP}")) == []


def test_un_fallo_deja_intacto_el_mes_anterior(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """ATOMICIDAD: si la escritura falla, lo que ya estaba sigue ahi."""
    escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)

    with pytest.raises(duckdb.Error):
        escribir.escribir_particion(con, "SELECT columna_que_no_existe", tmp_path, 2025, 3)

    # el mes viejo sigue completo
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 10


def test_limpiar_temporales_borra_las_huerfanas(tmp_path: Path) -> None:
    huerfana = escribir.ruta_particion(tmp_path, 2025, 3)
    huerfana = huerfana.with_name(huerfana.name + escribir.SUFIJO_TMP)
    huerfana.mkdir(parents=True)
    (huerfana / "data.parquet").write_bytes(b"basura")

    assert escribir.limpiar_temporales(tmp_path) == 1
    assert not huerfana.exists()


def test_limpiar_temporales_no_toca_las_buenas(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    escribir.escribir_particion(con, consulta_falsa(5), tmp_path, 2025, 3)
    assert escribir.limpiar_temporales(tmp_path) == 0
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 5


def test_meses_escritos(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    for anio, mes in [(2025, 3), (2025, 1), (2024, 12)]:
        escribir.escribir_particion(con, consulta_falsa(2), tmp_path, anio, mes)
    assert escribir.meses_escritos(tmp_path) == [(2024, 12), (2025, 1), (2025, 3)]


def test_meses_escritos_ignora_carpetas_vacias(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    escribir.escribir_particion(con, consulta_falsa(2), tmp_path, 2025, 3)
    vacia = escribir.ruta_particion(tmp_path, 2025, 4)
    vacia.mkdir(parents=True)
    assert escribir.meses_escritos(tmp_path) == [(2025, 3)]


def test_meses_escritos_en_base_inexistente(tmp_path: Path) -> None:
    assert escribir.meses_escritos(tmp_path / "no_existe") == []


def test_ruta_con_apostrofo_no_rompe_el_sql(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Una comilla simple en la ruta tiene que escaparse, no romper la consulta."""
    raro = tmp_path / "carpeta'con'comillas"
    raro.mkdir()
    try:
        escritas = escribir.escribir_particion(con, consulta_falsa(4), raro, 2025, 3)
        assert escritas == 4
        assert escribir.filas_en_particion(con, raro, 2025, 3) == 4
    finally:
        shutil.rmtree(raro, ignore_errors=True)


# ------------------------------------------- fallos al RENOMBRAR (errores B9)


@pytest.fixture
def renombre_que_falla(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Hace fallar `Path.replace` cuando la temporal pasa a ser el mes.

    `veces` = cuantas veces falla antes de funcionar (99 = siempre). Es lo que
    hacia el antivirus el 2026-10-08 al re-migrar 2021-10.
    """
    estado = {"veces": 99, "intentos": 0}
    original = Path.replace

    def replace(self: Path, destino: str | Path) -> Path:
        if self.name.endswith(escribir.SUFIJO_TMP):
            estado["intentos"] += 1
            if estado["intentos"] <= estado["veces"]:
                raise PermissionError(5, "Acceso denegado")
        return original(self, destino)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    return estado


def test_si_el_renombre_falla_el_mes_viejo_vuelve_a_su_lugar(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, renombre_que_falla: dict[str, int]
) -> None:
    renombre_que_falla["veces"] = 0
    escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    renombre_que_falla.update(veces=99, intentos=0)

    with pytest.raises(PermissionError):
        escribir.escribir_particion(con, consulta_falsa(20), tmp_path, 2025, 3)

    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 10
    viejo = escribir.ruta_particion(tmp_path, 2025, 3).with_name("mes=3" + escribir.SUFIJO_VIEJO)
    assert not viejo.exists()


def test_un_bloqueo_pasajero_se_reintenta(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, renombre_que_falla: dict[str, int]
) -> None:
    renombre_que_falla["veces"] = 2
    assert escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3) == 10
    assert renombre_que_falla["intentos"] == 3
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 10


def test_no_queda_el_mes_viejo_apartado_tras_el_exito(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    escribir.escribir_particion(con, consulta_falsa(20), tmp_path, 2025, 3)
    assert list(tmp_path.rglob(f"*{escribir.SUFIJO_VIEJO}")) == []
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 20


def test_limpiar_restaura_un_mes_viejo_si_el_mes_falta(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """El proceso murio justo despues de apartar el mes viejo."""
    escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    mes = escribir.ruta_particion(tmp_path, 2025, 3)
    mes.replace(mes.with_name(mes.name + escribir.SUFIJO_VIEJO))

    assert escribir.limpiar_temporales(tmp_path) == 1
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 10


def test_limpiar_borra_un_mes_viejo_que_sobra(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """El proceso murio despues de poner el mes nuevo, antes de borrar el viejo."""
    escribir.escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    mes = escribir.ruta_particion(tmp_path, 2025, 3)
    shutil.copytree(mes, mes.with_name(mes.name + escribir.SUFIJO_VIEJO))

    assert escribir.limpiar_temporales(tmp_path) == 1
    assert list(tmp_path.rglob(f"*{escribir.SUFIJO_VIEJO}")) == []
    assert escribir.filas_en_particion(con, tmp_path, 2025, 3) == 10
