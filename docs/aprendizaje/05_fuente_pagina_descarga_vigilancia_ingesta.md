# M4, M4b y M5 — La página del Coordinador: descargar, vigilar e ingerir

> Guía de estudio. Archivos: `src/cmg_ingesta/extract/{coordinador_cmg,pagina_cen,ingerir_cen}.py`,
> `src/cmg_ingesta/quality/deriva.py`.
> Tests: `tests/extract/test_{coordinador_cmg,pagina_cen}.py`, `tests/quality/test_deriva.py`,
> `tests/test_cli.py`.

---

## 0. La idea central

```
 página del CEN ──► coordinador_cmg.py ──► BRONZE  data/bronze/cen_cmg/
   (HTML + ZIP)      descarga, sin tocar      ZIP tal cual + manifiesto.json
                          │
                          ▼
                     deriva.py  ── vigila ──► data/alertas/deriva_*.md
                          │
                          ▼
                  pagina_cen.py + ingerir_cen.py ──► SILVER  data/silver/cmg/anio=/mes=/
                     mapear, validar, escribir         el mismo esquema que el Maestro
```

Tres responsabilidades, tres módulos:

| Módulo | Pregunta que responde | Toca la red | Toca Silver |
|---|---|---|---|
| `coordinador_cmg.py` | ¿qué publicó el CEN y ya lo tengo? | sí | no |
| `deriva.py` | ¿cambió algo que el programa no conoce? ¿falta algo? | solo lee | no |
| `pagina_cen.py` + `ingerir_cen.py` | ¿cómo queda esto en el esquema canónico? | no | sí |

Es la arquitectura **medallion**: el Bronze guarda lo crudo **sin modificar**, para que si mañana
descubres un error en la ingesta puedas reingerir sin volver a descargar.

---

## 1. Descargar sin que Cloudflare te bloquee

El sitio está detrás de Cloudflare, que mira la **huella TLS** del cliente (cómo saluda al
servidor al abrir la conexión cifrada) antes de mirar cualquier header. `requests` y `httpx`
saludan como Python, y los rechaza:

| Cliente | Resultado |
|---|---|
| `requests` | `SSLEOFError` |
| `httpx` (aunque tenga User-Agent de Chrome) | 403 `cf-mitigated: challenge` |
| `curl_cffi` con `impersonate="chrome"` | **200** |

`curl_cffi` no ejecuta JavaScript ni abre un navegador: solo saluda como lo haría Chrome. Por eso
es suficiente y es lo mínimo (ADR-H04).

### Por qué la sesión se pasa como parámetro

```python
def sincronizar(desde, hasta, carpeta, sesion: Sesion, pausa=1.0, ...):
```

`Sesion` es un `Protocol`: cualquier objeto con un método `get(url, timeout)` sirve. No hay que
heredar de nada. En producción se pasa la sesión real de `curl_cffi`; en los tests, una
`SesionFalsa` que responde desde un diccionario:

```python
class SesionFalsa:
    def __init__(self, paginas: dict[str, str], zips: dict[str, bytes] | None = None):
        ...
    def get(self, url: str, timeout: float = 30.0) -> RespuestaFalsa:
        if url in self.paginas:
            return RespuestaFalsa(200, texto=self.paginas[url])
        ...
        return RespuestaFalsa(404)
```

Eso se llama **inyección de dependencias**: la función no crea su conexión, se la entregan. Es
lo que permite que 355 tests corran **sin tocar la red**.

### 🔬 Caso práctico 1 — la pausa obligatoria

```powershell
uv run python -c "from datetime import date; from pathlib import Path; from cmg_ingesta.extract import coordinador_cmg as c; c.sincronizar(date(2026,1,1), date(2026,1,1), Path('x'), None, pausa=0.2)"
```

Revienta con `ValueError` **antes** de hacer una sola petición. La pausa mínima de 1 s no es una
recomendación en un comentario: está en el código. Un error de uso no debería poder saturar el
sitio de otra institución.

