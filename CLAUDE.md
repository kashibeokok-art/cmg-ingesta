# CLAUDE.md — `cmg-ingesta`

> Memoria del proyecto. Claude lo lee al iniciar cada sesión.

## ⚠️ Antes de hacer nada

**Lee [`docs/errores_verificados.md`](docs/errores_verificados.md).** Son errores que Claude ya
cometió en este proyecto, con la evidencia que los desmintió. Repetirlos es el fallo más caro.

**Regla permanente del usuario:** cuando Claude descubra que se equivocó, agrega la entrada a ese
archivo **antes de seguir**. No esperar a que el usuario lo pida.

---

## 0. Alcance definido por el usuario (2026-10-05)

**El programa es SOLO de CMg y barras.** Nada de retiros ni de inyecciones.

### 0.1 Las dos únicas fuentes de datos

| | Fuente | Periodo | Para qué |
|---|---|---|---|
| **a** | **`CMG_DB` (el Maestro ya procesado)** | **2021-01 → 2024-12** | el histórico |
| **b** | **Request de la página** de Transferencias Económicas → Costo Marginal Real | **2025-01 en adelante** | todo lo nuevo |

**Todo lo demás queda fuera**, por decisión explícita: la API `costo-marginal-real/v4`
(pierde la hora DST, §4.5.2) y los ZIP mensuales de Plabacom.

**El corte en 2024-12-31** (decisión del usuario, 2026-10-05) resuelve el problema de procedencia:
los timestamps de las particiones de `CMG_DB` muestran que 2021-01 → 2026-03 se escribieron en un
**único bloque** (el Maestro), y que 2026-04/05, 2026-06 y 2026-07 se agregaron por separado — el
último desde `cmg2607_15min_formateado.zip`, que es un ZIP y por tanto fuente descartada.
Cortando en 2024-12 se toma solo lo que está inequívocamente dentro del bloque del Maestro, y
**todo 2025+ se vuelve a traer de la página**, sin mezclas.

⚠️ **El histórico migrado arrastra el defecto de fechas de §4.5.1**: la hora extra de abril está en
el **viernes** en 2021, 2022 y 2024 (correcta en 2023). Al migrar hay que **validarlo y reportarlo**
con el calendario de M1, no corregirlo en silencio: no sabemos si el error viene del CEN o de la
ingesta antigua, y para saberlo haría falta el Maestro crudo, que ya no se usa.

## 0.5 Plan de desarrollo

Modo mentor (§2): Claude diseña, explica y revisa; **el usuario escribe el código**.

| Fase | Qué se construye | Estado |
|---|---|---|
| **M1** | **Dominio puro**: `calendario.py` (92/96/100 derivado de `zoneinfo`), `bloques.py`, `periodo.py` | ✅ 2026-10-06 |
| **M2** | **Esquema canónico** (`domain/esquema.py`) + **escritura particionada atómica** (`silver/escribir.py`) | ✅ 2026-10-06 |
| **M3** | **Migración del histórico** 2021–2024 (`extract/{cmg_db,migrar}.py`) + **validación** (`quality/checks.py`) | ✅ 2026-10-06 |
| **M6** | **Consultas y exportes**: `silver/leer.py`, `gold/{bloques_mes,riesgo_nodal}.py`, `reportes/exportar.py` | ✅ 2026-10-06 |
| **M7** | **CLI** (`cli.py`, `conexion.py`) con 9 comandos y 3 códigos de salida | ✅ 2026-10-06 |
| **M4** | **Descarga desde la página** (`extract/coordinador_cmg.py`, `curl_cffi`) | ✅ 2026-10-06 |
| **M4b** | **Vigilancia de la fuente** (`quality/deriva.py`): cambios de formato + completitud | ✅ 2026-10-06 |
| **M5** | **Ingesta de (b) a Silver** (`extract/{pagina_cen,ingerir_cen}.py`) | ✅ 2026-10-06 |
| **M8** | **Menú interactivo** (`menu/{busqueda,consola,app}.py`), estilo `CMG_Portable` | ✅ 2026-10-07 |

**441 tests · `mypy --strict` limpio · `ruff` limpio.**

**Uso diario:** `uv run cmg` sin argumentos abre el menú. Los comandos siguen para automatizar.

**Flujo diario de la fuente (b):**

```
cmg descargar-cen --desde 2025-01-01      # Bronze: ZIP + manifiesto (idempotente)
cmg ingerir-pagina                        # Silver: solo los meses que cambiaron
cmg vigilar-fuente                        # tarea programada: exit 2 = revisar data/alertas/
```

**Pendiente con el usuario:** el **backfill real 2025-01 → hoy** (~640 días, ~1.300 ZIP, ~16 GB,
~40 min de descarga con pausa de 1 s + ~16 min de ingesta). No se corre sin su aprobación: el
brief de la sesión 3 limitó las pruebas reales a 8 días (ya se usaron 7).

**Resultado de la migración (M3, corrida real):** 201.810.260 filas, 48 meses, 213,5 MB,
3,3 minutos. Las validaciones detectaron **solas** los 10 días defectuosos que el estudio E0
había encontrado a mano: los 6 de abril (corrimiento de fecha en 2021, 2022 y 2024, cada uno
como un par +4/−4) y los 4 de septiembre (hora fantasma, 23.152 filas).

**Qué pasa con E0.3b/E0.3c:** quedan dentro de **M4**. El timeout y los reintentos con backoff
siguen siendo necesarios; la paginación (`page`/`limit`/`totalPages`) era propia de la API y
**ya no aplica**.

### 0.6 ✅ ACTUALIZADO 2026-10-06 — la fuente (b) funciona

`extract/coordinador_cmg.py` construido y verificado contra el sitio real (`curl_cffi`):
parser, URLs con `SUFIJO` por año, recorrido del árbol, `sincronizar` idempotente con
manifiesto + sha256. Smoke test real 2026-09-28..30: 4 ZIP, todos íntegros, segunda corrida
0 nuevos. Fixtures HTML **reales** en `tests/fixtures/`. 257 tests.

**Formato del ZIP (nueva variante para M5):** `CmgBarrasComparativo_AAAAMMDD_AAAAMMDD_15.csv` con
`FECHA;HORA;MINUTO;BARRA;INFO_BARRA_ID;INFO_BARRA_NOMBRE;CMG_EN_LINEA[USD/MWh];CMG_REAL_PRE[USD/MWh];CMG_REAL_DEF[USD/MWh];USD;MODIFICADO`.
`HORA` en **base 0**. Un `def` trae también el `pre` y una bandera `MODIFICADO`.

**M5 cerrado (2026-10-06).** Reglas verificadas sobre los 6 ZIP reales que hay en disco:

- 🔴 **En un ZIP `pre`, `CMG_REAL_DEF` viene en 0 (no vacío).** Hay que leer `CMG_REAL_PRE` en
  los `pre` y `CMG_REAL_DEF` en los `def`. Leer siempre DEF cargaría ceros sin ningún error.
  2026-09-28: el PRE del ZIP pre y el DEF del ZIP def coinciden en las 158.016 filas.
  `MODIFICADO=SI` marca donde el PRE difiere del CMg en línea (1.839 filas ese día).
