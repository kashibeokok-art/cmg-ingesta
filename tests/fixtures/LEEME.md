# Fixtures HTML

**Son HTML real del sitio del Coordinador, capturado el 2026-10-06** con
`coordinador_cmg.nueva_sesion()` (`curl_cffi` con huella TLS de Chrome).

Son una **foto** de ese día: los tests verifican contra lo que había publicado entonces. Por
ejemplo, `dia_2026-09-30.html` tiene solo el `pre`, porque el `def` todavía no salía. Que el sitio
publique después más documentos no rompe los tests, porque el archivo no cambia.

| Archivo | Página | Contenido verificado |
|---|---|---|
| `dia_2026-01-15.html` | día | `def_260115` (pub 2026-01-22) y `pre_260115` (pub 2026-01-16), como hermanos |
| `dia_2026-10-05.html` | día | solo `pre_261005` (pub 2026-10-06) |
| `dia_2026-09-30.html` | día | `pre_260930` bajo **`uploads/2026/10/`**: datos de septiembre, carpeta de octubre |
| `mes_octubre-2026.html` | mes | **cero** ZIP; cada día aparece 2 veces en el texto crudo y **1** en el DOM |

## Si un test de parseo falla

**No toques el selector primero.** Recaptura el fixture y compáralo con el guardado: si el
markup cambió, eso es información — el sitio se movió — y hay que entender qué cambió antes de
ajustar el parser.

```powershell
uv run python -c "from datetime import date; from cmg_ingesta.extract import coordinador_cmg as c; s=c.nueva_sesion(); print(s.get(c.url_dia(date(2026,1,15)), timeout=45).text)" > nuevo.html
```

## Historia

Una primera versión de estos archivos fue **sintética**, construida desde el markup documentado,
porque desde `requests`/`httpx` el sitio respondía 403. La causa resultó ser el filtro de huella
TLS de Cloudflare (ver `docs/errores_verificados.md` A8). Al pasar a `curl_cffi` se capturó el
HTML real y los 53 tests pasaron contra él **sin cambiar ningún valor esperado**: el markup
documentado era exacto.
