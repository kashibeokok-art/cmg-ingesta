"""Compara un dia COMPLETO entre la API y el ZIP, intervalo por intervalo.

Estudio E0, cierre de las dimensiones 4 (DST) y 8 (valores). Dos dias:
  - 2025-04-07 (normal): confirmar paridad total en los 96 intervalos
  - 2025-04-05 (DST):    caracterizar donde y como se desalinea la API

Son SOLO 2 llamadas. Las respuestas se guardan en estudio/salida/ para poder
re-analizarlas sin volver a pedirlas.

Uso:
    uv run python estudio/comparar_dia_completo.py
"""

import glob
import io
import json
import zipfile
from pathlib import Path

import httpx

from cmg_ingesta.config import Settings

URL = "https://sipub.api.coordinador.cl/costo-marginal-real/v4/findByDate"
HEADERS = {"accept": "application/json"}
BARRA = "A.BLANCAS_____013"
DIA_NORMAL = "2025-04-07"
DIA_DST = "2025-04-05"
ZIP_PATRON = (
    r"C:\Users\claudio.araya\Desktop\Scripts\Retiros\Old\2504"
    r"\**\cmg2504*15minutal.zip"
)
SALIDA = Path("estudio/salida")
TOL = 1e-4


def traer_api(clave: str, dia: str) -> list[dict]:
    """Una llamada. Guarda el JSON crudo y devuelve los registros."""
    cache = SALIDA / f"api_{BARRA}_{dia}.json"
    if cache.exists():
        print(f"  (reutilizando {cache})")
        return list(json.loads(cache.read_text(encoding="utf-8")))
    r = httpx.get(
        URL,
        params={
            "startDate": dia,
            "endDate": dia,
            "bar_transf": BARRA,
            "page": 0,
            "limit": 200,
            "user_key": clave,
        },
        headers=HEADERS,
        timeout=120.0,
    )
    r.raise_for_status()
    cuerpo = r.json()
    registros = list(cuerpo.get("data") or [])
    SALIDA.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(registros, indent=1, ensure_ascii=False), encoding="utf-8")
    print(
        f"  guardado en {cache}  ({len(registros)} registros, "
        f"totalPages={cuerpo.get('totalPages')})"
    )
    return registros


def traer_zip(dias: set[str]) -> dict[str, dict[tuple[int, int], float]]:
    """Lee el ZIP una sola vez para todos los dias pedidos. HORA es 1-based."""
    ruta = glob.glob(ZIP_PATRON, recursive=True)
    if not ruta:
        raise SystemExit("no encontre el zip de abril")
    fuera: dict[str, dict[tuple[int, int], float]] = {d: {} for d in dias}
    claves = {d.replace("-", "") for d in dias}
    with zipfile.ZipFile(ruta[0]) as z:
        nombre = z.namelist()[0]
        with z.open(nombre) as fh:
            texto = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
            cab = texto.readline().rstrip("\n").split(";")
            ix = {c: i for i, c in enumerate(cab)}
            for linea in texto:
                c = linea.rstrip("\n").split(";")
                if len(c) < len(cab) or c[ix["BARRA"]] != BARRA:
                    continue
                f = c[ix["FECHA"]]
                if f not in claves:
                    continue
                dia = f"{f[:4]}-{f[4:6]}-{f[6:]}"
                hora = int(c[ix["HORA"]]) - 1  # 1-based -> 0-based
                fuera[dia][(hora, int(c[ix["MINUTO"]]))] = float(c[ix["CMg[USD/MWh]"]])
    return fuera


def comparar(dia: str, api: list[dict], zipd: dict[tuple[int, int], float]) -> None:
    print(f"\n{'=' * 78}\n{dia}   API: {len(api)} registros | ZIP: {len(zipd)} intervalos")
    print("=" * 78)

    mapa_api = {(r["hra"], r["min"]): r["cmg_usd_mwh_"] for r in api}

    iguales = distintos = solo_zip = 0
    desalineados: list[tuple[int, int, int]] = []
    for (h, m), v_zip in sorted(zipd.items()):
        v_api = mapa_api.get((h, m))
        if v_api is None:
            solo_zip += 1
            continue
        if abs(v_api - v_zip) <= TOL:
            iguales += 1
        else:
            distintos += 1
            # el valor de la API en esta hora, a que hora del ZIP corresponde?
            for d in (1, -1, 2, -2):
                otro = zipd.get((h + d, m))
                if otro is not None and abs(v_api - otro) <= TOL:
                    desalineados.append((h, m, d))
                    break

    print(f"  iguales (tol {TOL}): {iguales}")
    print(f"  distintos          : {distintos}")
    print(f"  solo en el ZIP     : {solo_zip}")

    if desalineados:
        print("\n  DESALINEACION: el valor que la API pone en la hora h es el del ZIP en h+d")
        por_d: dict[int, list[int]] = {}
        for h, _m, d in desalineados:
            por_d.setdefault(d, []).append(h)
        for d, horas in sorted(por_d.items()):
            hs = sorted(set(horas))
            print(f"    d={d:+d}  ->  horas {hs[0]}..{hs[-1]}  ({len(horas)} intervalos)")

    faltan = sorted(set(zipd) - set(mapa_api))
    if faltan:
        hs = sorted({h for h, _ in faltan})
        print(f"\n  intervalos del ZIP que la API NO trae: {len(faltan)}  (horas {hs})")

    sobran = sorted(set(mapa_api) - set(zipd))
    if sobran:
        print(f"  intervalos que la API trae y el ZIP no: {len(sobran)} -> {sobran[:6]}")


def main() -> None:
    config = Settings()
    if config.cen_api_key is None:
        raise SystemExit("Falta CMGI_CEN_API_KEY en .env")
    clave = config.cen_api_key.get_secret_value()

    print("=== trayendo de la API (2 llamadas como maximo) ===")
    api = {d: traer_api(clave, d) for d in (DIA_NORMAL, DIA_DST)}

    print("\n=== leyendo el ZIP (una pasada) ===")
    zipd = traer_zip({DIA_NORMAL, DIA_DST})
    for d, v in zipd.items():
        print(f"  {d}: {len(v)} intervalos")

    for d in (DIA_NORMAL, DIA_DST):
        comparar(d, api[d], zipd[d])


if __name__ == "__main__":
    main()