- **Nombres de barra iguales al Maestro**: 1.531 de las 1.534 barras de 2024-12 calzan exacto;
  115 barras son nuevas de 2025–26 y 3 se retiraron. No se normaliza nada.
- **Estado en el linaje:** `origen` = `pagina_cen_def` | `pagina_cen_pre` | `maestro_cmg_db`.
  El exporte lo muestra, así se sabe si un mes trae preliminares.
- **Incremental por huella** (sha256 de los ZIP de cada mes, en `bronze/cen_cmg/ingesta_silver.json`):
  cuando llega el `def` de un día en `pre`, el mes se reescribe solo.
- **Un día con hallazgo crítico de deriva no entra**, se reporta y el mes queda sin huella
  para reintentarse.
- Corrida real: 04-04 = 1.611 × 100 con hora 24; 09-06 = 1.641 × 92, 6.564 filas fantasma
  descartadas; valor de control A.BLANCAS 09-28 00:15 = 51.43224, exacto. 9,1 s los 6 días.

Texto anterior, superado:

### 0.6 (anterior) Lo que falta, y la única decisión pendiente

M4 y M5 son todo lo que queda, y dependen de resolver cómo obtener los `.zip` de la página
(§4.5.7). Medido el 2026-10-06:

- La **API** sirve para mantener la base al día (~30 consultas/día, dentro del límite de 60/hora)
  pero **no para la carga inicial**: `limit` máximo práctico 5.000, ~27 s por consulta, y 21 meses
  serían ~19.000 consultas ≈ 320 h con el rate limit. `limit=50.000` → 502; `limit=200.000` → 504.
- Los **`.zip` de la página** no se pueden descargar ni verificar con `httpx`: 403 con
  `cf-mitigated: challenge`, incluso con User-Agent de navegador. Eso bloquea **tanto la descarga
  como el chequeo de `v2`** que pidió el usuario.
- **Falta saber si los `.zip` son diarios o mensuales.** El nombre tiene `YYMMDD`, lo que sugiere
  diarios (~640 archivos para la carga inicial) pero podría ser mensual (21). **Hay que bajar uno
  a mano y mirarlo**: define el costo de toda la estrategia y además responde si trae los 100
  cuartos del día DST, que es la razón de usar esta fuente.

### 0.2 Qué hace el programa

1. **Ingerir** de esas dos fuentes y dejar los datos **procesados y compactados de forma óptima**
   (Parquet + DuckDB, particionado `anio/mes`, como la base actual).
2. **Todo lo que ya hace `CMG_Portable` con cada barra**, en el ámbito CMg:
   - Descargar CMg quinceminutal de una o varias barras, por periodo, en Excel/CSV/Parquet,
     con bloques A/B/C + Solar/NoSolar y hoja de resumen mensual.
   - Agregar un archivo nuevo a la base (validación, previsualización, confirmación, alerta de
     formato desconocido, historial de ingestas).
   - Riesgo nodal entre dos barras, en valor y en porcentaje.
   - Resumen de estado al arrancar, cacheado.

### 0.3 Qué queda FUERA

Del programa actual **no se porta**: ingesta de retiros, descarga de retiros por cliente,
valorización de retiros, ficha de cliente, y la compactación de medidas de red
(`preparar_medidas.py`). Todo eso es retiros.

**Proyecciones de CMg (AMEBA) — fuera por ahora, no descartadas** (decisión del usuario,
2026-10-07). Son las opciones 6 y 7 del programa actual y vienen de las planillas AMEBA, que no
son ninguna de las dos fuentes de §0.1. Si se retoman, AMEBA entra como tercera fuente; no
diseñar nada que lo impida.

### 0.4 🔴 El riesgo que abre esta decisión

Descartar los ZIP **elimina la única fuente verificada como completa en el día DST de abril**.

- El **Archivo Maestro** cubre el histórico, pero tiene la hora extra en el **día equivocado**
  en 2021, 2022 y 2024 (§4.5.1).
- La **API** pierde la hora (§4.5.2) — y queda fuera.
- El **ZIP** era el único con los 100 cuartos en la fecha correcta — y queda fuera.

→ Por lo tanto, **la fuente (b) tiene que traer los 100 cuartos del día DST de abril y los 92 de
septiembre**. Si no los trae, cada abril futuro pierde una hora de forma irrecuperable, con el
descuadre de balance que describe §4.5.3. **Verificarlo es el primer requisito de (b)**, antes de
construir cualquier cosa sobre ella.

## 1. Objetivo

Extraer la **ingesta y el procesamiento de datos** de `CMG_Portable` a un programa nuevo e
independiente, con la estructura y las buenas prácticas de
`C:\Users\claudio.araya\Desktop\Scripts\ppa-pipeline`.

- **No** se continúa el desarrollo de `ppa-pipeline` (decisión del usuario, 2026-10-05). De ahí se
  toman solo las convenciones y el método de trabajo.
- **No** se reutiliza código de `CMG_Build/src` por copiar y pegar: sirve como especificación
  funcional y como oráculo (los resultados actuales son la referencia numérica).
- La forma de encontrar los archivos **se mantiene**: el usuario entrega la ruta y el código procesa.

## 2. Modo de trabajo: MENTOR

Confirmado por el usuario el 2026-10-05: **el usuario escribe el código, Claude guía.** Rige
`ppa-pipeline/CLAUDE.md` §2 completo. En resumen:

- Claude entrega: concepto con su nombre técnico, el problema en el código legado con
  `archivo:línea`, el enunciado, **esqueletos con TODO** (no soluciones), criterios de aceptación,
  cómo decirlo en entrevista, y una lectura en inglés.
- Formato fijo de cada tarea: 🎯 Tarea · 🧠 Concepto · 🔍 En el legado · 🛠️ Tu turno ·
  ✅ Criterios · 🎤 Entrevista · 📖 Leer (EN).
- Escalar la ayuda de a poco: pista → pista concreta → fragmento → solución.
- Excepción: si el usuario dice "hazlo tú", Claude lo hace y explica línea por línea.
- Una tarea por vez. Cada parte termina funcionando y probada.
- Español; términos técnicos en inglés entre paréntesis la primera vez.
- Preguntas de verificación **antes** de dar respuestas.
- El usuario es fuerte en el negocio eléctrico y está en formación en Python. POO recién vista en
  `ppa-pipeline` H2 (pydantic-settings): no dar clases por sabidas.

## 3. Stack

Igual que `ppa-pipeline` §6.3, más decisiones propias:

| Área | Herramienta |
|---|---|
| Entorno/deps | `uv` + `pyproject.toml` + lockfile |
| Calidad | `ruff`, `mypy` **strict**, `pre-commit` |
| Tests | `pytest` |
| Datos | `duckdb`, `pandas`, `pyarrow` |
| Config | `pydantic-settings` (`SecretStr` para la clave) |
| HTTP | **`httpx`** (ADR-H01) |
| Reintentos | `tenacity` |
| CLI | `typer` |

## 4. Fuentes de CMg y el estudio E0

