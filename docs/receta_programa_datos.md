# Receta: un programa de datos del mercado eléctrico, de la fuente a la app

> Cómo construir un programa como `cmg-ingesta` (fuente pública → Bronze → Silver → Gold →
> PostgreSQL → app de consulta) sin repetir los errores que costaron vueltas. Cada regla dice
> **qué hacer**, **por qué** y **cómo se comprueba**. Las referencias `A1…A20`, `B1…B9`, `C1…C8`
> y `D1…D3` apuntan a [`errores_verificados.md`](errores_verificados.md), donde está la evidencia.
>
> Escrita el 2026-10-14 con lo aprendido en las sesiones 1–10 de `cmg-ingesta`. El §14 traduce
> cada pieza a los hitos de `ppa-pipeline`.

---

## 0. El orden de construcción

```
 0. Alcance y estudio de fuentes      ← antes de escribir código
 1. Andamiaje (uv, ruff, mypy, pytest)
 2. Configuración (Settings)
 3. Dominio puro (calendario, bloques, periodo, nombres)
 4. Bronze: descargar y guardar tal cual
 5. Calidad: catálogo de formatos y reporte
 6. Silver: leer, validar, escribir atómico
 7. Gold: agregados desde el dato fino
 8. Publicar en la base (PostgreSQL / SQL)
 9. CLI, menú y operación
10. App para usuarios
```

Cada paso termina **funcionando y probado** antes de empezar el siguiente. Un paso a medias
contamina los siguientes: el error aparece lejos de donde se cometió (A20: un dato vacío entró en
el paso 6 y la carga se cayó en el paso 8).

---

## 1. Antes de escribir código

### 1.1 Alcance escrito, con lo que queda fuera

- Las fuentes, el período y lo que **no** se hace van en el `CLAUDE.md` del proyecto, con fecha y
  como decisión del usuario. Ejemplo: "CMG_DB hasta 2024-07; desde 2024-08-01 la página del
  Coordinador; **no** usar la API".
- **El usuario entrega las rutas.** No se exploran carpetas de red por iniciativa propia (ADR-H03).

### 1.2 Estudio de fuentes antes de elegir una

Si hay dos fuentes del mismo dato, se comparan **antes** de elegir, en estas 8 dimensiones:

| # | Dimensión | Qué se mira |
|---|---|---|
| 1 | Granularidad | ¿15 min, hora, día? |
| 2 | Unidad | USD/MWh, CLP/kWh, mills/kWh (= USD/MWh) |
| 3 | Convención de hora | ¿hora en base 0 o en base 1? |
| 4 | **Cambio de horario** | ¿trae el día de 25 h y el de 23 h bien? |
| 5 | Nombres | ¿la misma barra se llama igual? |
| 6 | Cobertura | ¿mismas barras y días? |
| 7 | Versión | ¿preliminar, definitivo, v2? |
| 8 | Valores | ¿coinciden número a número? |

En `cmg-ingesta` la API coincidía en todo **salvo el día de 25 horas, donde perdía la hora extra**.
Eso decidió no usarla.

- Una muestra del borde **detecta** una anomalía; no la **explica**. Para explicarla se compara el
  conjunto completo, intervalo por intervalo (C5).
- Para rechazar "existe una operación que explica esto", se **enumeran y prueban** las operaciones;
  no se argumenta sobre una muestra (C7).
- Si los datos contradicen una regla de negocio, lo primero que se sospecha es **el dato**, no la
  regla (C6).

### 1.3 El contrato de datos

Se escribe antes de la primera línea de código:

- **Clave natural** de una fila. En CMg: `(barra, fecha, hora, minuto)`. **Nunca puede ser nula**
  (A20).
- **Esquema canónico**: columnas, tipos y unidades. `DOUBLE`, no `FLOAT`: 32 bits no alcanzan para
  `208.03955`.
- **Calendario Chile**: el primer sábado de abril tiene **25 horas** (100 cuartos); la hora extra
  va como `hora = 24`, con `fecha_hora` vacía porque sería ambigua. El primer domingo de
  septiembre tiene **23 horas**: la hora que no existió se descarta, aunque la fuente la publique
  en 0 (memoria `dst-abril-sen-chile`).