---

## 2. Idempotencia: el manifiesto

Cada ZIP descargado queda anotado en `manifiesto.json` con su `sha256`, su tamaño, y lo que dice
su nombre (`tipo`, `version`, `reemision`, `fecha_operacion`).

- Si el nombre ya está en el manifiesto, **no se vuelve a bajar**.
- El manifiesto se escribe a un `.tmp` y se reemplaza con `os.replace` (atómico, igual que las
  particiones de la guía 02). Un corte a mitad no deja un JSON truncado.
- Si igual queda corrupto, `leer_manifiesto` devuelve `{}`: se re-descarga todo. **Más lento,
  nunca incorrecto.** Ese es el criterio para elegir qué hacer ante un estado inválido.

### 🔬 Caso práctico 2 — dos corridas

```powershell
cmg descargar-cen --desde 2026-09-28 --hasta 2026-09-28    # "2 archivo(s) nuevo(s)"
cmg descargar-cen --desde 2026-09-28 --hasta 2026-09-28    # "0 archivo(s) nuevo(s)"
```

El test que lo garantiza sin red: `test_descargar_cen_dos_veces_no_vuelve_a_bajar`. Fíjate que
no solo mira el texto, **cuenta las peticiones** a la URL del ZIP: tiene que ser una.

---

## 3. Los nombres de archivo y su regex

```python
RE_NOMBRE = re.compile(
    r"CMG_Real_(?P<tipo>[a-z]+)(?:_v(?P<v_antes>\d+))?_(?P<aammdd>\d{6})"
    r"(?:_v(?P<v_despues>\d+))?(?:-(?P<reemision>\d+))?\.zip$", re.I)
```

Se armó con **nombres reales**, no imaginados. Las dos variantes raras aparecieron en el sitio:

```powershell
uv run python -c "from cmg_ingesta.extract import coordinador_cmg as c; [print(n, c._info_nombre(n)) for n in ['Antecedentes_CMG_Real_def_260115.zip','Antecedentes_CMG_Real_pre_260906_v2.zip','Antecedentes_CMG_Real_pre_260404-1.zip','Antecedentes_CMG_Real_def_260115_final.zip']]"
```

```
Antecedentes_CMG_Real_def_260115.zip        ('def', 1, 0)
Antecedentes_CMG_Real_pre_260906_v2.zip     ('pre', 2, 0)
Antecedentes_CMG_Real_pre_260404-1.zip      ('pre', 1, 1)
Antecedentes_CMG_Real_def_260115_final.zip  ('desconocido', 1, 0)
```

El último no existe: es lo que pasaría si el CEN inventara un nombre nuevo. Queda como
`desconocido`, **se descarga igual** (el Bronze no pierde nada) y **no se ingiere** hasta que
alguien lo catalogue. Eso lo cuenta la sección 4.

---

## 4. Deriva: detectar, no adoptar

*Deriva* (drift) es cuando la fuente cambia y tu programa no se entera. Es el error más caro de
una ingesta, porque no revienta: carga datos mal y nadie lo nota durante meses.

`deriva.py` compara lo que publica el CEN contra un **catálogo** de lo que el programa conoce:

| Qué revisa | Ejemplo de hallazgo | Severidad |
|---|---|---|
| Índice de años | `año_nuevo` (2027 apareció en el índice) | info → **se adopta solo** |
| Índice de años | `slug_cambiado` | aviso |
| Página del día | `nombre_no_catalogado` | aviso |
| Miembros del ZIP | falta el `CmgBarrasComparativo` | crítico |
| Encabezado del CSV | `columna_requerida_ausente` | crítico |
| Encabezado del CSV | `columnas_nuevas` | aviso |
| Forma del día | `convencion_hora_base_1`, `falta_hora_extra` | crítico |
| Forma del día | `hora_fantasma_presente` (septiembre) | info |