Antes de construir la ingesta se verifica si el archivo y la API entregan **el mismo dato**.
Son **tres** fuentes, no dos:

| | Fuente | Estado |
|---|---|---|
| **A** | CSV maestro → ya cargado en `CMG_Portable/CMG_DB` (2021-01 a 2026-07) | disponible |
| **B** | ZIP mensual `cmgAAMM_15minutal.zip` (en `Scripts/Retiros/Old/25MM/.../03 Cmg/`) | disponible |
| **C** | API SIP `costo-marginal-real/v4/findByDate` | **falta la `user_key`** |

Hallazgos del perfilado inicial (2026-10-05):

- El ZIP trae `FECHA;HORA;MINUTO;BARRA;CMg[USD/MWh];CMg[CLP/KWh];USD`. La columna de barra se
  llama **`BARRA`**, no `nombre_barra_cmg`, así que `core.detectar_formato()` **la rechaza**:
  es una **tercera variante** de formato que hay que soportar.
- Consistencia interna confirmada: `CMg[CLP/KWh] = CMg[USD/MWh] × USD / 1000`
  (55,84362 × 992,12 / 1000 = 55,4036 ✓). La columna correcta es **USD/MWh**.
- `HORA` es **1-based** (HORA=1 ↔ 00:00), consistente con el `- 1` de `core.py:159`.
- Los meses 2025-01..04 de `CMG_DB` vinieron del **maestro**, no de estos ZIP. Por eso A sirve
  como tercer testigo: si B y C coinciden y A difiere, el problema está en cómo se ingirió A.

**Las 8 dimensiones, todas resueltas el 2026-10-05:**

| # | Dimensión | Resultado |
|---|---|---|
| 1 | Granularidad | ✅ ambas quinceminutales |
| 2 | Unidad | ✅ las dos traen USD/MWh y CLP/kWh, mismo tipo de cambio (992,12) |
| 3 | Convención de hora | ⚠️ API `hra` **0-based**, ZIP `HORA` **1-based** |
| 4 | **DST** | 🔴 **la API pierde la hora extra y la mislabela** (§4.5) |
| 5 | Nombre de barra | ✅ `barra_transf` == `BARRA`, idénticos |
| 6 | Cobertura | ✅ 1.554 barras en ambas (§4.4) |
| 7 | Versión | ✅ API se autoidentifica `REAL-DEF`; el ZIP lo dice en el nombre (`_def`) |
| 8 | Valores | ✅ idénticos al 5º decimal en día normal (§4.3) |

**Conclusión: son el mismo dato, salvo el día DST de abril, donde la API está incompleta.**
Ver la decisión de fuente en §4.6.

### 4.1 API del CEN: lo confirmado

Fuente: `USO_DE_APIS_CEN_v1.0.pdf` (documento oficial del CEN, leído 2026-10-05), que incluye un
`curl` funcional de ejemplo:

```
curl -X 'GET' \
  'https://sipub.api.coordinador.cl/cotas-embalses-reales/v3/findAll?startDate=2024-01-01&endDate=2024-01-02&page=0&user_key=<CLAVE>' \
  -H 'accept: application/json'
```

| Dato | Valor |
|---|---|
| Host | `https://sipub.api.coordinador.cl` (**sin `:443` ni `/443/`**) |
| Patrón | `/<servicio>/<version>/<operacion>` |
| Endpoint de CMg | `/costo-marginal-real/v4/findByDate` |
| Autenticación | `user_key` como **query parameter** (no header, no Bearer) |
| Paginación | parámetro `page`, **0-based** |
| Header | `accept: application/json` |

- **SIP = Sistema de Información Pública.** Modelo API Key, se activa **de inmediato**, sin
  aprobación del CEN. Las APIs **operacionales** son otra cosa (OAuth 2.0 con
  `client_id`/`client_secret` → JWT contra `security-access.api.coordinador.cl`): **no es este caso.**
- ⚠️ **Restricción de diseño:** los datos del SIP tienen **al menos 1 día de desfase**
  (textual del PDF: *"destinadas a consultar información al menos con un desface de 1 día"*).
  La ingesta no debe pedir el día en curso.
- ⚠️ El PDF trae un `user_key` de ejemplo. **No usarlo ni copiarlo a ningún archivo.**

### 4.2 API del CEN: esquema real de la respuesta

Confirmado con una llamada real el 2026-10-05 (`status 200`), **no supuesto**.

Raíz: `{"data": [...], "type": ..., "totalPages": ..., "page": ..., "limit": ...}`
→ los registros vienen en **`data`**; la paginación se controla con `page` (0-based) y `limit`,
y `totalPages` dice cuántas hay.

Campos de un registro:

| Campo | Ejemplo | Nota |
|---|---|---|
| `id_info` | `160` | id interno de barra. *Untrusted source key*: no usar para unir |
| `barra_info` | `"BA S/E SANTA ELVIRA 15KV BP1"` | nombre descriptivo |
| `barra_transf` | `"STA.ELVIRA____013"` | **idéntico al `BARRA` del ZIP** → es la clave de cruce |
| `fecha` | `"2025-01-01"` | |
| `hra` | `0` | **0-based** (¡el `HORA` del ZIP es **1-based**!) |
| `min` | `0`, `15`, `30`, `45` | **granularidad quinceminutal** |
| `cmg_clp_kwh_` | `48.71837` | ojo al guion bajo final |
| `cmg_usd_mwh_` | `49.10532` | **la columna a usar** (RN/ADR-005) |
| `version` | `"REAL-DEF"` | la API se autoidentifica como definitiva → dimensión 7 |
| `fecha_hora` | `"2025-01-01 00:00"` | el **bucket horario**: igual para los 4 cuartos |
| `fecha_minuto` | `"2025-01-01 00:15"` | el timestamp real del intervalo |

- **No vienen headers de rate limit.** No se sabe el límite: subir el volumen de a poco.
- `bar_transf` **sí existe** como parámetro de consulta (se usó en la llamada).
- ⚠️ `hra` 0-based vs `HORA` 1-based del ZIP: comparar sin ajustar deja todo **corrido una hora**.
  El código actual ya resta 1 al ZIP (`CMG_Build/src/core.py:159`).

### 4.3 Resultado del estudio: las tres fuentes coinciden ✅

Verificado el 2026-10-05 para `STA.ELVIRA____013`, 2025-01-01, los 4 primeros cuartos:

```
hora:min       A (base)      B (zip)      C (api)
00:00          49.10532     49.10532     49.10532
00:15          51.60896     51.60896     51.60896
00:30          54.10641     54.10641     54.10641
```

Tres confirmaciones independientes: los valores, el mismo tipo de cambio implícito (992,12 en las
tres) y la convención de hora que calza al aplicar el `-1` al ZIP.

⚠️ La base guarda `49.10531997680664` y las otras dos `49.10532`: es **float32 en el Parquet**, no
una diferencia de datos. La comparación formal (E0.5) debe usar **tolerancia**, no `=` (ver C3).

### 4.4 Cobertura de barras: la API calza exacto ✅

Medido el 2026-10-05 con **una** llamada (`limit=1` → `totalPages` = total de registros del día):

