"""Fixtures compartidas por todos los tests.

pytest carga `conftest.py` solo y le pasa estas fixtures a cualquier test que las
pida por nombre. Asi se comparten ayudas entre modulos de test **sin imports** ni
tocar `sys.path`, que es el problema que aparece con el layout `src/`.

`sembrar` es una *fixture de fabrica* (factory fixture): no devuelve un dato,
devuelve una FUNCION que el test llama con los argumentos que quiera.
"""

import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import pytest

from cmg_ingesta.config import Settings
from cmg_ingesta.domain import bloques
from cmg_ingesta.quality import deriva
from cmg_ingesta.silver import escribir

#: Valores constantes por bloque. Con constantes, los promedios esperados se
#: calculan a mano y cualquier error de ponderacion salta de inmediato.
VALORES_BLOQUE = {"A": 100.0, "B": 10.0, "C": 200.0}

Sembrar = Callable[..., None]


@pytest.fixture(autouse=True)
def sin_env_del_proyecto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ningun test lee el `.env` real del proyecto (tiene la ruta de datos real).

    `autouse=True` la aplica a TODOS los tests sin que la pidan. Antes bastaba con
    cambiar de carpeta, porque el `.env` se buscaba en la carpeta actual; ahora se
    busca en la del proyecto, asi que hay que apagarlo explicitamente.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """Una conexion DuckDB en memoria, nueva para cada test."""
    return duckdb.connect()


@pytest.fixture
def valores() -> dict[str, float]:
    return dict(VALORES_BLOQUE)


@pytest.fixture
def sembrar() -> Sembrar:
    """Devuelve una funcion que escribe un mes sintetico en una base Silver."""

    def _sembrar(
        con: duckdb.DuckDBPyConnection,
        base: Path,
        anio: int,
        mes: int,
        barra: str,
        valor_por_bloque: dict[str, float] | None = None,
        dias: int = 1,
        horas: list[int] | None = None,
        otras: dict[str, dict[str, float]] | None = None,
    ) -> None:
        """`otras` agrega mas barras AL MISMO MES, cada una con sus valores.

        Hace falta porque escribir un mes reemplaza la particion entera: sembrar
        dos veces el mismo mes deja solo la ultima barra.
        """
        por_barra = {barra: valor_por_bloque or dict(VALORES_BLOQUE), **(otras or {})}
        rango = horas if horas is not None else list(range(24))
        bloque_sql = " ".join(f"WHEN {h} THEN '{bloques.bloque_de_hora(h)}'" for h in rango)
        lista = ", ".join(str(h) for h in rango)

        def _select(nombre: str, valores: dict[str, float]) -> str:
            casos = " ".join(f"WHEN {h} THEN {valores[bloques.bloque_de_hora(h)]}" for h in rango)
            # el nombre va escapado: hay barras con apostrofo
            barra_sql = "'" + nombre.replace("'", "''") + "'"
            return f"""
                SELECT {barra_sql} AS barra,
                       make_date({anio}, {mes}, d) AS fecha,
                       CAST(h AS UTINYINT) AS hora,
                       CAST(m AS UTINYINT) AS minuto,
                       CAST(CASE h {bloque_sql} END AS VARCHAR) AS bloque,
                       CAST(CASE h {casos} END AS DOUBLE) AS cmg_usd_mwh,
                       (h = 24) AS es_hora_extra,
                       CAST(NULL AS TIMESTAMP) AS fecha_hora,
                       'maestro_cmg_db' AS origen,
                       CAST('2026-10-06' AS TIMESTAMP) AS ingerido_en
                FROM (SELECT unnest([{lista}]) AS h) a,
                     (SELECT unnest([0, 15, 30, 45]) AS m) b,
                     (SELECT unnest(range(1, {dias + 1})) AS d) c
            """

        consulta = " UNION ALL ".join(_select(n, v) for n, v in por_barra.items())
        escribir.escribir_particion(con, consulta, base, anio, mes)

    return _sembrar


ArmarZipCen = Callable[..., Path]


@pytest.fixture
def zip_cen() -> ArmarZipCen:
    """Devuelve una funcion que arma un ZIP con la forma real del Coordinador.

    Los miembros y el encabezado son los del catalogo de `quality/deriva.py`, que
    se tomaron de ZIP reales. Los valores son sinteticos: `pre` y `def` distintos
    para poder verificar que la ingesta toma la columna correcta.
    """

    def _armar(
        ruta: Path,
        dia: date,
        horas: list[int] | None = None,
        barras: tuple[str, ...] = ("A.BLANCAS_____013", "STA.ELVIRA____013"),
        pre: float = 40.0,
        definitivo: float = 50.0,
        formato: str = "2025",
        carpeta: str = "",
        v2: tuple[float, float] | None = None,
        v2_al_reves: bool = False,
        sufijo: str = "",
    ) -> Path:
        """Variantes reales del Coordinador:

        - `formato="2024"`: CMgE/CMgP/CMgR en mills/kWh y PromediosBarras sin `_`.
        - `carpeta`: todo dentro de una carpeta del ZIP (109 ZIP de 2025-02..2026-09).
        - `v2=(pre, def)`: revision con columnas `... v2` ademas de las normales, que
          quedan con `pre`/`definitivo` (el v1). Con `v2_al_reves`, el orden de
          `pre-v2_250228`: `PRE v2, DEF v2, PRE, DEF`, donde lo revisado va al final.
        - `sufijo="_NUEVO"`: `CmgBarrasComparativo_..._15_NUEVO.csv`.
        """
        f = dia.strftime("%Y%m%d")
        rango = horas if horas is not None else list(range(24))
        viejo = formato == "2024"
        cab = list(deriva.ENCABEZADO_COMPARATIVO_2024 if viejo else deriva.ENCABEZADO_COMPARATIVO)
        valores = [str(pre), str(definitivo)]
        if v2 is not None:
            nuevas = ["CMG_REAL_PRE v2[USD/MWh]", "CMG_REAL_DEF v2[USD/MWh]"]
            if v2_al_reves:  # las "v2" primero, con lo VIEJO; las normales al final, con lo nuevo
                cab[7:9] = nuevas + cab[7:9]
                valores = valores + [str(v2[0]), str(v2[1])]
            else:
                cab[9:9] = nuevas
                valores = valores + [str(v2[0]), str(v2[1])]
        lineas = [";".join(cab)]
        for barra in barras:
            for h in rango:
                for m in (0, 15, 30, 45):
                    lineas.append(f"{f};{h};{m};{barra};141;X;1.0;{';'.join(valores)};915.95;NO")
        miembros = {
            f"CmgBarrasComparativo_{f}_{f}_15{sufijo}.csv": "\n".join(lineas),
            f"PromediosBarras{'' if viejo else '_'}{f}_{f}_R.csv": ";".join(
                deriva.ENCABEZADO_PROMEDIOS_2024 if viejo else deriva.ENCABEZADO_PROMEDIOS
            ),
            f"CMgBarrasMinuto_{f}_{f}.zip": "",
            (f"Fpen_Reales_{dia:%y%m%d}_MOD_def.xlsx" if viejo else f"Fpen_{f}.xlsx"): "",
            "FPen_Mapeo_Barras.xlsx": "",
            f"Barras_Subsistemas_{f}.xlsx": "",
        }
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(ruta, "w") as zf:
            if carpeta:
                zf.writestr(carpeta + "/", "")  # la entrada de la carpeta, como los reales
            for nombre, contenido in miembros.items():
                zf.writestr(f"{carpeta}/{nombre}" if carpeta else nombre, contenido)
        return ruta

    return _armar