Las tres severidades significan cosas distintas para quien opera:

- **crítico**: ese archivo **no se ingiere** hasta resolverlo.
- **aviso**: algo cambió; lo cargado sigue siendo válido, pero hay que mirarlo.
- **info**: cambio conocido o ya adoptado.

### Por qué solo se adopta el año nuevo (ADR-H05)

El slug de un año nuevo es inequívoco: el índice lo lista y la URL se arma sola. Si no se
adoptara, cada 1 de enero habría que editar código.

Todo lo demás se **reporta y no se adopta**. Si una columna se renombra de `CMG_REAL_DEF` a
`CMG_DEF`, adoptarla en automático sería ingerir datos que nadie miró. Puede ser un renombre
inocente o puede ser otra magnitud.

### Callbacks: vigilar sin acoplar

`sincronizar` no sabe nada de deriva. Acepta dos funciones opcionales:

```python
def al_leer_pagina(dia, html, docs): hallazgos.extend(revisar_pagina_dia(dia, html, docs))
def al_bajar(ruta, entrada):         hallazgos.extend(revisar_zip(ruta, ...))

cen.sincronizar(..., al_leer_pagina=al_leer_pagina, al_bajar=al_bajar)
```

Estas dos funciones se definen **dentro** de `sincronizar_vigilando` y usan la lista `hallazgos`
de afuera. Eso es un **closure**: una función que recuerda las variables del lugar donde se
creó. Así el extractor se puede usar sin vigilancia, y la vigilancia se agrega sin tocarlo.

### 🔬 Caso práctico 3 — simular que el CEN cambia una columna

```python
from cmg_ingesta.quality import deriva
cab = list(deriva.ENCABEZADO_COMPARATIVO)
cab[8] = "CMG_DEF[USD/MWh]"            # renombran CMG_REAL_DEF
for h in deriva.revisar_encabezado("x.csv", cab, deriva.ENCABEZADO_COMPARATIVO,
                                    deriva.REQUERIDAS_COMPARATIVO):
    print(h["severidad"], h["tipo"])
```

Vas a ver un `critico columna_requerida_ausente` y un `aviso columnas_nuevas`. Ese día no
entraría a Silver.

---

## 5. Completitud: ¿falta algo?

Que el formato no cambie no basta: también hay que saber si **llegó todo**. Dos preguntas:

1. ¿Hay días **sin ningún archivo**? → `dia_sin_registro`
2. ¿Hay días que **siguen en preliminar** cuando ya debería estar el definitivo? → `pre_sin_definitivo`

Los umbrales salen de **medir**, no de suponer:

```python
DIAS_GRACIA = 3    # el pre sale al día siguiente (medido: 1 a 3 días)
DIAS_MAX_PRE = 15  # el def sale a los 7–10 días (medido en 4 días reales)
```

Ayer sin archivo es normal; hace una semana no. Y un `pre` de 9 días es normal; uno de 20 días
puede ser una discrepancia en trámite ante el Panel de Expertos.

### Agrupar en rangos

Si la tarea programada no corrió durante un mes, no quieres 30 avisos: quieres uno.

```python
>>> deriva._rangos([date(2026,1,1), date(2026,1,3), date(2026,1,2), date(2026,1,7)])
[(date(2026,1,1), date(2026,1,3)), (date(2026,1,7), date(2026,1,7))]
```

Y la acción del hallazgo ya trae el comando exacto para arreglarlo:
`cmg descargar-cen --desde 2026-09-02 --hasta 2026-09-08`.

### `hoy` como parámetro

```python
def revisar_completitud(manifiesto, desde, hasta, hoy, ...):
```

¿Por qué no usar `date.today()` adentro? Porque entonces el test daría un resultado distinto
cada día que lo corras. Pasar el reloj como parámetro vuelve la función **pura**: misma entrada,
misma salida, siempre. La CLI es la que llama a `date.today()` (en `_hoy()`, que los tests
reemplazan).