- Día normal `2025-04-07`, sin filtrar barra: **`totalPages` = 149.184**
- 149.184 / 96 cuartos = **1.554,0 barras**, idéntico a `CMG_DB` 2025-04 (1.554)
- El servidor **respeta el `limit`** que se le pide (pedimos 1, devolvió `limit: 1`)

### 4.5 🔴 La API PIERDE la hora extra del cambio de horario

**El hallazgo más importante del estudio.** Verificado el 2026-10-05 para `A.BLANCAS_____013`
en el día DST `2025-04-05` (el día de 25 horas de 2025):

| Fuente | Registros | Convención |
|---|---|---|
| **A** `CMG_DB` (maestro) | **100** | `hora` 0..24 |
| **B** ZIP `cmg2504_def` | **100** | `HORA` 1..25 (→ 0..24 al restar 1) |
| **C** API | **96** ❌ | `hra` 0..23, **sin 24** |

Y no falla de forma inocua. Los valores que la API entrega como `hra=23` son los de la **segunda**
hora 23 (la de después del retroceso del reloj = `hora 24` en A y `HORA=25` en B):

| min | ZIP `HORA=24` → hora 23 | ZIP `HORA=25` → hora 24 | API `hra=23` |
|---|---|---|---|
| 0 | 204.63290 | **206.15653** | **206.15653** |
| 15 | 207.50211 | **202.05323** | **202.05323** |
| 30 | 208.24630 | **201.77911** | **201.77911** |
| 45 | 206.88693 | **192.75051** | **192.75051** |

**Caracterización completa** (2026-10-05, `A.BLANCAS_____013`, los 96 vs 100 intervalos,
tolerancia 1e-4 — `estudio/comparar_dia_completo.py`, respuestas crudas en `estudio/salida/`):

| Hora (reloj) | API | ZIP y base | Relación |
|---|---|---|---|
| 00–21 | ✅ | ✅ | **idénticas**, 88 intervalos |
| **22** | 208.03955… | 210.07436… | 🔴 **datos distintos** |
| **23** | 206.15653… | 204.63290… | la API trae la **hora 24** del ZIP |
| **24** | ausente | 206.15653… | la API **no la trae** |

Resultado del comparador: `iguales: 88 · distintos: 8 · solo en el ZIP: 4 (hora 24)`,
con desalineación `d=+1` únicamente en la hora 23.

**La hora 22 es una contradicción entre fuentes, no un corrimiento.** Los 4 valores que la API
da a las 22:00 **no aparecen en ningún lugar del abril del ZIP** para esa barra (búsqueda
exhaustiva sobre las 2.884 filas del mes, tolerancia 1e-4). Y A (maestro) y B (ZIP) **coinciden
entre sí** en 210.07436: dos snapshots independientes contra uno. La API es la minoría.
Cuál es el valor correcto **no se puede decidir con los datos**; es pregunta para el CEN.

Los metadatos de la API no lo explican: ambos días declaran `version: REAL-DEF` y el mismo
`id_info`. La API afirma que su hora 22 es definitiva.

### 4.5.2 Mecanismo: sobrescritura por colisión de clave (confirmado)

Hipótesis del usuario: *"¿puede ser que en la petición se guarde la última hora registrada y
sobrescriba la esperada?"*. **Confirmada para la hora repetida.** Probado en 4 barras
(`A.BLANCAS_____013`, `STA.ELVIRA____013`, `LATORRE_______066`, `QUELLON_______013`,
`estudio/dst_varias_barras.py`):

| barra | API | ZIP | 0–21 | h22==ZIP22 | h23==ZIP24 | h24 |
|---|---|---|---|---|---|---|
| A.BLANCAS_____013 | 96 | 100 | 88/88 | NO | **SÍ** | no |
| STA.ELVIRA____013 | 96 | 100 | 88/88 | NO | **SÍ** | no |
| LATORRE_______066 | 96 | 100 | 88/88 | NO | **SÍ** | no |
| QUELLON_______013 | 96 | 100 | 88/88 | NO | **SÍ** | no |

Si la API guarda por clave `(barra, fecha, hora_de_reloj, min)`, las dos 23:00 del día DST
colisionan y **la segunda escritura gana**. Eso predice exactamente `API[23] == ZIP[24]` y la
ausencia de la hora 24, y es lo que ocurre en el 100% de las barras probadas.

**La hora 22 sigue sin explicación, pero está caracterizada:**

- Difiere en **todas** las barras probadas → es parte del mismo mecanismo, no un caso aislado.
- **No coincide con ninguna hora del ZIP** (20 a 24) en ninguna barra.
- **No es una operación aritmética** sobre las horas del ZIP: 64 combinaciones probadas
  (promedios de pares y triples de las horas 18–24, más ponderados), tolerancia 1e-3 →
  **0 de 16 casos** reproducidos (`scratchpad/probar_operaciones.py`).
- Coincidencia suelta, probablemente espuria: `A.BLANCAS` h22 min0 == `LATORRE` ZIP h23 min0
  (208.03955). 1 de 16 casos, y el CMg se repite entre barras sin congestión, así que no prueba
  mezcla de barras.

→ El valor de la hora 22 es **ajeno a la serie propia de la barra** ese día y no se deriva de ella.
Apunta a un cálculo distinto aguas arriba, pero **no se puede determinar con los datos**.

### 4.5.3 Marco normativo del día de 25 horas (aportado por el usuario)

Esto explica por qué la búsqueda aritmética de 4.5.2 **no podía** encontrar nada, y eleva la
validación de 100 cuartos a requisito normativo.

- **DS N° 98** (Ministerio del Interior): retrasa la hora oficial el primer sábado de abril.
- **NT CyO** (CNE): los balances de inyecciones, retiros y costos marginales deben **adaptarse
  dinámicamente a los husos horarios oficiales**.
- **NTSyCS**: medidores, SCADA y registradores operan con **sincronización GPS referenciada a UTC**.
  → **Físicamente no hay pérdida de datos.** Cualquier hora faltante es un defecto de la **capa de
  publicación en hora local**, no una laguna de medición. Esto es lo que clasifica el
  comportamiento de la API como un bug de publicación.
- **Dimensión del día:** normal = 24 filas (horario) o **96** (cuarto-horario); el sábado del
  cambio = 25 u **100**.
- **Dos convenciones de etiquetado** según la fuente:
  1. **Indexación consecutiva 1..25**, la hora 25 es la repetida → **es la del ZIP mensual**.
  2. **Dos filas con estampa 23:00**: la primera GMT-3 (verano saliente), la segunda GMT-4
     (invierno entrante) → **es la que intenta la API, pero colapsa las dos y conserva la de
     GMT-4**.
- 🔴 **Doble valorización:** las horas 24 y 25 tienen **CMg independientes y potencialmente
  distintos**, porque en esos 60 minutos cambian la demanda real, la generación eólica/hidráulica
  y el despacho. **No existe ninguna relación aritmética esperable entre ellas** — por eso las 64
  combinaciones de 4.5.2 no reprodujeron nada: se buscaba algo que el dominio dice que no existe.