- **Bloques**: A 23:00–07:59 · B 08:00–17:59 · C 18:00–22:59. Solar = B. NoSolar = los cuartos de
  A y C **juntos**.
- **Linaje**: cada fila dice de dónde salió (`origen`) y cuándo entró (`ingerido_en`).

### 1.4 El rango esperado es un contrato fijo

"Qué días debería haber" se define como constante (por ejemplo, de 2021-01-01 a hoy − 3 días), y
**nunca** se calcula a partir de lo que ya se descargó. Si se calcula desde los datos, bajar solo
los días recientes hace desaparecer todo el hueco anterior (A11). El test del escenario "con
hueco" debe esperar que el hueco **se vea**.

---

## 2. Andamiaje

```powershell
uv init --package <nombre>
uv add duckdb pandas pyarrow pydantic-settings typer tenacity
uv add --dev pytest pytest-cov mypy ruff pre-commit pandas-stubs
```

| Regla | Por qué |
|---|---|
| `mypy` en modo `strict` sobre `src` y `tests` | Atrapa errores de tipo antes de correr. Ojo: `files` exige al menos un `.py` en cada carpeta (A1) |
| Un test desde el primer día | `pytest` sin tests sale con código **5**, no 0 (A2) |
| `.gitignore` con `data/`, `.env`, `*.zip`, `*.csv`… y la excepción `!tests/fixtures/**` | Si no, los patrones globales esconden en silencio los archivos de prueba |
| `mypy` como hook **local** de pre-commit (`uv run mypy`) | El hook aislado no ve las dependencias del proyecto |
| Criterios de aceptación alcanzables en el estado en que queda la tarea | "mypy limpio y pytest verde" es imposible en un andamiaje sin tests (A4) |
| Ni `mypy` ni `ruff` reemplazan al test | Una anotación fuera de la clase no crea el campo y nadie lo detecta (A5) |

---

## 3. Configuración (`pydantic-settings`)

```python
RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="<PREFIJO>_",
        env_file=RAIZ_PROYECTO / ".env",     # anclado, no relativo (A10)
        env_ignore_empty=True,
        extra="ignore",                       # lo ajeno se ignora (A17)…
    )
    data_dir: Path = Path("data")           # relativa → se cuelga de RAIZ_PROYECTO
    silver_dir: Path | None = None          # carpetas propias (por ejemplo, en la red)
    bronze_dir: Path | None = None
    pg_url: SecretStr | None = None
    desde: str | None = None                # primer mes de la base, AAAA-MM
    # …y un model_validator que DETIENE el arranque ante una <PREFIJO>_* desconocida
```