### 🔬 Caso práctico 4 — rómpelo

En `revisar_completitud`, cambia `if edad >= dias_gracia:` por `if edad > dias_gracia:`.

```powershell
uv run pytest tests/quality/test_deriva.py -q -k recientes
```

Falla `test_los_dias_recientes_sin_archivo_no_avisan`: el día con exactamente 3 días de edad dejó
de avisarse. Un `>` contra un `>=` es la diferencia entre avisar y no avisar, y solo un test con
el borde exacto lo detecta.

---

## 6. M5: la trampa de los ceros

Este es el hallazgo más importante de la fuente (b). Mirado en un ZIP real del 2026-09-28:

| | `CMG_REAL_PRE` | `CMG_REAL_DEF` |
|---|---|---|
| ZIP **pre** | el valor | **0** |
| ZIP **def** | el valor | el valor |

En el ZIP pre, la columna DEF **no viene vacía: viene en cero**. Si la ingesta leyera siempre
`CMG_REAL_DEF`, cargaría ceros para todos los días en preliminar, **sin ningún error**: cero es
un CMg válido (pasa en horas de vertimiento solar).

Por eso la columna depende del tipo:

```python
COLUMNA_VALOR = {
    "def": "CMG_REAL_DEF[USD/MWh]",
    "pre": "CMG_REAL_PRE[USD/MWh]",
}
```

### 🔬 Caso práctico 5 — rómpelo

Cambia `"pre": "CMG_REAL_PRE[USD/MWh]"` por `"pre": "CMG_REAL_DEF[USD/MWh]"`.

```powershell
uv run pytest tests/extract/test_pagina_cen.py -q
```

Fallan `test_pre_lee_la_columna_pre_y_no_los_ceros_de_def`, `test_ingesta_de_un_mes` y
`test_cuando_llega_el_def_el_mes_se_reescribe_con_el`. El primero existe **solo** para esto: el fixture arma el ZIP pre con `definitivo=0`, igual que el real.

### Por qué `all_varchar=true`

```sql
read_csv(..., delim=';', header=true, all_varchar=true)
```

Si dejas que DuckDB adivine tipos, la columna DEF de un ZIP pre (todo `0`) sale `BIGINT` y la de
un def sale `DOUBLE`. Leer todo como texto y castear a mano deja **un** comportamiento.

---

## 7. Las dos horas raras

```python
def sql_un_dia(csv, dia, tipo):
    fantasma = calendario.horas_inexistentes(dia)     # [0] el día corto, [] los demás
    filtro = f"WHERE hora NOT IN (...)" if fantasma else ""
```

- **Abril (día largo):** el CSV trae `HORA` 0..24. La 24 es la segunda 23:00, que es justo la
  convención del esquema. Entra con `es_hora_extra = true` y `fecha_hora = NULL` (sería ambigua).
- **Septiembre (día corto):** el CSV trae la hora 0 en 0,00, pero esa hora **no existió**. Se
  descarta y se **cuenta** (`hora_fantasma` en el reporte).

Fíjate que la regla no tiene ninguna fecha escrita: sale de `calendario.horas_inexistentes`, que
la deriva de `zoneinfo` (guía 01). Funciona para 2027 sin tocar nada.

Datos reales: 2026-04-04 = 1.611 barras × 100 cuartos; 2026-09-06 = 1.641 × 92, con 6.564 filas
fantasma descartadas.

---

## 8. Incremental por huella

`ingerir-pagina` no reescribe todo cada vez. Guarda, por mes, el `sha256` de los ZIP que usó:

```json
{ "2026-09": ["0a1f...", "7c3e...", ...] }
```

Al correr de nuevo:

- Si la huella de un mes es igual → `2026-09: sin cambios, se omite`.
- Si llegó el `def` de un día que estaba en `pre` → cambia el archivo elegido de ese día →
  cambia la huella → **el mes se reescribe solo**.