- 🔴 **Omitir la fila 25 genera un descuadre energético y financiero** en el balance mensual de la
  auditoría. Las fórmulas de transferencias económicas deben iterar sobre los **25** periodos.

**Consecuencia para el diseño:** la validación "el día DST debe tener 100 cuartos por barra" no es
una buena práctica opcional, es un **requisito normativo**. Y la regla ya registrada en la memoria
de Claude aplica a cualquier script del mercado eléctrico chileno, no solo a este proyecto.

**El "por qué" no se puede resolver con los datos.** Pregunta concreta para
`soporte.sip@coordinador.cl`: cómo trata `costo-marginal-real/v4` la hora duplicada del cambio de
horario, con este caso de ejemplo (`A.BLANCAS_____013`, 2025-04-05, horas 22–24).

**Día normal, para contraste:** `2025-04-07`, **96 de 96 idénticos, 0 diferencias.**

Consecuencias firmes:

- Una ingesta ingenua desde la API en el día DST pierde 4 intervalos, **mete la hora 24 en la
  hora 23** y además discrepa en la hora 22. No basta con "agregar la hora que falta":
  hay que **descartar el día DST completo de la API y tomarlo del ZIP**.
- Volumen: 4 intervalos × 1.554 barras = **6.216 filas por año**. Caen en el **bloque A**
  (23:00–07:59), así que contaminan los promedios de bloque.
- `fecha_minuto` **no se repite** en la respuesta de la API, justamente porque falta una hora.
  No sirve para detectar el problema.

### 4.5.1 El día DST: la regla y un defecto en los datos actuales

**Regla de negocio (aportada por el usuario, experto en el dominio):** la hora extra aparece
el **primer sábado de abril**. Texto oficial:

> En Chile el cambio de hora de abril ocurre a la medianoche del primer sábado del mes. A las
> 00:00 del domingo — *es decir, cuando pasan de las 00:00 a las 23:00 del sábado* — los relojes
> se atrasan una hora.

Lo decisivo de ese texto: la hora repetida (23:00–23:59) pertenece al **sábado**, así que **el
sábado tiene 25 horas**. Eso resuelve la ambigüedad de a qué fecha se asocia la hora extra.

Verificado contra `zoneinfo("America/Santiago")`: el día local de 25 horas coincide con el primer
sábado de abril **6 de 6 años** (2021–2026).

**Excepciones regionales — verificado que NO aplican:** Aysén y Magallanes no cambian de hora, e
Isla de Pascua lo hace a las 22:00 locales del mismo sábado. Si hubiera barras de esas zonas, una
sola zona horaria sería incorrecta. Revisadas las 1.646 barras de `CMG_DB` (2026-10-05): **ninguna**
de Coyhaique, Punta Arenas, Natales, Porvenir ni Isla de Pascua. Las más australes son de Chiloé
(`QUELLON`, `CASTRO`, `CHONCHI`, `ANCUD`), consistente con que Aysén y Magallanes son *Sistemas
Medianos*, separados del SEN.
→ **Supuesto documentado:** una sola zona `America/Santiago` cubre todo el dato. Revisar si alguna
vez se ingieren Sistemas Medianos.

**Pero `CMG_DB` tiene la `hora=24` en el viernes anterior en 3 de 6 años:**

| Año | En `CMG_DB` | 1er sábado / 25 h real | |
|---|---|---|---|
| 2021 | 2021-04-02 (viernes) | 2021-04-03 (sábado) | ❌ −1 día |
| 2022 | 2022-04-01 (viernes) | 2022-04-02 (sábado) | ❌ −1 día |
| 2023 | 2023-04-01 (sábado) | 2023-04-01 (sábado) | ✅ |
| 2024 | 2024-04-05 (viernes) | 2024-04-06 (sábado) | ❌ −1 día |
| 2025 | 2025-04-05 (sábado) | 2025-04-05 (sábado) | ✅ |
| 2026 | 2026-04-04 (sábado) | 2026-04-04 (sábado) | ✅ |

En esos 3 años el **viernes tiene 100 cuartos y el sábado 96**, al revés de lo correcto.
Son ~17.000 registros fechados un día antes. **Origen no determinado**: puede venir del CSV
maestro o de cómo se ingirió. Es un defecto de la base actual (`CMG_Portable/CMG_DB`), no del
proyecto nuevo. Pendiente de decisión del usuario si se persigue.

**Regla de diseño para la ingesta nueva — hacer las dos cosas:**

1. **Calcular** el día DST con `zoneinfo("America/Santiago")`, no con una lista fija ni
   "detectando" el día de 100 cuartos (el cálculo es confiable; la etiqueta del dato no).
2. **Validar** que ese día tenga 100 cuartos por barra. Si tiene 96, o si el día de 100 cuartos
   es otro, es un **error de calidad de datos que se reporta**, no un caso a absorber en silencio.

Esto confirma `ppa-pipeline` ADR-002: *"no programar abril/septiembre a mano: usar la zona
America/Santiago"*.

En 6 años **no hay ninguna otra anomalía**: todos los demás días tienen exactamente 96 cuartos por
barra, y el hueco de septiembre no aparece (consistente con RN-03, que no lo considera).

### 4.5.4 🔴 El caso espejo: septiembre inventa una hora a precio cero

En septiembre el reloj **se adelanta** (salta de 00:00 a 01:00 del domingo), así que ese día tiene
**23 horas = 92 cuartos**. Verificado con `zoneinfo("America/Santiago")`: el día de 23 horas es
siempre **domingo** (2021-09-05, 2022-09-11, 2023-09-03, 2024-09-08, 2025-09-07) y la hora que
**no existe es la hora 0**.

**Pero tanto la API como `CMG_DB` traen 96 cuartos, con la hora 0 presente y en CERO.**

Verificado en la API el 2026-10-05 (`A.BLANCAS_____013`, 2025-09-07, 1 llamada): 96 registros,
hora 0 con `fecha_minuto` `2025-09-07 00:00..00:45`, los 4 en `0.00000`, `version: REAL-DEF`.
→ **El cero lo publica el CEN, no lo pone nuestra ingesta.**

Tratamiento en `CMG_DB`, sobre todas las barras:

| Día | Barras | h0 en CERO | h0 == h1 |
|---|---|---|---|
| 2021-09-05 | 1.384 | 154 (11,1%) | **1.230 (88,9%)** |
| 2022-09-11 | 1.426 | **1.426 (100%)** | 0 |
| 2023-09-03 | 1.465 | **1.465 (100%)** | 0 |
| 2024-09-08 | 1.513 | **1.513 (100%)** | 0 |
| 2025-09-07 | 1.590 | **1.590 (100%)** | 0 |

Desde 2022 la convención es uniforme (cero); en 2021 el 89% **duplicaba la hora 1**, que es peor
porque un valor duplicado no se distingue de uno real.

**Impacto medido — dilución pura del bloque A de septiembre:**

```
2022-09 bloque A: con la hora fantasma 116.8413 | sin ella 117.2756  -> -0.370%
2023-09 bloque A: con la hora fantasma  69.1677 | sin ella  69.4248  -> -0.370%
```

