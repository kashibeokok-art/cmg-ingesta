"""Llamada exploratoria a la API del CEN: descubrir el esquema real de la respuesta.

Esto NO es codigo del proyecto. Es un script de estudio de un solo uso, para responder
las preguntas 1 a 6 de E0.3b paso 0 antes de escribir el cliente de verdad.

Uso:
    uv run python estudio/explorar_api.py

La clave se lee de .env via Settings. Nunca se escribe aqui.
"""

import json

import httpx

from cmg_ingesta.config import Settings

# TODO: la URL del endpoint, sacada de la documentacion del portal.
# NO inventarla: si no calza, la normalizacion de E0.4 trabaja sobre campos que no existen.
URL = "https://sipub.api.coordinador.cl/costo-marginal-real/v4/findByDate"

# Un solo dia: todavia no sabemos el rate limit (esa es justamente la pregunta 5).
PARAMS: dict[str, str] = {
    "startDate": "2025-01-01",
    "endDate": "2025-01-01",
    "bar_transf": "STA.ELVIRA____013",
    # TODO: agregar los demas parametros que acepte el endpoint (bar_transf, page, limit...)
}


def main() -> None:
    if not URL:
        raise SystemExit("Falta completar URL con el endpoint del portal.")

    config = Settings()
    if config.cen_api_key is None:
        raise SystemExit("Falta CMGI_CEN_API_KEY en .env")

    params = dict(PARAMS)
    params["user_key"] = config.cen_api_key.get_secret_value()

    respuesta = httpx.get(URL, params=params, timeout=60.0, headers={"accept": "application/json"})

    print("=== 1. peticion ===")
    print("status:", respuesta.status_code)
    # str(respuesta.url) incluiria la clave: se imprime solo la ruta.
    print("endpoint:", respuesta.url.path)

    print("\n=== 5. rate limit ===")
    limites = {k: v for k, v in respuesta.headers.items() if "rate" in k.lower()}
    print(limites or "(no vienen headers de rate limit)")

    if respuesta.status_code != 200:
        print("\nla peticion fallo; cuerpo recortado:")
        print(respuesta.text[:500])
        raise SystemExit(1)

    datos = respuesta.json()

    print("\n=== 2 y 4. forma de la respuesta ===")
    print("tipo raiz:", type(datos).__name__)
    if isinstance(datos, dict):
        print("claves raiz:", list(datos))
        # TODO: si hay una clave que contiene la lista de registros, apuntar a ella
        registros = None
    else:
        print("(la raiz es una lista)")
        registros = datos

    print("\n=== 3. campos de un registro ===")
    if registros:
        print("cantidad de registros:", len(registros))
        print("campos:", list(registros[0]))
        print("\nprimer registro completo:")
        print(json.dumps(registros[0], indent=2, ensure_ascii=False))
    else:
        print("no se identifico la lista de registros; respuesta recortada:")
        print(json.dumps(datos, indent=2, ensure_ascii=False)[:1200])

    print("\n=== 6. granularidad ===")
    print("Revisa el campo de tiempo del primer registro:")
    print("  un registro por HORA        -> hay que promediar los cuartos (RN-14) para comparar")
    print("  un registro por CUARTO      -> comparable directo con el ZIP")


if __name__ == "__main__":
    main()
