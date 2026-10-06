"""El dia DST, el patron de la API es el mismo en varias barras?

Prueba la hipotesis de SOBRESCRITURA POR COLISION DE CLAVE: si la API guarda por
(barra, fecha, hora_de_reloj, min), las dos 23:00 del dia DST colisionan y la segunda
gana. Eso predice, para TODA barra:
    horas 0..21  -> identicas al ZIP
    hora 23      -> igual a la hora 24 del ZIP (la segunda 23:00)
    hora 24      -> ausente
Lo que NO predice es que la hora 22 difiera. Si difiere en todas, es parte del mismo
mecanismo; si solo pasa en una barra, es otra cosa.

Una llamada por barra. Las respuestas se cachean en estudio/salida/.
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
DIA = "2025-04-05"
BARRAS = ["A.BLANCAS_____013", "STA.ELVIRA____013", "LATORRE_______066", "QUELLON_______013"]
ZIP_PATRON = r"C:\Users\claudio.araya\Desktop\Scripts\Retiros\Old\2504\**\cmg2504*15minutal.zip"
SALIDA = Path("estudio/salida")
TOL = 1e-4


def traer_api(clave: str, barra: str) -> list[dict]:
    cache = SALIDA / f"api_{barra}_{DIA}.json"
    if cache.exists():
        return list(json.loads(cache.read_text(encoding="utf-8")))
    r = httpx.get(
        URL,
        params={
            "startDate": DIA,
            "endDate": DIA,
            "bar_transf": barra,
            "page": 0,
            "limit": 200,
            "user_key": clave,
        },
        headers=HEADERS,
        timeout=120.0,
    )
    r.raise_for_status()
    regs = list(r.json().get("data") or [])
    SALIDA.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(regs, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"  {barra}: {len(regs)} registros (nueva llamada)")
    return regs


def traer_zip(barras: set[str]) -> dict[str, dict[tuple[int, int], float]]:
    ruta = glob.glob(ZIP_PATRON, recursive=True)[0]
    fuera: dict[str, dict[tuple[int, int], float]] = {b: {} for b in barras}
    with zipfile.ZipFile(ruta) as z:
        with z.open(z.namelist()[0]) as fh:
            texto = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
            cab = texto.readline().rstrip("\n").split(";")
            ix = {c: i for i, c in enumerate(cab)}
            for linea in texto:
                c = linea.rstrip("\n").split(";")
                if len(c) < len(cab):
                    continue
                b = c[ix["BARRA"]]
                if b not in fuera or c[ix["FECHA"]] != DIA.replace("-", ""):
                    continue
                fuera[b][(int(c[ix["HORA"]]) - 1, int(c[ix["MINUTO"]]))] = float(
                    c[ix["CMg[USD/MWh]"]]
                )
    return fuera


def main() -> None:
    config = Settings()
    if config.cen_api_key is None:
        raise SystemExit("Falta CMGI_CEN_API_KEY en .env")
    clave = config.cen_api_key.get_secret_value()

    print(f"=== API, {DIA}, {len(BARRAS)} barras ===")
    api = {b: traer_api(clave, b) for b in BARRAS}

    print("\n=== ZIP (una pasada) ===")
    zipd = traer_zip(set(BARRAS))

    print(
        f"\n{'barra':<20} {'API':>4} {'ZIP':>4} {'0-21':>6} "
        f"{'h22==ZIP22':>11} {'h23==ZIP24':>11} {'h24':>6}"
    )
    print("-" * 70)
    for b in BARRAS:
        regs = api[b]
        z = zipd.get(b, {})
        if not regs or not z:
            print(f"{b:<20} {len(regs):>4} {len(z):>4}   (sin datos en una fuente)")
            continue
        m = {(r["hra"], r["min"]): r["cmg_usd_mwh_"] for r in regs}

        ok_0_21 = sum(
            1
            for h in range(22)
            for mi in (0, 15, 30, 45)
            if (h, mi) in m and (h, mi) in z and abs(m[(h, mi)] - z[(h, mi)]) <= TOL
        )
        h22 = all(
            (22, mi) in m and (22, mi) in z and abs(m[(22, mi)] - z[(22, mi)]) <= TOL
            for mi in (0, 15, 30, 45)
        )
        h23 = all(
            (23, mi) in m and (24, mi) in z and abs(m[(23, mi)] - z[(24, mi)]) <= TOL
            for mi in (0, 15, 30, 45)
        )
        tiene24 = any((24, mi) in m for mi in (0, 15, 30, 45))
        print(
            f"{b:<20} {len(regs):>4} {len(z):>4} {ok_0_21:>4}/88 "
            f"{'SI' if h22 else 'NO':>11} {'SI' if h23 else 'NO':>11} "
            f"{'si' if tiene24 else 'no':>6}"
        )

    print("\nLectura:")
    print("  h22==ZIP22 = NO en todas  -> la hora 22 es parte del mismo mecanismo")
    print("  h22==ZIP22 = SI en otras  -> A.BLANCAS es un caso aparte")
    print("  h23==ZIP24 = SI en todas  -> confirma la sobrescritura de la hora repetida")


if __name__ == "__main__":
    main()