El −0,370% es exacto y explicable: el bloque A tiene 9 horas → 36 cuartos/día × 30 días = 1.080,
y 4/1.080 = 0,370%. Son 4 intervalos **inventados** a precio 0 que arrastran el promedio.

⚠️ No confundir con los ceros legítimos: el 24,7% de los CMg de la base son exactamente 0 (horas
de sol). El cero **no** es la señal; la señal es que **esa hora no existió en el reloj**.

**Regla de diseño:** RN-03 dice que la hora faltante de septiembre no se considera, así que esos
4 intervalos se **descartan**, no se ingieren con cero. El día correcto tiene **92 cuartos**.

### 4.5.5 Los dos casos límite, lado a lado

| | Abril (1er sábado) | Septiembre (domingo) |
|---|---|---|
| Reloj | 25 horas | 23 horas |
| Cuartos correctos | **100** | **92** |
| La API trae | **96** ❌ | **96** ❌ |
| Falla | **pierde** una hora real (colisión de clave) | **inventa** una hora a precio 0 |
| Hora afectada | la segunda 23:00 (y la 22 difiere) | la hora 0 |
| Arreglo | tomar el día completo del ZIP | **descartar** esos 4 intervalos |
| Detección | `zoneinfo` + validar 100 cuartos | `zoneinfo` + validar 92 cuartos |

Los dos se detectan de forma determinista con `America/Santiago`. **Ninguna validación que asuma
96 cuartos por día sirve**: hay que calcular el largo esperado del día.

### 4.5.6 Fuente nueva para datos nuevos: la página de Transferencias Económicas

Decisión del usuario (2026-10-05): **por los defectos de la API en los días de cambio de hora, los
datos nuevos se toman de la página de documentos de Costo Marginal Real.**

`https://www.coordinador.cl/mercados/documentos/transferencias-economicas/costo-marginal-real/2026-costo-marginal-real/`

**Cómo se accede, medido el 2026-10-05:**

| Recurso | Resultado con `httpx` simple |
|---|---|
| `/wp-content/uploads/...` (archivos) | **200**, descarga completa (probado con un PDF de 1,08 MB) |
| La página HTML del listado | **403**, `cf-mitigated=challenge` |
| `www.coordinador.cl/` (raíz) | **403**, `cf-mitigated=challenge` |

→ **Las páginas HTML están detrás de un desafío de bots de Cloudflare; los archivos no.**
Un `GET` con `requests`/`httpx` **no** baja el listado: devuelve el HTML de
`Just a moment...` con `challenges.cloudflare.com`.

### Patrón de URL (aportado por el usuario, 2026-10-06)

```
/wp-content/uploads/{YYYY}/{MM}/Antecedentes_CMG_Real_{def|pre}[_v2]_{YYMMDD}.zip
```

- **`def` vs `pre`**: definitivo vs preliminar. Prioridad: `def_v2` > `def` > `pre_v2` > `pre`.
- **`YYMMDD`** es la fecha del dato (`250702` = 2025-07-02).
- ⚠️ **La carpeta `{YYYY}/{MM}` es el mes de PUBLICACIÓN, no el del dato.** Evidencia:
  `/2026/07/Antecedentes_CMG_Real_def_260707.zip` y
  `/2026/08/Antecedentes_CMG_Real_def_v2_260707.zip` — misma fecha de dato, carpetas distintas,
  porque la revisión `v2` se publicó en agosto. → **la URL de una revisión no se puede construir**
  desde la fecha del dato; habría que sondear los meses siguientes.

### ✅ CORREGIDO 2026-10-06: los `.zip` SÍ se descargan — con `curl_cffi`

**Lo que sigue en esta sección fue un diagnóstico equivocado** (ver
`docs/errores_verificados.md` A8). El filtro de Cloudflare es por **huella TLS**, no por
JavaScript: `curl_cffi` con `impersonate="chrome"` obtiene 200 en el índice, en las páginas de
día y en los ZIP, sin navegador. La pista ignorada: `requests` puro no recibía 403 sino un **corte
del handshake TLS** (`SSLEOFError`). Implementado en `extract/coordinador_cmg.py: nueva_sesion()`.

**Resultado de la verificación, día de 25 horas (2026-04-04), `def_260404.zip`:**
`CmgBarrasComparativo_*_15.csv` trae **100 cuartos por barra, `HORA` 0..24, sin repeticiones**
(1.611 barras × 100). La hora extra es `HORA=24`, en base 0 — la misma convención del programa.
**Esta fuente NO colapsa la hora extra, a diferencia de la API.** El día de 23 horas
(2026-09-06) trae 96: la hora 0 inexistente, en cero en el 100% de las 1.641 barras, igual que
todas las fuentes.

Texto original del diagnóstico equivocado, conservado como registro:

Medido el 2026-10-06 sobre las 8 URL reales:

| Recurso | `httpx` simple |
|---|---|
| `.pdf` bajo `/wp-content/uploads/` | **200**, descarga completa |
| `.zip` bajo `/wp-content/uploads/` | **403**, `cf-mitigated: challenge` |

Probado con `HEAD`, con `GET`, sin User-Agent, con User-Agent de navegador y con `referer`:
**todas 403**. Cloudflare tiene una regla que protege los `.zip` de esa ruta y **exige resolver el
desafío JavaScript**. No es un header que falte.

→ El diseño de "construir la URL y descargar" **no es viable para los `.zip`**, aunque
`robots.txt` tenga `Allow: /wp-content/uploads/*`. Alternativas en §4.5.7.

### 4.5.7 Alternativas para la fuente (b), con el costo de cada una

1. **Landing zone manual (recomendada).** El usuario descarga el `.zip` en su navegador y lo deja
   en una carpeta; el programa ingiere desde ahí. Es la única que no requiere eludir nada y no se
   rompe cuando cambie el desafío. Costo: un paso manual por archivo.
2. **API SIP + ZIP manual solo para abril.** La API funciona con la `user_key` y es correcta
   **salvo** el día DST (§4.5.2) y la hora fantasma de septiembre (§4.5.4, descartable de forma
   determinista). Automatiza todo el año y deja 1 descarga manual anual. Costo: depender de una
   fuente con un defecto conocido y acotado.
3. **Navegador automatizado** (Selenium/Playwright). Resolvería el desafío, pero es el
   anti-patrón que `ppa-pipeline` §5.1 rechaza explícitamente y elude un control puesto a
   propósito. **No recomendada.**
4. **Pedir al CEN acceso oficial** a estas descargas (junto con la consulta de
   `docs/consulta_cen_dst.md`). Es la solución de fondo, pero no tiene plazo.

⚠️ Distinción con el anti-patrón: `ppa-pipeline` §5.1 condena el scraping de **buscadores**
(Google/Bing con user-agents rotativos). Descargar del **canal oficial de distribución del
publicador** es otra cosa. Lo que sí se evita es forzar el desafío de Cloudflare.

**Pendiente antes de comprometerse con esta fuente — hay que verificar:**

1. ¿Tiene el **día DST completo** (100 cuartos en abril, 92 en septiembre)? Es la razón del cambio
   de fuente; si falla igual que la API, no sirve.
