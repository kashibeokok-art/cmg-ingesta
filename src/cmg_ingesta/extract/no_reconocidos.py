"""Registro de enlaces a ZIP que no calzan con ninguna forma conocida.

Cada vez que el programa lee una pagina de dia, anota aqui todo ZIP que:

- es de CMg Real (contiene `debe_contener`) pero su nombre no calza con ninguna
  plantilla de config/nombres_cen.toml  -> motivo "forma_desconocida"; o
- es otro producto (no contiene `debe_contener`)  -> motivo "otro_producto".

Se guarda junto al manifiesto como CSV que Excel abre directo (separador `;` y
UTF-8 con BOM, para que las tildes se vean bien):

    data/bronze/cen_cmg/nombres_no_reconocidos.csv

Una fila por URL. Si el mismo enlace aparece de nuevo, no se duplica: se
actualiza `ultima_vez` y se suma 1 a `veces`. Asi el archivo es la lista de
trabajo para mantener el catalogo: cuando se agrega la forma al TOML, ese enlace
deja de aparecer en corridas nuevas (la fila vieja queda como historial).
"""

import csv
import io
from datetime import date
from pathlib import Path
from typing import TypedDict

from bs4 import BeautifulSoup

from cmg_ingesta.extract import catalogo_nombres
from cmg_ingesta.extract import coordinador_cmg as cen

ARCHIVO = "nombres_no_reconocidos.csv"
COLUMNAS = [
    "url",
    "nombre",
    "dia_pagina",
    "motivo",
    "etiqueta",
    "texto_encontrado",
    "primera_vez",
    "ultima_vez",
    "veces",
]


class Enlace(TypedDict):
    url: str
    nombre: str
    dia_pagina: str
    motivo: str  # "forma_desconocida" | "otro_producto"
    etiqueta: str  # el titulo del documento en la pagina
    texto_encontrado: str  # el texto del bloque del documento, tal cual


def detectar(
    html: str, dia: date, catalogo: catalogo_nombres.Catalogo | None = None
) -> list[Enlace]:
    """Los ZIP de la pagina que el catalogo no reconoce. Funcion pura."""
    cat = catalogo or catalogo_nombres.vigente()
    soup = BeautifulSoup(html, "html.parser")
    salida: list[Enlace] = []
    vistos: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "/wp-content/uploads/" not in href or not href.lower().endswith(".zip"):
            continue
        if href in vistos:
            continue
        vistos.add(href)
        nombre = href.rsplit("/", 1)[-1]
        if not catalogo_nombres.es_candidato(href, cat):
            motivo = "otro_producto"
        elif catalogo_nombres.leer_nombre(nombre, cat) is None:
            motivo = "forma_desconocida"
        else:
            continue
        bloque = a.find_parent("div") or a.parent
        texto = " ".join(bloque.get_text(" ", strip=True).split()) if bloque else ""
        etiqueta = ""
        if bloque is not None:
            span = bloque.find("span", class_="informes-estudio-Titulo")
            if span is not None:
                etiqueta = str(span.get("title") or span.get_text(strip=True))
        salida.append(
            {
                "url": href if href.startswith("http") else cen.BASE + href,
                "nombre": nombre,
                "dia_pagina": dia.isoformat(),
                "motivo": motivo,
                "etiqueta": etiqueta,
                "texto_encontrado": texto[:300],
            }
        )
    return salida


def leer(carpeta: Path) -> dict[str, dict[str, str]]:
    """El registro actual, por URL. Vacio si no existe."""
    ruta = carpeta / ARCHIVO
    if not ruta.exists():
        return {}
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        return {fila["url"]: dict(fila) for fila in csv.DictReader(f, delimiter=";")}


def registrar(carpeta: Path, enlaces: list[Enlace], ahora: str) -> int:
    """Suma los enlaces al registro. Devuelve cuantos son NUEVOS (URL no vista antes).

    `ahora` se inyecta (texto ISO) para que los tests no dependan del reloj.
    Escritura atomica: temporal + reemplazo, igual que el manifiesto.
    """
    if not enlaces:
        return 0
    registro = leer(carpeta)
    nuevos = 0
    for e in enlaces:
        fila = registro.get(e["url"])
        if fila is None:
            nuevos += 1
            fila_nueva = {k: str(v) for k, v in e.items()}
            fila_nueva.update(primera_vez=ahora, ultima_vez=ahora, veces="1")
            registro[e["url"]] = fila_nueva
        else:
            fila.update(ultima_vez=ahora, veces=str(int(fila.get("veces") or 0) + 1))

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNAS, delimiter=";", extrasaction="ignore")
    w.writeheader()
    for fila in sorted(registro.values(), key=lambda f: (f["dia_pagina"], f["nombre"])):
        w.writerow(fila)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / ARCHIVO
    tmp = ruta.with_suffix(".csv.tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    cen.reemplazar_atomico(tmp, ruta)
    return nuevos