Y una regla fina: si un mes tuvo un día omitido por hallazgo crítico, **no se guarda su huella**.
Así, cuando alguien corrija el catálogo, la próxima corrida lo reintenta sin `--forzar`.

### 🔬 Caso práctico 6 — rómpelo

En `ingerir_pagina`, quita el `if not fila["omitidos"]:` y deja el guardado siempre.

```powershell
uv run pytest tests/extract/test_pagina_cen.py -q -k critico
```

Falla `test_un_dia_con_hallazgo_critico_no_entra_y_se_reintenta`: el día malo quedaría fuera
para siempre, porque la huella diría "ya procesado".

---

## 9. Vista o tabla: el costo de releer

Primera versión:

```python
con.execute(f"CREATE OR REPLACE TEMP VIEW _entrante AS {consulta}")
res = checks.resumen(con, "_entrante")      # 6 consultas
escribir.escribir_particion(con, consulta, ...)
```

Una **vista** es una consulta guardada con nombre: cada vez que la usas, **se vuelve a
ejecutar**. `resumen` hace 6 consultas, más la escritura: los ~30 CSV del mes se parseaban 7
veces. Con 6 días reales tardaba **47 s**.

```python
con.execute(f"CREATE OR REPLACE TEMP TABLE _entrante AS {consulta}")   # se lee UNA vez
```

Con una **tabla** temporal, **9,1 s**. Mismo resultado, fila por fila.

Ningún test detecta esto: los tests verifican **qué** hace el programa, no **cuánto tarda**.
Por eso se mide con datos reales antes de dar algo por terminado.

---

## 10. Operación diaria

```powershell
cmg descargar-cen --desde 2025-01-01    # la primera vez; después, desde hace ~20 días
cmg ingerir-pagina
cmg vigilar-fuente                      # exit 2 = abrir data/alertas/
```

`--hasta` por omisión es **ayer**: el día de hoy todavía no tiene publicación.

¿Por qué `descargar-cen` desde hace ~20 días y no desde ayer? Porque el `def` llega 7–10 días
después. Si solo miras ayer, nunca recoges el definitivo de la semana pasada. El manifiesto
evita re-descargar lo que ya está, así que revisar 20 días cuesta 20 páginas, no 20 ZIP.

| Comando | exit 0 | exit 1 | exit 2 |
|---|---|---|---|
| `descargar-cen` | todo bien | fecha inválida, pausa < 1 | hay avisos/críticos → reporte |
| `ingerir-pagina` | todo bien | no hay Bronze | días omitidos o validación falló |
| `vigilar-fuente` | sin cambios | `--dias` inválido | algo cambió o falta |

---

## Glosario

| Término | Qué es | Dónde |
|---|---|---|
| Bronze / Silver | crudo tal cual / limpio en el esquema canónico | `data/bronze/`, `data/silver/` |
| Huella TLS (TLS fingerprint) | cómo saluda el cliente al abrir HTTPS; Cloudflare filtra por ella | §1 |
| `Protocol` | "cualquier cosa con estos métodos sirve", sin herencia | `Sesion`, `Respuesta` |
| Inyección de dependencias | pasar la conexión como parámetro en vez de crearla adentro | `sincronizar(..., sesion)` |
| Manifiesto | registro de lo descargado, con su sha256 | `manifiesto.json` |
| Deriva (drift) | la fuente cambia y el programa no se entera | `quality/deriva.py` |
| Closure | función que recuerda variables del lugar donde se creó | `al_leer_pagina` |
| Función pura | misma entrada → misma salida; sin reloj, red ni disco | `revisar_completitud` |
| Huella de un mes | sha256 de los ZIP usados; decide si reescribir | `ingesta_silver.json` |
| Vista vs tabla temporal | consulta que se re-ejecuta vs resultado guardado | §9 |
