"""Explora la pagina de documentos de Costo Marginal Real del CEN.

Fuente alternativa a la API, elegida porque la API pierde la hora del dia DST
(ver CLAUDE.md 4.5.2 a 4.5.5). Es el canal oficial de distribucion del publicador,
no scraping de terceros.

Objetivo: entender la estructura antes de escribir el extractor.
  - responde 200 a un GET simple?
  - donde estan los enlaces a los archivos y que extension tienen?
  - el listado esta en el HTML o lo carga por JavaScript/XHR?

Uso:
    uv run python estudio/explorar_pagina_cmg.py
"""

import re
from collections import Counter

import httpx

URL = (
    "https://www.coordinador.cl/mercados/documentos/transferencias-economicas/"
    "costo-marginal-real/2026-costo-marginal-real/"
)
UA = "cmg-ingesta/0.1 (uso propio; contacto via portal del desarrollador)"


def main() -> None:
    r = httpx.get(
        URL,
        headers={"accept": "text/html,application/xhtml+xml", "user-agent": UA},
        timeout=60.0,
        follow_redirects=True,
    )
    print(f"status: {r.status_code}")
    print(f"content-type: {r.headers.get('content-type')}")
    print(f"tamano: {len(r.text):,} caracteres")
    if r.status_code != 200:
        print("\ncuerpo recortado:")
        print(r.text[:600])
        raise SystemExit(1)

    html = r.text

    # Enlaces a archivos descargables
    enlaces = re.findall(r'href=["\']([^"\']+)["\']', html)
    exts = Counter()
    archivos = []
    for h in enlaces:
        m = re.search(r"\.(zip|csv|xlsx|xls|pdf|tsv)(\?|$)", h, re.I)
        if m:
            exts[m.group(1).lower()] += 1
            archivos.append(h)

    print(f"\nenlaces totales: {len(enlaces)}")
    print(f"enlaces a archivos: {len(archivos)}  -> {dict(exts)}")
    print("\nprimeros 15 archivos:")
    for h in archivos[:15]:
        print(f"  {h}")

    # Senales de carga dinamica
    print("\nsenales de contenido dinamico:")
    for pat in ("wp-json", "admin-ajax", "api/", "fetch(", "XMLHttpRequest", "data-url"):
        n = html.count(pat)
        if n:
            print(f"  '{pat}': {n} menciones")

    # Menciones de meses/periodos, para ver si el listado esta en el HTML
    meses = re.findall(r"\b(20\d{2})[-_ ]?(0[1-9]|1[0-2])\b", html)
    print(f"\nperiodos AAAA-MM detectados en el HTML: {len(set(meses))}")
    if meses:
        print("  ejemplos:", sorted(set(meses))[:8])


if __name__ == "__main__":
    main()
