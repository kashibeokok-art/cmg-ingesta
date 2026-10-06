"""El dia de 23 horas de septiembre: que devuelve la API?

Caso limite simetrico al de abril. El reloj salta de 00:00 a 01:00, asi que el domingo
no tiene hora 0. En CMG_DB ese dia aparece con 96 cuartos y la hora 0 en CERO
(100% de las barras desde 2022). Falta saber si el cero lo pone el CEN o la ingesta.

Preguntas:
  - devuelve 92 (sin la hora fantasma), 96 con ceros, o 96 con valores?
  - que dice fecha_minuto en la hora 0?

Una sola llamada.
"""

import json
from pathlib import Path

import httpx

from cmg_ingesta.config import Settings

URL = "https://sipub.api.coordinador.cl/costo-marginal-real/v4/findByDate"
HEADERS = {"accept": "application/json"}
BARRA = "A.BLANCAS_____013"
DIA = "2025-09-07"  # domingo de 23 horas (calculado con America/Santiago)
SALIDA = Path("estudio/salida")


def main() -> None:
    config = Settings()
    if config.cen_api_key is None:
        raise SystemExit("Falta CMGI_CEN_API_KEY en .env")

    cache = SALIDA / f"api_{BARRA}_{DIA}.json"
    if cache.exists():
        regs = json.loads(cache.read_text(encoding="utf-8"))
        print(f"(reutilizando {cache})")
    else:
        r = httpx.get(
            URL,
            params={
                "startDate": DIA,
                "endDate": DIA,
                "bar_transf": BARRA,
                "page": 0,
                "limit": 200,
                "user_key": config.cen_api_key.get_secret_value(),
            },
            headers=HEADERS,
            timeout=120.0,
        )
        r.raise_for_status()
        regs = list(r.json().get("data") or [])
        SALIDA.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(regs, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\n{DIA}  barra {BARRA}")
    print(f"  registros: {len(regs)}   (dia normal=96, dia de 23 horas real=92)")
    horas = sorted({r["hra"] for r in regs})
    print(f"  horas presentes: {horas}")
    print(f"  tiene hora 0?  {'SI' if 0 in horas else 'NO'}")

    ceros = [r for r in regs if r["cmg_usd_mwh_"] == 0]
    print(f"  intervalos en CERO: {len(ceros)}")
    if ceros:
        print("    horas con cero:", sorted({r["hra"] for r in ceros}))

    print("\n  horas 0 a 2 en detalle:")
    print(f"    {'hra':>3} {'min':>4} {'fecha_minuto':<18} {'usd_mwh':>11}  version")
    for r in regs:
        if r["hra"] <= 2:
            print(
                f"    {r['hra']:>3} {r['min']:>4} {r['fecha_minuto']:<18} "
                f"{r['cmg_usd_mwh_']:>11.5f}  {r['version']}"
            )


if __name__ == "__main__":
    main()