2. ¿Qué **granularidad**? La página es de *Transferencias Económicas*, puede ser horaria y no
   cuarto-horaria.
3. ¿Qué **formato y columnas**? ¿Coincide con el `cmgAAMM_15minutal.csv` del ZIP o es otro esquema
   (otra variante de formato a soportar)?
4. ¿Qué **versión** del dato? (preliminar vs. definitivo, como el `_def` del ZIP)

### 4.6 Decisión de fuente

**El ZIP mensual es la fuente autoritativa. La API es complemento, no reemplazo.**

- ✅ ZIP / maestro: completos, incluida la hora DST.
- ⚠️ API: cobertura de barras idéntica (§4.4) y valores idénticos en días normales (§4.3), pero
  **incompleta en el día DST de abril** (§4.5).
- Si en algún momento se ingiere desde la API, el día DST de abril debe **completarse desde el ZIP**
  y la ingesta debe **validar que abril tenga 100 cuartos por barra**, no 96.

Pendiente de ampliar en E0.5: más barras y más meses, para confirmar que fuera del día DST la
paridad es total. El septiembre de 23 horas no aplica (RN-03: no se considera).

⚠️ `https://portal.api.coordinador.cl` **no se puede leer con WebFetch**: devuelve
`unable to verify the first certificate` (cadena de certificados incompleta en su servidor).
Hay que abrirlo en el navegador; su documentación tiene un "Authorize" + "try it out".

## 5. Anti-patrones del código a portar (`CMG_Build/src`)

Lo que **no** se repite:

- 🔴 `input()` dentro de la lógica — `ingesta.py:234` pregunta a mitad de `ingerir_csv()`. Por eso
  esa función no se puede testear ni automatizar. La decisión sale a la CLI.
- 🟠 `print()` en vez de logging; sin `run_id` no hay trazabilidad de una ejecución.
- 🟠 Dos mecanismos de rutas que hacen lo mismo: `core._raiz()` y `comun.conexion.ruta_base()`,
  ambos derivando del ejecutable. Va a `Settings` (12-factor).
- 🟠 `comun.conexion.abrir()` crea carpetas como efecto secundario al llamarse.
- 🟠 Dos conexiones DuckDB con parámetros distintos (`core.abrir_duckdb` vs `conexion.abrir`).
- 🔵 `ingerir_csv()` hace diez cosas: detecta, lee, valida, reporta, previsualiza, pregunta,
  mergea, escribe, resume e historia.
- 🔵 Cero tests y cero tipos en ~1.450 líneas.

Lo que **sí** se conserva porque ya está bien:

- Escritura atómica: `tmp` + `os.replace` (`ingesta.py:286-298`).
- Idempotencia por `(barra, fecha, hora, minuto)`.
- DST con `fold` en `comun/tiempo.py: mapa_cuartos()`.
- La alerta de formato desconocido con documento de instrucciones.

## 6. Backlog

Leyenda: `[ ]` pendiente · `[~]` en curso · `[x]` hecho

- [x] **E0.2 — Andamiaje** (2026-10-05). `uv init --package`, deps, src layout, ruff/mypy strict,
  `.gitignore`, `.gitattributes`. Code review aplicado: se quitó `[project.scripts]` que apuntaba
  a un `main` inexistente, se declaró `pytest` explícito (llegaba transitivo por `pytest-cov`), y
  se agregó la excepción `!tests/fixtures/**` al `.gitignore` (los patrones `*.csv`/`*.zip`
  globales escondían los fixtures **en silencio**).
- [x] **E0.3a — `config.py` con `SecretStr`** (2026-10-05). `cen_api_key: SecretStr | None = None`
  (opcional en la config, la exigirá el cliente al usarse). 9 tests, mypy y ruff limpios.
  `.env.example` versionado **sin valores**; `.env` local con la clave real (32 caracteres),
  que carga como `SecretStr('**********')`.
  - Errores del paso, ya en `docs/errores_verificados.md`: campos declarados **fuera** de la clase
    (anotación huérfana, que ni `mypy --strict` ni `ruff` detectan → A5); `cen_base_url` agregado
    antes de conocer su valor (YAGNI, se quitó); un valor puesto en `.env.example`, que **sí** se
    versiona (→ D3).
  - Pendiente menor: la fixture de aislamiento borra 3 variables a mano en vez de barrer el
    prefijo (→ D1). Decisión del usuario si lo cambia; los tests pasan igual.
- [~] **E0.3b — Cliente HTTP.** Timeout explícito, backoff exponencial con `tenacity`.
  Punto fino: se reintenta 5xx/429/timeout, **nunca un 403** (una clave mal configurada no se
  arregla esperando). Tests con `httpx.MockTransport`: **no tocan la red**.
  - **Paso 0 en curso:** llamada exploratoria para conocer el esquema real de la respuesta.
    Hay que reportar: URL base exacta, nombres de los parámetros, claves de un registro, si viene
    paginada, headers de rate limit y la granularidad (horaria o quinceminutal). Sin eso no se
    escribe la normalización (→ C2).
- [ ] **E0.3c — Paginación** (`page`/`limit`) y rate limit (`X-Rate-Limit`).
- [ ] **E0.4 — Normalización** API → esquema canónico, como función pura + tests.
- [ ] **E0.5 — Comparación** B vs C (y A), las 8 dimensiones de §4.
- [x] **Usuario: `user_key` obtenida** (2026-10-05). Está en `.env` como `CMGI_CEN_API_KEY`,
  32 caracteres. **Nunca pedirla por chat ni escribirla en un archivo versionado.**
- ⛔ **E0.3b/E0.3c/E0.4/E0.5 descartados (2026-10-06):** el usuario decidió **no usar la API**.
  La fuente (b) es solo la página. `config.cen_api_key` queda opcional y sin uso.
- [x] **M4 — extractor de la página** (2026-10-06). `curl_cffi` (`impersonate="chrome"`): con
  `requests` hay `SSLEOFError` y con `httpx` 403 de Cloudflare por huella TLS (→ A8).
- [x] **M4b — vigilancia** (2026-10-06). `quality/deriva.py`: adopta solo los slugs de año
  nuevos del índice; **detecta y reporta** (no adopta) nombres, miembros del ZIP, encabezados,
  convención de hora y largo del día. **Completitud:** `dia_sin_registro` (gracia 3 días) y
  `pre_sin_definitivo` (más de 15 días sin def). Reporte `data/alertas/deriva_*.md|json`.
- [x] **M5 — ingesta de la página a Silver** (2026-10-06), ver §0.6.
- [ ] **Backfill real 2025-01 → hoy.** Requiere aprobación del usuario (volumen, §0.5).
- [ ] Tarea programada de Windows para `descargar-cen` + `ingerir-pagina` + `vigilar-fuente`.
- [ ] Guía de estudio 05 ampliada con casos sobre datos reales una vez hecho el backfill.

## 7. Decisiones (ADR)

- **ADR-H01 · 2026-10-05 · `httpx` en vez de `requests`.** Motivo: timeouts explícitos por defecto
  (el default de `requests` es colgarse indefinidamente) y `MockTransport` para testear sin red ni
  librerías extra. `ppa-pipeline` §6.3 no fijaba librería HTTP.