| Regla | Error que evita |
|---|---|
| Toda ruta relativa se ancla a la raíz del proyecto; se prueba **corriendo desde otra carpeta** | "No reconoce los datos" al ejecutar desde `Scripts\` (A10) |
| Una carpeta configurada **tiene que existir**; el programa no la crea. Se revisa en **todos** los comandos que escriben | Una ruta mal escrita descargaba 23 GB a una carpeta nueva (A17) |
| `extra="ignore"` + validador que rechaza solo `<PREFIJO>_*` desconocidas | Un nombre mal escrito usaba el valor por defecto en silencio; uno ajeno rompía el arranque |
| Al renombrar una variable, el nombre viejo **avisa** (no se ignora) | `CMGI_PG_DESDE` → `CMGI_DESDE` |
| Claves con `SecretStr`, solo en `.env`; nunca en chat ni en archivos versionados | Se prueba con `str(cfg)` y con `get_secret_value()` (D2) |
| `.env.example` versionado, **sin valores** | Todo lo que va ahí es público (D3) |
| Fixture `autouse` que borra **todo el prefijo** y hace `chdir` a un temporal | Una lista a mano se desincroniza al agregar un campo (D1, A6) |
| Valores por defecto de una librería: consultarlos, no suponerlos | DuckDB desborda a `.tmp` **relativo**, no al temporal del sistema (A16) |

---

## 4. Dominio puro

Funciones sin I/O, testeadas en aislamiento: calendario (horas por día), bloques, periodos
(`"2025"`, `"2025-03"`, `"2025-03 a 2025-08"`, `"ultimos 6"`), nombres.

- Toda regla sobre horas se prueba en **los tres tipos de día**: normal, 25 h y 23 h. La regla
  "hora mínima = 1 ⇒ base 1" daba falsa alarma crítica en septiembre (A9).
- Normalizar texto con `unicodedata.normalize("NFKD")` y descartar marcas. `[A-Z]` borra la Ñ:
  `PEÑABLANCA` quedaba `PE ABLANCA` (B8).
- Las equivalencias de nombres (renombres de barras) van en una **tabla explícita**, no en una
  regla genérica: una barra nueva se revisa antes de unirla (ADR-H08).
- Antes de escribir la aserción de un test, se relee el contrato de la función que se usa como
  dato (C8).

---

## 5. Bronze: descargar y guardar tal cual

### 5.1 Llegar a la fuente

- Si un sitio bloquea, se prueban **las capas en orden**: red/proxy → **TLS** → headers →
  JavaScript → CAPTCHA. Un corte en el *handshake* (`SSLEOFError`) significa que el filtro decide
  **antes** de ver un header. `curl_cffi` con `impersonate="chrome"` lo resolvió sin navegador (A8).
- Pausa mínima de 1 s entre peticiones, **forzada en código**: el sitio es de un tercero.
- Reintentos con espera creciente solo para 429, 5xx y *timeout*. **Nunca para un 403**: una clave
  o un bloqueo no se arreglan esperando.
- Los parámetros de una URL van en un solo lugar: `httpx` descarta el *query string* si además se
  pasa `params` (A7).
- Los esquemas de una API se leen de una respuesta real o de su documentación, no se adivinan (C2).

### 5.2 Guardar sin perder nada si se corta

```
bajar → archivo.part → verificar (tamaño, testzip) → renombrar → anotar en el manifiesto → guardar manifiesto
```

- El manifiesto se guarda **después de cada archivo**. "Idempotente" (repetir da lo mismo) no es
  "retomable" (lo hecho no se pierde si se corta). Se prueba **cortando a la mitad** (A12).
- La clave del manifiesto es el **nombre** del archivo: una revisión (`def_v2_…`) tiene otro nombre
  y convive con la original, cada una con su sha256.
- Revisiones: el `sitemap.xml` del sitio dice qué páginas cambiaron (`lastmod`). Ponerse al día
  cuesta 1 + N peticiones, en vez de recorrer ~800 días (ADR-H09).
- Antes de descargar, se compara el sitio con una **línea base** (índice, marcas del HTML, un día
  de referencia). Si cambió algo estructural, se detiene sin bajar nada (ADR-H12).
- Los nombres que escribe a mano la fuente van en un **catálogo editable** (`config/*.toml`), y lo
  que no calza se anota con el texto hallado en un CSV para revisar (ADR-H11).
- Cuando dos fuentes dicen lo mismo (nombre del archivo y etiqueta de la página), se mide dónde
  chocan **antes** de elegir una. "Más regular" no es "más correcta" (A13).
- Un recurso de red a la vez. Un SMB se cae en lecturas largas (B4).

### 5.3 Si el disco no alcanza

| Opción | Cuándo | Cuidado |
|---|---|---|
| Bronze en la red (`<PREFIJO>_BRONZE_DIR`) | Hay espacio en el servidor | La primera lectura es lenta |
| Testigo en lugar del ZIP ya ingerido | No hay otro lugar | Leer, escribir y borrar en serie **es el patrón de un ransomware**: Acronis lo detuvo y restauró todo. No se esquiva: se pide una excepción a TI |

---

## 6. Calidad: catálogo de formatos y reporte

- Un catálogo de "lo conocido" (archivos dentro del ZIP, encabezados, columnas) se verifica sobre
  una muestra de **todo el período**: como mínimo, el primer y el último mes. Construido con 6 ZIP
  de 2026, rechazaba 269 de 332 de 2024 (A18). Verificado con 332 hasta 2025-01, fallaba en 318 de
  1.774 (A19). **"Verificado" se dice sobre qué conjunto.**
- **Antes de un backfill**, se corre el chequeo sobre todo lo descargado y se cuentan los críticos.
- Variantes reales que aparecieron, para no volver a suponer:
  - otro encabezado en otra época (`CMgP/CMgR [mills/kWh]` en 2024-08/09);
  - un `_` que va y viene en el nombre;
  - archivos **dentro de una carpeta** del ZIP;
  - un sufijo `_NUEVO`;
  - un `;` al final del encabezado;
  - un CSV **rellenado con líneas vacías** hasta 200.000 (A20).
- **Cuando aparece una columna nueva junto a la conocida, se comparan valores** antes de decidir cuál
  leer. Las revisiones v2 traían `CMG_REAL_DEF` (= el valor viejo) y `CMG_REAL_DEF v2` (= el
  revisado). La regla correcta fue **posicional**, "la última del encabezado", porque un archivo
  traía las etiquetas al revés (A19).
- Un formato nuevo se **verifica con datos** antes de adoptarlo: columnas viejas contra nuevas en
  días que tienen los dos formatos, y contra un archivo de control de la misma fuente.
- Se adopta en automático **solo lo inequívoco** (un año nuevo en el índice). Lo demás se reporta:
  adoptar un formato en automático es ingerir datos que nadie miró (ADR-H05).
- Severidades: **crítico** (no entra), **aviso** (entra; mirarlo), **info** (conocido).
- Cada hallazgo lleva **el archivo** donde está, y el reporte **agrupa** el mismo problema en una
  tabla. 2.267 hallazgos sueltos no se pueden leer (pedido del usuario).
- Toda cifra de un informe sale de un script con el resultado a la vista (A14). Y antes de declarar
  un bug en los datos, se revisa el criterio propio (C3).

---

## 7. Silver: leer, validar, escribir atómico

- **Silver es una función del Bronze y de las reglas de lectura.** Cada mes se reescribe completo
  desde sus archivos (ADR-H06). La huella de un mes = las reglas + el sha256 de cada archivo usado.
  Si cambia una regla, se cambia `REGLAS` y todos los meses se rehacen una vez, solos.
- **La clave nunca entra nula.** La lectura descarta las líneas totalmente vacías; una fila con
  datos y sin clave **no** se descarta: se reporta (A20).
- Filas cortas: se alinean por la derecha y se mira dónde está el hueco antes de rellenar. Pueden
  estar **corridas**, no truncadas (B2). Se cuadra el conteo de entrada y salida.
- **Escritura atómica en tres renombres**: `mes → mes__old`, `tmp → mes`, recién entonces se borra
  `__old`. Cada renombre reintenta ante `PermissionError` (antivirus, indexador). "Borrar y después
  renombrar" deja el mes sin datos si el renombre falla (B9). Se prueba **inyectando el fallo en
  cada paso**.
- La limpieza de intermedios **caros** va en la ruta de éxito, no en `finally` (B3).
- Preliminar o definitivo va en el linaje (`origen`), no en una columna nueva (ADR-H07).
- Opciones de operación: `--forzar` (todo) y `--mes AAAA-MM` (uno puntual).
- **Avance visible**: una línea por día y por etapa. Sin ella, un mes de 30 s "parece pegado" y el
  usuario corta el proceso.

---

## 8. Gold: agregados desde el dato fino

- **Todo sale de los cuartos de hora, nunca de promediar promedios.** Como cada cuarto dura 15
  minutos, `avg` sobre los cuartos de un bloque ya es el promedio ponderado por tiempo.
- NoSolar = `avg` de los cuartos de A **y** C juntos. No es `(A + C) / 2`.
- Riesgo nodal = CMg(comparada) − CMg(referencia), cruzando por `(fecha, hora, minuto)` con
  `INNER JOIN`, para no inventar ceros. El % por cuarto queda vacío si la referencia vale 0 (el
  24,7 % de los CMg son 0); el % del resumen se calcula sobre los promedios.
- Si la misma regla existe en dos lugares (DuckDB y PostgreSQL, o Python y SQL), **un test exige
  que den lo mismo** (`rel=1e-12`), y se comprueba también con datos reales. En `cmg-ingesta` la
  diferencia fue 0.

---

## 9. Publicar en la base (PostgreSQL)

| Pieza | Cómo | Por qué |
|---|---|---|
| Esquema | Un `.sql` **idempotente** que corre el administrador una vez | Se puede volver a correr sin romper nada |
| Usuarios | `<x>_carga` (dueño del esquema, escribe) y `<x>_lectura` (solo `SELECT` + `ALTER DEFAULT PRIVILEGES`) | Mínimo privilegio; las claves se ponen con `\password`, nunca en el archivo |
| Tabla grande | Particionada por mes (`RANGE (fecha)`), clave primaria por partición | La consulta de una barra en un periodo tarda ~12 ms |
| Carga | `COPY` a `tabla_AAAA_MM_nuevo`, clave, y en una transacción corta: comparar con el mes viejo, `DETACH` del viejo y `ATTACH` del nuevo | La consulta nunca ve un mes a medias; si se corta, se retoma |
| Qué subir | sha256 de cada mes de Silver en una tabla de control | Solo se sube lo que cambió |
| Revisiones | Al reemplazar un día ya cargado, guardar cuántos cuartos cambiaron y cuánto | Responde "qué se revisó y cuándo" |
| Dimensiones | Insertar **solo las que faltan** | `ON CONFLICT DO NOTHING` gasta la secuencia en cada carga; un `smallint` se agotaría en ~20 meses |
| Validar lo que llega | Antes de tocar la base: Silver sin claves nulas, Silver al día con Bronze | Un dato malo se rechaza con un mensaje, no con un `TypeError` (A20) |
| Rango | `<PREFIJO>_DESDE` + `--quitar-anteriores` (todo en una transacción) | La base guarda solo lo que se usa |

Otras reglas:

- Las tablas se crean **en el servidor del usuario y por el usuario**. Claude prueba contra un
  PostgreSQL portable local, en otro puerto, con una base temporal por test.
- Si una clave aparece en el chat, se recomienda rotarla y no se repite.

---

## 10. CLI, menú y operación

- **Códigos de salida**: 0 bien · 1 no se pudo cumplir · 2 comando mal escrito · 3 terminó con
  hallazgos. `click` ya usa el 2 para sus errores de uso: se prueba **invocando el comando mal
  escrito** (A15).
- Mensajes que dicen **qué hacer**: "Corre primero: `cmg ingerir-pagina --mes 2025-01`".
- Procesos largos: retomables, con avance por unidad, y "si lo cortas con Ctrl+C no se pierde nada"
  tiene que ser verdad (probado cortándolo).
- Un menú interactivo para el uso diario; los comandos, para automatizar.
- Vigilancia sin descargar (`vigilar-fuente`), pensada para una tarea programada; sale con 3 si hay
  algo que revisar.
- No borrar ni descargar a Bronze sin que el usuario lo pida. **No hacer push sin que lo pida.**

---

## 11. App para usuarios (ejecutable en la carpeta compartida)

| Decisión | Detalle |
|---|---|
| Arquitectura | Directo a la base con un usuario **de solo lectura** + sesión `default_transaction_read_only`. Sin API mientras no haga falta |
| Credenciales | `conexion.ini` **junto al .exe**, no compilado dentro: se cambia sin recompilar; no se versiona |
| Cálculo | En la base (`avg FILTER`, `JOIN`): viaja solo el resultado |
| Separación | La ventana solo junta lo elegido; la lógica (`datos`, `informes`) se prueba sin abrirla |
| Búsqueda de barras | Aproximada por palabras (prefijos, números sin ceros, abreviaturas del CEN como `C.NAVIA` y `STA`, tipeos). **Siempre 10 opciones con su %; nunca elige sola** (pedido del usuario) |
| Versión del dato | Cada fila dice "Definitivo v2" / "Preliminar v1"; la hoja Info avisa si hay días preliminares |
| Excel | Fechas como fechas (`=MES()` funciona), encabezados con unidad, formato numérico, encabezado fijo, hoja Info |
| Ventana | `tkinter` (viene con Python); la consulta en un hilo, con su propia conexión |
| Empaquetado | PyInstaller `--onedir --windowed`, excluyendo lo que no usa (DuckDB, etc.); el script de build **se detiene si PyInstaller falla** (PowerShell no se detiene solo ante un programa externo) |
| Diagnóstico | Modo `--prueba` sin ventana que escribe un log; prueba de humo que abre la ventana real |
| Desde la red | El `.exe` directo tarda **51–65 s** (lee ~1.700 archivos por SMB). Lanzador `.cmd` con `robocopy /MIR` a `%LOCALAPPDATA%`: **3–5 s** desde la segunda vez, y las actualizaciones llegan solas |
| Firma | Probar primero: desde la intranet, SmartScreen suele no avisar. Si hace falta, el certificado de la empresa (TI) |

Al publicar en la red: copiar **solo** la carpeta final (`dist`), nunca la de compilación
(`build`), que trae un `.exe` intermedio que no funciona.

---

## 12. El entorno corporativo (Windows, red, antivirus)

| Fenómeno | Qué hacer |
|---|---|
| `PermissionError` al renombrar o borrar | Reintentar con espera; si sigue trabado, dejar el archivo y seguir |
| Antivirus de comportamiento (Acronis) | No imitar el patrón leer-escribir-borrar en masa; si hace falta, excepción de TI. Nunca esquivarlo |
| Red SMB lenta (~0,6 MB/s) | Medir antes de diseñar; leer local lo que se lee mucho |
| Rutas con tildes (`Información`) | Los scripts van a un **archivo** UTF-8; pegarlos en la terminal rompe la `ó` |
| `\b`, `\n` en scripts escritos desde Python | Usar `/` en rutas de PowerShell; revisar el archivo generado |
| Saltos de línea CRLF/LF mezclados | Editar respetando el estilo de cada archivo |
| Poco disco | Medir (`df`), no suponer; ofrecer red o testigos |
| Dos procesos sobre la misma base | Una carga a la vez; avisar antes de correr algo que compite |

---

## 13. Método de trabajo con Claude

- **Cada error descubierto se escribe** en `errores_verificados.md` **antes de seguir**, con
  evidencia, corrección y lección (memoria `registrar-errores-en-md`).
- Se verifica el disco antes de afirmar qué existe; la documentación describe la intención (C1).
- Un test que arma el escenario con el problema debe esperar que **el problema se vea**. Si no, el
  test fija el error (A11).
- Al explicar: primero la respuesta literal, un concepto por vez, antes/después con tabla, y
  preguntas que se puedan responder (C4; memoria `explicar-codigo-a-claudio`).
- Las decisiones de negocio y de alcance son del usuario: se le presentan opciones con su costo y
  una recomendación.
- La bitácora del `CLAUDE.md` registra cada sesión: qué se decidió, qué se midió, qué falta.

---

## 14. Cómo aplicarlo en `ppa-pipeline`

`ppa-pipeline` ya tiene la arquitectura medallion, los principios (§6.2) y los hitos H0–H12. Esta
tabla dice qué pieza de `cmg-ingesta` resuelve cada hito y qué cambia:

| Hito `ppa-pipeline` | Pieza de `cmg-ingesta` que se reusa | Qué cambia |
|---|---|---|
| H2 Config | §3 completo: anclar rutas, carpetas que deben existir, prefijo `PPA_`, desconocidas, `SecretStr`, fixture D1 | Nada esencial |
| H2.3 CLI | §10: códigos 0/1/2/3 probados con el comando mal escrito | Hoy usa 0/1: definir el 2 (uso) y el 3 (hallazgos) |
| H3 Bronze (Plabacom ZIP, `.mdb`) | §5.2: `.part` → verificar → renombrar → manifiesto por archivo; línea base; catálogo editable de nombres | La fuente es otra (Plabacom, URLs prefirmadas); el estudio del §1.2 va primero, con **los 12+ meses** del período |
| H4 Silver retiros | §7: función del Bronze, huella con `REGLAS`, clave no nula, escritura atómica de 3 renombres, B2 (filas corridas) | La clave es (cliente/medidor, fecha, hora, cuarto) y la medida es `medida_3` (RN-12) |
| H5 Silver CMg | **No volver a ingerir CMg**: leer `cmg.cmg_15min` de la base `cmg_cen` con un usuario de lectura | RN-14: CMg horario = promedio de los 4 cuartos; la valorización es cuarto a cuarto y se suma por hora (RN-13) |
| H6 Calidad | §6: catálogo verificado sobre todo el período, severidades, hallazgo con archivo, reporte agrupado, chequeo previo al backfill | Agregar `pandera` donde el contrato lo pida |
| H7 Homologación | §4: tabla explícita de equivalencias + normalización NFKD; búsqueda aproximada del §11 | Empresas y RUT en vez de barras |
| H8 Gold SQL | §8 (desde el dato fino, test de igualdad entre implementaciones) y §9 (cargar por periodo con intercambio atómico, sha256, revisiones, usuarios carga/lectura, insertar solo dimensiones nuevas) | Azure SQL: `SWITCH PARTITION` o tabla de staging + transacción en vez de `ATTACH`; `BULK INSERT`/`bcp` en vez de `COPY` |
| H9 Operación | §10 y §12: retomable, avance visible, vigilancia con código 3, tarea programada | Task Scheduler / GitHub Actions |
| H10 Power BI | Gold con fechas reales y unidades en el nombre | — |
| App para usuarios | §11 completo | Si el consumidor es Power BI, puede no hacer falta |

**Reglas de negocio de `ppa-pipeline` que chocan con esta receta, a decidir con el usuario:**

- RN-04 "las horas sin medición se rellenan con cero": en CMg se decidió **no inventar** datos
  (una hora faltante es un hueco que se reporta). Para energía, un cero puede ser válido, pero
  conviene marcarlo (columna `relleno = true`) para que el Gold distinga "cero medido" de "sin dato".
- RN-03 "la hora faltante de septiembre no se considera" coincide con la regla del §1.3: la hora
  que no existió se descarta.
- RN-02 "la hora extra de abril va al bloque A" coincide: `hora = 24` cae en A.

**Orden sugerido en `ppa-pipeline`:** cerrar H2.3 con los códigos 0/1/2/3 → estudio de fuentes del
balance (§1.2) sobre todo el período → H3 con la descarga retomable del §5.2 → H4 con el §7 → H5
leyendo CMg desde `cmg_cen`.

---

## 15. Listas de chequeo

### Antes de un backfill
- [ ] Catálogo de formatos verificado sobre **todo** el período (primer y último mes, como mínimo).
- [ ] Chequeo de formatos corrido sobre todo lo descargado: 0 críticos, o cada crítico explicado.
- [ ] Espacio en disco medido; destino definido (local o red).
- [ ] La descarga se probó cortándola a la mitad.

### Antes de publicar en la base
- [ ] Silver sin claves nulas y al día con Bronze.
- [ ] Usuarios carga/lectura con clave puesta por `\password`.
- [ ] Rango (`DESDE`) decidido; meses viejos quitados si corresponde.

### Antes de entregar la app
- [ ] `.exe` probado en modo `--prueba` y con la ventana real.
- [ ] Resultados iguales al Gold del programa, con datos reales.
- [ ] En la red solo `dist`, con `conexion.ini` y el lanzador.
- [ ] Probado en el equipo de otra persona, desde la red.

### Al cerrar cada tarea (definición de terminado)
- [ ] `ruff`, `mypy --strict` y `pytest` en verde; los tests de base corridos con la base de prueba.
- [ ] Probado contra datos reales, no solo sintéticos.
- [ ] Errores de la sesión registrados (A/B/C).
- [ ] Bitácora y README actualizados.
- [ ] Commit y push **solo si el usuario lo pide**.
