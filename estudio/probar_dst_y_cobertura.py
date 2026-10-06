"""Dos pruebas puntuales contra la API: cobertura de barras y el dia DST de abril.

Estudio E0, dimensiones 4 (DST) y 6 (cobertura). Script de un solo uso, no es codigo
del proyecto.

Son SOLO 2 llamadas, porque todavia no se conoce el rate limit (la API no devuelve
headers de limite).

Referencia local, medida en CMG_DB el 2026-10-05:
    dia DST 2025        = 2025-04-05, con hora=24 (100 cuartos en vez de 96)
    barras en 2025-04   = 1.554  ->  1.554 x 96 = 149.184 registros esperados en un dia normal

Uso:
    uv run python estudio/probar_dst_y_cobertura.py
"""

import httpx

from cmg_ingesta.config import Settings

URL = "https://sipub.api.coordinador.cl/costo-marginal-real/v4/findByDate"
HEADERS = {"accept": "application/json"}
BARRA = "A.BLANCAS_____013"
DIA_DST = "2025-04-05"
DIA_NORMAL = "2025-04-07"


def pedir(clave: str, **params: str | int) -> dict:
    todos: dict[str, str | int] = {**params, "user_key": clave}
    r = httpx.get(URL, params=todos, headers=HEADERS, timeout=120.0)
    r.raise_for_status()
    datos: dict = r.json()
    return datos


def main() -> None:
    config = Settings()
    if config.cen_api_key is None:
        raise SystemExit("Falta CMGI_CEN_API_KEY en .env")
    clave = config.cen_api_key.get_secret_value()

    # --- Prueba 1: cobertura. limit=1 -> totalPages == total de registros del dia ---
    print("=== 1. COBERTURA (dimension 6) ===")
    print(f"un dia normal ({DIA_NORMAL}), sin filtrar barra, limit=1")
    d = pedir(clave, startDate=DIA_NORMAL, endDate=DIA_NORMAL, page=0, limit=1)
    total = d.get("totalPages")
    print(
        f"  totalPages (= registros del dia): {total:,}"
        if isinstance(total, int)
        else f"  totalPages: {total!r}"
    )
    print(f"  limit que respeto el servidor: {d.get('limit')!r}")
    if isinstance(total, int):
        print(f"  registros / 96 cuartos = {total / 96:,.1f} barras implicadas")
        print("  referencia CMG_DB 2025-04: 1,554 barras")

    # --- Prueba 2: el dia DST ---
    print(f"\n=== 2. DIA DST (dimension 4): {DIA_DST}, barra {BARRA} ===")
    d = pedir(clave, startDate=DIA_DST, endDate=DIA_DST, bar_transf=BARRA, page=0, limit=200)
    registros = d.get("data") or []
    print(f"  registros devueltos: {len(registros)}   (dia normal = 96, DST = 100)")
    print(
        f"  totalPages: {d.get('totalPages')!r}  page: {d.get('page')!r}  limit: {d.get('limit')!r}"
    )

    horas = sorted({r["hra"] for r in registros})
    print(f"  valores de 'hra' presentes: {horas}")
    print("  -> si aparece 24, la API usa la misma convencion que CMG_DB")
    print("  -> si el maximo es 23, hay que ver como marca la hora repetida")

    print("\n  ultimos 8 registros (hra, min, fecha_minuto, cmg_usd_mwh_):")
    for r in registros[-8:]:
        print(f"    hra={r['hra']:>2} min={r['min']:>2}  {r['fecha_minuto']}  {r['cmg_usd_mwh_']}")

    sellos = [r["fecha_minuto"] for r in registros]
    repetidos = {s for s in sellos if sellos.count(s) > 1}
    print(f"\n  'fecha_minuto' repetidos: {len(repetidos)}")
    if repetidos:
        print("  OJO: el timestamp se repite -> no sirve como clave unica ese dia")
        print("  ejemplos:", sorted(repetidos)[:4])


if __name__ == "__main__":
    main()