- **ADR-H02 · 2026-10-05 · Proyecto separado, no continuación de `ppa-pipeline`.** Decisión del
  usuario. De `ppa-pipeline` se heredan convenciones y método; su backlog (H0–H12) no aplica acá.
- **ADR-H03 · 2026-10-05 · El usuario entrega la ruta.** No se automatiza el descubrimiento de
  archivos ni se exploran recursos de red por iniciativa propia.
- **ADR-H04 · 2026-10-06 · `curl_cffi` para la página del CEN.** Cloudflare filtra por huella
  TLS; `curl_cffi` presenta la de Chrome sin navegador ni JavaScript. Supera a ADR-H01 solo para
  esa fuente. Pausa mínima de 1 s entre peticiones, forzada en código.
- **ADR-H05 · 2026-10-06 · Deriva: adoptar solo lo inequívoco.** Un slug de año nuevo en el
  índice se adopta solo (el 1 de enero no requiere tocar código). Todo lo demás (nombres,
  columnas, horas) se **reporta** para revisión: adoptar un formato nuevo en automático es
  ingerir datos que nadie miró.
- **ADR-H06 · 2026-10-06 · Silver de la página = función del Bronze.** Cada mes se reescribe
  completo desde sus ZIP; no hay modo "combinar". Idempotente por construcción.
- **ADR-H07 · 2026-10-06 · Pre/def en `origen`, no en una columna nueva.** No obliga a reescribir
  los 201,8 M de filas migradas ni a leer con `union_by_name`.

## 8. Bitácora

### 2026-10-07 — Sesión 4: menú interactivo
- **AMEBA:** fuera por ahora, no descartada (§0.3).
- **M8, menú** a pedido del usuario, "como el de `CMG_Portable`, con búsqueda fuzzy y selección
  múltiple". Se portó la experiencia del legado (`CMG.py`, `core.elegir_barras()`,
  `descargar.ejecutar()`): números, `v` para volver, `quitar`, `1,3`/`todas`, previsualización y
  confirmación. Sin dependencias nuevas (`difflib`).
- **Diferencias con el legado:** entrada/salida **inyectadas** (`Consola`) → cada diálogo se
  testea con un guion de respuestas; centinela tipado `Nav.VOLVER` en vez del texto `"back"`;
  búsqueda por palabras en cualquier orden, rangos `2-5`, sin tildes/Ñ, y sin ruido de `difflib`
  cuando hay coincidencia por texto; una coincidencia única se agrega sola.
- **Sin caché del estado de arranque:** el legado lo cacheaba; aquí `resumen_base` tarda 0,3 s
  sobre 201,8 M filas porque Parquet guarda los conteos (YAGNI).
- **Funciones nuevas que el menú necesitó:** `leer.resumen_barras` (previsualización),
  `exportar.exportar_riesgo` (el CLI solo imprimía), `cen.desde_sugerido` (opción 4: lo nuevo +
  los días que siguen en `pre`), `avisar` en `deriva.sincronizar_vigilando` (avance en pantalla).
- **Hallazgo de datos:** `PENABLANCA____013` y `PEÑABLANCA____013` existen como barras
  **distintas** (también `_110`). Probablemente la misma barra con dos grafías. No se normaliza
  (regla de §0.6); queda para decisión del usuario.
- Errores propios: B8 (la normalización del legado borraba la Ñ), C8 (tests con semántica
  supuesta).

### 2026-10-06 — Sesión 3: fuente (b) completa
- **M4:** extractor de la página con `curl_cffi`; fixtures HTML reales; smoke test real
  2026-09-28..30 (4 ZIP, sha256 ok, segunda corrida 0 nuevos). Variantes de nombre reales
  `pre_260906_v2.zip` y `pre_260404-1.zip` → `RE_NOMBRE` con versión antes/después y reemisión.
- **API descartada** por decisión del usuario.
- **M4b:** `quality/deriva.py` (detección de cambios) + completitud de fechas y preliminares, a
  pedido del usuario: "que revise si un archivo pre lleva demasiado tiempo sin actualizarse, o si
  alguna fecha quedó sin registro". Línea base sobre 6 ZIP reales: solo
  `info:hora_fantasma_presente` el 09-06.
- **M5:** ingesta a Silver; la trampa de `CMG_REAL_DEF = 0` en los ZIP pre (§0.6).
- CLI: `descargar-cen`, `ingerir-pagina`, `vigilar-fuente`. 355 tests.
- Rendimiento: validar sobre una **vista** releía los CSV 6 veces (47 s → 9,1 s al materializar).
- Errores propios: A8 (declarar imposible Cloudflare sin probar la capa TLS), A9 (falsa alarma
  de base 1 en el día corto).

### 2026-10-06 — Sesión 2: se construyó el programa
- **Cambio de modo:** a pedido del usuario, Claude escribe el código y entrega guías de estudio.
  Rige la excepción de §2.1 (explicar cada parte). El modo mentor sigue vigente para lo que el
  usuario quiera retomar.
- **M1, M2, M3, M6 y M7 cerrados.** 204 tests, `mypy --strict` y `ruff` limpios.
- Migración real: 201,8 M filas, 48 meses, 213,5 MB, 3,3 min. Las validaciones reprodujeron solas
  el análisis DST del estudio E0.
- Dos aportes del usuario que mejoraron el diseño: `horas_inexistentes` con ida y vuelta por UTC
  (quedó tal cual), y la observación de que el total del día no basta → nació `horas_esperadas`,
  que permite validar hora por hora.
- Errores propios registrados en `docs/errores_verificados.md`: A6 (`os.environ` no revienta),
  A7 (`httpx` descarta el query string de la URL), C5 (concluir con 8 filas de 96), C6 (decir
  "no se puede calcular" sin intentarlo), C7 (descartar una hipótesis con un vecindario mal
  elegido). Más dos `# type: ignore` que eran síntoma de un tipo mal elegido, resueltos con
  `TypedDict`.
- **Hallazgo de negocio:** en noviembre 2024 el bloque B estuvo en cero en el 95,8% de los
  cuartos de las 1.524 barras (vertimiento solar). El bloque solar salió gratis ese mes.
- Documentación nueva: `README.md`, `docs/aprendizaje/` (4 guías + índice).
- **Próximo paso:** bajar **un** `.zip` de la página a mano para desbloquear M4/M5 (§0.6).

### 2026-10-05 — Sesión 1
- Se decidió el proyecto nuevo (ADR-H02) y el modo mentor.
- Inventario de lo que es "ingesta" en `CMG_Build/src`: ~1.450 líneas en 4 dominios
  (CMg, retiros, proyecciones, medidas de red) más `comun/`.
- Diagnóstico de anti-patrones (§5).
- Perfilado inicial del ZIP de CMg: tercera variante de formato (`BARRA`), consistencia
  USD↔CLP verificada, `HORA` 1-based (§4).
- E0.2 cerrado con code review. E0.3a en curso.
- Se creó `docs/errores_verificados.md` por instrucción del usuario, y este archivo lo referencia
  al inicio para que se lea en cada sesión.
