"""Tests del catalogo editable de nombres (config/nombres_cen.toml).

El primer test es el que hay que correr despues de editar el archivo: verifica
que el catalogo del proyecto siga reconociendo los 1.765 nombres reales.
"""

import csv
from pathlib import Path

import pytest

from cmg_ingesta.extract import catalogo_nombres as cn

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_el_catalogo_del_proyecto_reconoce_los_1765_nombres_reales() -> None:
    """CORRER ESTE TEST despues de editar config/nombres_cen.toml."""
    cat = cn.cargar()
    with (FIXTURES / "nombres_reales.tsv").open(encoding="utf-8") as f:
        nombres = [fila["nombre"] for fila in csv.DictReader(f, delimiter="\t")]
    assert len(nombres) == 1765
    assert [n for n in nombres if cn.leer_nombre(n, cat) is None] == []


def test_lectura_de_un_nombre() -> None:
    lectura = cn.leer_nombre("Antecedentes_CMG_Real_pre_260811_v2-1.zip")
    assert lectura == {
        "tipo": "pre",
        "version": 2,
        "reemision": 1,
        "fecha": "260811",
        "plantilla": "{tipo}_{fecha}_v{version}-{reemision}",
    }


def test_dos_versiones_gana_la_mayor() -> None:
    lectura = cn.leer_nombre("Antecedentes_CMG_Real_def-v3_v2.zip")
    assert lectura is not None and lectura["version"] == 3 and lectura["fecha"] == ""


def test_alias_de_tipo() -> None:
    lectura = cn.leer_nombre("Antecedentes_CMG_Real_prel_v2_250612.zip")
    assert lectura is not None and lectura["tipo"] == "pre"


def test_un_nombre_sin_forma_devuelve_none() -> None:
    assert cn.leer_nombre("Antecedentes_CMG_Real_def_260115_final.zip") is None
    assert cn.leer_nombre("otra_cosa.zip") is None


def test_es_candidato_sin_distinguir_mayusculas() -> None:
    assert cn.es_candidato("https://x/uploads/Antecedentes_CMg_Real_def_260115.ZIP")
    assert not cn.es_candidato("https://x/uploads/CmgBarrasComparativo_20260508.zip")


# --------------------------------------------------- editar el archivo


BASE = """
prefijo = "Antecedentes_CMG_Real_"
extension = ".zip"
debe_contener = "cmg_real"
[tipos]
def = "def"
pre = "pre"
[[formas]]
plantilla = "{tipo}_{fecha}"
ejemplo = "Antecedentes_CMG_Real_def_260115.zip"
"""


def catalogo_desde(tmp_path: Path, texto: str) -> cn.Catalogo:
    ruta = tmp_path / "nombres.toml"
    ruta.write_text(texto, encoding="utf-8")
    return cn.cargar(ruta)


def test_agregar_una_forma_nueva_la_hace_reconocible(tmp_path: Path) -> None:
    """El caso de uso del archivo: el CEN inventa un nombre y se agrega sin tocar codigo."""
    nombre = "Antecedentes_CMG_Real_def_260115_final.zip"
    assert cn.leer_nombre(nombre, catalogo_desde(tmp_path, BASE)) is None

    con_forma_nueva = BASE + (
        '\n[[formas]]\nplantilla = "{tipo}_{fecha}_final"\n'
        f'ejemplo = "{nombre}"\nnota = "inventado para el test"\n'
    )
    lectura = cn.leer_nombre(nombre, catalogo_desde(tmp_path, con_forma_nueva))
    assert lectura is not None and lectura["tipo"] == "def" and lectura["fecha"] == "260115"


def test_agregar_un_alias_de_tipo(tmp_path: Path) -> None:
    texto = BASE.replace('pre = "pre"', 'pre = "pre"\nprelim = "pre"')
    lectura = cn.leer_nombre(
        "Antecedentes_CMG_Real_prelim_260115.zip", catalogo_desde(tmp_path, texto)
    )
    assert lectura is not None and lectura["tipo"] == "pre"


@pytest.mark.parametrize(
    ("cambio", "mensaje"),
    [
        (("{tipo}_{fecha}", "{tipo}_{dia}"), "marcador desconocido"),
        (("{tipo}_{fecha}", "{fecha}"), "no tiene {tipo}"),
        (("{tipo}_{fecha}", "{tipo}_{fecha}_{fecha}"), "aparece dos veces"),
        (("def_260115", "def_260115_v2"), "no calza con su plantilla"),
        (('def = "def"', 'def = "definitivo"'), "solo se permite"),
        (('prefijo = "Antecedentes_CMG_Real_"', 'prefijo = "sin cerrar'), "no es TOML"),
    ],
)
def test_un_error_en_el_archivo_se_explica(
    tmp_path: Path, cambio: tuple[str, str], mensaje: str
) -> None:
    """Un error de tipeo no puede dejar de reconocer nombres en silencio."""
    with pytest.raises(cn.ErrorCatalogo, match=mensaje):
        catalogo_desde(tmp_path, BASE.replace(*cambio))


def test_archivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(cn.ErrorCatalogo, match="no existe"):
        cn.cargar(tmp_path / "no_existe.toml")
