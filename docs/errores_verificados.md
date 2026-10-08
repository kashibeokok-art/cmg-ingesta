# Errores verificados (para que Claude no los repita)

> Cada entrada es un error **que Claude cometió** en este proyecto o en el código que se está
> portando, con la evidencia que lo desmintió. No son teorías: todos se comprobaron ejecutando.
> **Regla:** cuando Claude descubra que se equivocó, agrega la entrada aquí antes de seguir.

Formato: qué afirmé → qué pasa de verdad → evidencia → lección.

## Documento compartido

Este archivo se usa en **`cmg-ingesta`** y en **`ppa-pipeline`**. Los dos proyectos comparten
stack (`uv`, `ruff`, `mypy --strict`, `pytest`, `pydantic-settings`) y modo de trabajo (mentor),
así que los mismos errores aplican en ambos.

Al escribir una entrada nueva, redactarla de forma **portable**: donde el prefijo de las variables
de entorno importe, usar `<PREFIJO>_` en vez del valor concreto, que es `CMGI_` en `cmg-ingesta`
y `PPA_` en `ppa-pipeline`. Si una entrada aplica solo a un proyecto, decirlo explícitamente.

---

## A. Herramientas (mypy, pytest, ruff, PowerShell)

### A1. mypy con `files` NO acepta una carpeta vacía
- **Afirmé:** "una carpeta vacía sí le sirve a mypy; no encuentra `.py` ahí y sigue".
- **Realidad:** falla con `There are no .py[i] files in directory 'tests'`.
- **Evidencia:** `uv run mypy` con `files = ["src", "tests"]` y `tests/` recién creada vacía (2026-10-05).
- **Lección:** `files` es una lista de rutas **obligatorias**, cada una con al menos un `.py`.
  Un andamiaje con esta config no puede estar verde hasta que exista el primer test.

### A2. pytest sin tests devuelve exit 5, no 0
- **Afirmé:** "pytest con 0 tests, exit 0".
- **Realidad:** exit **5** = `no tests collected`. Es un código propio, distinto de 1 (fallos),
  para que un CI no pase en verde cuando los tests desaparecieron.
- **Evidencia:** `uv run pytest -q` → `no tests ran in 0.01s`, exit 5 (2026-10-05).
- **Lección:** no prometer "exit 0" en un criterio de aceptación sin tests. Y si alguna vez
  se quiere tolerar, es `--exitfirst`/`-p no:cacheprovider`… no: es `pytest ... || exit 0`
  explícito, pero mejor es simplemente tener un test.

### A3. `Select-String -SimpleMatch` desactiva la regex
- **Afirmé (implícitamente):** que `-Pattern "a|b|c" -SimpleMatch` buscaría cualquiera de los tres.
- **Realidad:** `-SimpleMatch` busca el **literal** `a|b|c`, pipes incluidos. No encontró nada y
  casi concluí que los bloques del `pyproject.toml` faltaban, cuando estaban todos.
- **Evidencia:** la búsqueda no devolvió nada; `Read` del archivo mostró los 5 bloques (2026-10-05).
- **Lección:** para alternativas, **no** usar `-SimpleMatch`. Y cuando una búsqueda devuelve
  vacío, confirmar leyendo el archivo antes de afirmar que algo no está.

### A4. Criterios de aceptación autocontradictorios
- **Error:** pedí "`mypy` limpio y `pytest` en verde" para un andamiaje **sin tests**, lo que
  esta configuración vuelve imposible (ver A1 y A2).
- **Lección:** antes de escribir criterios de aceptación, verificar que sean alcanzables en el
  estado en que va a quedar el proyecto al terminar esa tarea.

### A5. Ni `mypy --strict` ni `ruff` detectan una anotación huérfana
- **Riesgo de afirmar lo contrario:** es natural suponer que `mypy --strict` atrapa un campo
  declarado fuera de la clase. **No lo hace.**
- **Caso:** en `config.py`, `cen_api_key: SecretStr | None` escrito a nivel de módulo (sin indentar,
  fuera de `class Settings`) y sin `= valor`.
- **Realidad:** una anotación sin asignación **no crea ninguna variable**; solo agrega una entrada a
  `__annotations__`. Dentro del cuerpo de una clase pydantic eso sí define un campo (pydantic lee
  `__annotations__`), pero a nivel de módulo no define nada y nadie la lee.
- **Evidencia (2026-10-05):** `mypy --strict` → `Success: no issues found in 7 source files`;
  `ruff check` → `All checks passed!`; pero `Settings.model_fields` → `['data_dir', 'log_level']`
  y `config.cen_api_key` → `AttributeError`.
- **Lección:** *Defense in Depth* — el linter y el chequeo de tipos no sustituyen al test. Lo único
  que atrapa esto es una aserción sobre `model_fields` o sobre el comportamiento. Mismo patrón que
  el `logging.warninr` y la función duplicada de `OLD/` en `ppa-pipeline` §5.2: ninguna herramienta
  atrapa todo.

### A6. `os.environ` NO revienta al borrar mientras se recorre
- **Afirmé:** que en `for clave in os.environ: del os.environ[clave]` el `list()` era obligatorio
  porque "sin la copia reventaría".
- **Realidad:** no lanza nada. `os.environ` no es un `dict`, es un `os._Environ`, y su `__iter__`
  ya copia las claves por dentro: `keys = list(self._data)` (con el comentario de CPython
  *"list() from dict object is an atomic operation"*). Un `dict` normal **sí** lanza
  `RuntimeError: dictionary changed size during iteration`.
- **Evidencia (2026-10-05):** el bucle sin `list()` sobre `os.environ` borró las 4 variables sin
  error; el mismo patrón sobre `{"a":1,"b":2,"c":3}` lanzó `RuntimeError`.
- **Lección:** `list(os.environ)` es defensivo y legible, pero **no** es obligatorio, y la razón
  que yo daba era falsa. No justificar una práctica con un mecanismo sin verificarlo: el tipo
  real de un objeto de la stdlib puede no ser el que uno asume (`os.environ` no es `dict`,
  `Path` no es `str`, etc.).

---

### A7. `httpx` DESCARTA el query string de la URL si se le pasa `params`
- **Afirmé:** que una URL con `?user_key={APIKey}` más un `user_key` en `params` enviaría el
  parámetro **duplicado**.
- **Realidad:** `httpx` **reemplaza** por completo el query string de la URL con el de `params`.
  Se envía uno solo, el de `params`. No hay duplicación.
- **Evidencia (2026-10-05):** `httpx.Request("GET", url_con_query, params=...)` →
  `url.params.get_list("user_key")` devuelve **una** entrada, la de `params`, y el `{APIKey}`
  literal de la URL desaparece.
- **Lección:** la trampa es la inversa de la que yo anuncié: cualquier parámetro escrito en la
  constante de la URL **se pierde en silencio** al pasar `params`. Los parámetros van en un solo
  lugar, y conviene que sea `params`. Y antes de afirmar cómo se combinan dos fuentes de datos
  en una librería, construir la petición y mirarla (sin enviarla).

### A8. Declarar "imposible" un bloqueo de Cloudflare sin probar la capa TLS
- **Afirmé:** que los `.zip` y las páginas del Coordinador "no se pueden descargar
  programáticamente, punto", que "no es un header que falte" y que hacía falta resolver el desafío
  JavaScript (o sea, un navegador). Lo repetí en varios mensajes y lo dejé escrito en `CLAUDE.md`.
- **Realidad:** el filtro era por **huella TLS** (JA3/JA4), no por JavaScript. `curl_cffi` con
  `impersonate="chrome"` obtiene **200** en el índice, en la página del día (con los 2 ZIP
  esperados) y en el ZIP (12,2 MB, `application/zip`, firma `PK\x03\x04`). Sin navegador.
- **La pista estaba a la vista:** `requests` puro no recibía un 403 sino un **corte del handshake
  TLS** (`SSLEOFError: UNEXPECTED_EOF_WHILE_READING`). Un corte en la negociación TLS significa que
  el servidor decide **antes** de ver un solo header. Cambiar el User-Agent nunca podía servir.
- **Evidencia (2026-10-06):** `scratchpad/probar_cffi.py`.
- **Lección:** antes de declarar algo imposible, enumerar las capas donde puede estar el filtro
  (red/proxy → TLS → headers → JavaScript → CAPTCHA) y probar cada una. Probé red y headers, me
  salté TLS, y concluí "JavaScript" por eliminación incompleta. **Costó varias vueltas al usuario,
  que tuvo que insistir en que "debe haber formas de hacerlo".** Tenía razón.

### A9. Una regla derivada del día normal da falsa alarma en el día DST
- **Error:** para detectar archivos con la hora en base 1, usé `hora mínima == 1 y no hay hora 0`.
  Sirve un día normal (1..24 vs 0..23), pero **el domingo de 23 horas los datos correctos empiezan
  en la hora 1**, porque la hora 0 no existió. La regla los marcaba como críticos.
- **Peor:** ese día la convención es **indistinguible**: base 0 con la hora 0 faltante (1..23) y
  base 1 (1..23) dan el mismo conjunto. No hay regla que lo resuelva; solo se puede no evaluarlo.
- **Evidencia (2026-10-06):** lo atrapó `test_dia_corto_sin_la_hora_fantasma_tambien_se_informa`
  antes de entregar. Corregido: la regla ahora es `hora máxima == horas del día`, y no se aplica el
  día corto.
- **Lección:** toda regla sobre horas se prueba sobre **los tres tipos de día** (normal, 25 h,
  23 h) antes de darla por buena. Los casos borde del calendario son justamente donde las reglas
  "obvias" fallan, y una falsa alarma crítica en el día DST es exactamente lo que hace que un
  vigilante se ignore.

### A10. Una ruta relativa en la configuración depende de desde dónde se ejecuta
- **Error:** dejé `data_dir: Path = Path("data")` y `env_file=".env"`, ambos **relativos a la
  carpeta actual**. Todos los tests pasaban porque siempre corrían desde la carpeta del proyecto
  (o hacían `chdir` a un temporal).
- **Evidencia (2026-10-08):** el usuario corrió `uv run cmg` y "no reconoce los datos". Desde
  `cmg-ingesta\` → 201.810.260 filas; desde `Scripts\` → "La base esta vacia. Esperada en:
  data\silver\cmg". Desde otra carpeta **ni siquiera se leía el `.env`**.
- **Corrección:** `RAIZ_PROYECTO = Path(__file__).resolve().parents[2]` (válido porque `uv sync`
  instala en modo editable); `env_file = RAIZ_PROYECTO / ".env"`; un `field_validator` cuelga de la
  raíz toda ruta relativa y respeta las absolutas. Verificado desde 3 carpetas distintas.
- **Efecto colateral que hubo que cubrir:** con el `.env` anclado al proyecto, cambiar de carpeta
  **ya no aislaba** los tests del `.env` real (que tiene la clave). Se apagó para todos con una
  fixture `autouse` en `conftest.py`.
- **Lección:** cualquier ruta que lea un programa de línea de comandos hay que probarla
  **ejecutando desde otra carpeta**. Un test que siempre corre desde la raíz no puede detectarlo.
  En `ppa-pipeline` aplica igual a `Settings`.

### A11. Calcular "lo esperado" a partir de lo que ya hay esconde los huecos
- **Error:** tres lugares medían la completitud contra los propios datos:
  `vigilar_fuente` revisaba desde el **primer día del manifiesto** (y con el manifiesto vacío no
  revisaba nada); `desde_sugerido` sugería desde el **día siguiente al último descargado**; y
  `cmg estado` mostraba **primer y último mes** ("2021-01 a 2026-10 (70 meses)").
- **Evidencia (2026-10-08):** el usuario bajó solo 2026-10-04..06. Silver quedó con 2021–2024 +
  esos 3 días. `vigilar_fuente` → **0 hallazgos**; el menú sugería **2026-10-04**; `estado`
  decía 70 meses teniendo 49. Los 641 días de 2025-01-01 a 2026-10-03 no existían para el programa.
  El reporte del usuario: "al añadir días recientes deja de saber que hay días que faltan".
- **Agravante:** los tests que escribí **fijaban el error**: armaban manifiestos con un hueco
  desde 2025 y esperaban que se ignorara (`test_desde_sugerido_sigue_despues_del_ultimo_dia`,
  `test_vigilar_fuente_revisa_la_completitud_de_todo_el_manifiesto`).
- **Corrección:** el rango esperado es un **contrato fijo** (2021-01-01 → hoy − 3 días; la
  página desde `INICIO_FUENTE`), nunca derivado de los datos. `quality/cobertura.py` lo mide en
  Silver y clasifica cada día faltante (maestro / sin descargar / sin ingerir). Regresión con el
  caso real en `test_cobertura.py::test_el_caso_real_del_usuario`.
- **Lección:** una validación de completitud necesita una expectativa **independiente** del
  dato que valida. Es el mismo principio de §3 de la guía 03 ("validar contra una expectativa"),
  que yo mismo había documentado, aplicado al calendario de días en vez de al de cuartos. Y un
  test que arma el escenario "con hueco" debe esperar que el hueco **se vea**.

---

## B. Procesamiento de datos (del código que se está portando)

### B1. El límite de una bisección no es el borde: hay que refinarlo
- **Error:** en `preparar_medidas.py`, `fin_contenido()` devolvía `lo`, el último bloque de 256 KB
  **que todavía tenía datos**. El contenido real seguía hasta 256 KB más allá del corte: ~1.300
  filas por archivo que se habrían perdido en silencio.
- **Evidencia:** la verificación estricta abortó con "contenido real DESPUÉS del corte (1 muestra)".
  Tras refinar byte a byte, los 5 archivos verifican limpio (2026-09-22).
- **Lección:** una bisección con bloques entrega una **cota inferior**, no el borde. Si el borde
  importa, refinar dentro de la ventana. Y la verificación estricta sirvió: no quitarla.

### B2. Filas cortas pueden estar **corridas**, no truncadas
- **Error:** supuse que 5 filas con 22/23 campos en vez de 24 les faltaban las columnas finales,
  e iba a usar `null_padding=true`.
- **Realidad:** les faltaba una columna **del medio** (`medida_3`, justo la energía). Alineando por
  la derecha, `C_FIS` caía en `Nombre_Corto` y el RUT en `Razon_Social`. Rellenar al final habría
  puesto números en la columna equivocada, sin error.
- **Evidencia:** volcado campo por campo de las 5 filas contra el encabezado (2026-09-22).
- **Lección:** antes de rellenar, **alinear por la derecha** y verificar dónde está el hueco.
  `null_padding` solo es seguro si se comprobó que faltan las columnas finales.
  Corolario: descartar + reportar + **cuadrar el conteo** (`filas_csv - descartadas == filas_parquet`).

### B3. No borrar trabajo caro en la ruta de error
- **Error:** el `finally` de `preparar()` borraba el staging, así que un fallo en el paso de parquet
  destruyó 1,2 GB ya descargados por red (3,5 min de transferencia).
- **Lección:** la limpieza de artefactos intermedios **caros** va en la ruta de éxito, no en
  `finally`. Con manifiesto (`total`, `fin`, `escritos`) la segunda corrida los reutiliza.

### B4. No leer el recurso de red en paralelo con una copia larga
- **Error:** mientras corría una copia de varios minutos desde `\\SAFIRA_CL1`, leí el encabezado de
  otro archivo del mismo share. Las dos operaciones murieron a la vez (`OSError 22`, "error de red
  inesperado").
- **Lección:** una sola operación contra el share a la vez. Y asumir que un SMB se cae en lecturas
  largas: reintentos que **retomen desde el último byte**, no que reinicien.

### B5. Ventanas de 12 meses: poner el límite superior
- **Error:** `WHERE fecha >= DATE '2025-08-01'` sin cota superior dio **13 meses** y sesgó el perfil
  estacional (el mes repetido contaba doble).
- **Lección:** una ventana móvil lleva las dos cotas, y verificar con
  `count(DISTINCT anio*100+mes) = 12`.

### B6. Unidades: no mezclar bytes con MB
- **Error:** reporté "159.144.095x más chico" al dividir bytes por MB. El factor real era 152x.
- **Lección:** convertir **una vez** a la unidad de salida y operar ahí.

### B7. Un nombre de cliente no es su rubro
- **Error:** `FARELLONES` encabezaba la búsqueda de frigoríficos por estacionalidad, pero son
  18,9 MW medios en 220 kV: es minería, no andariveles.
- **Lección:** validar con la **forma** (perfil, factor de carga, nivel de tensión), no con el nombre.
  Y los artefactos de cobertura parcial (`max/min = inf`, energía solo en un mes) se ven como
  estacionalidad extrema: filtrar `min(mes) > 0` antes de rankear.

### B8. Portar la normalización del legado borraba la Ñ
- **Error:** porté `core._normaliza()` tal cual para la búsqueda de barras del menú:
  `re.sub(r"[^0-9A-Za-z]+", " ", texto.upper())`. La `Ñ` no está en `A-Z`, así que caía en el
  separador: `PEÑABLANCA____013` quedaba como `PE ABLANCA 013`.
- **Evidencia (2026-10-07):** al probar el menú contra la base real, buscar "penablanca" no traía
  `PEÑABLANCA`. Y la base tiene **las dos grafías como barras distintas** (`PENABLANCA____013` y
  `PEÑABLANCA____013`), así que el usuario veía solo la mitad sin saberlo.
- **Arreglo:** descomponer con `unicodedata.normalize("NFKD", ...)` y descartar las marcas
  (`Ñ` = `N` + tilde) antes de limpiar separadores.
- **Lección:** un patrón `[A-Z]` en español es un bug esperando datos. Al portar, probar con
  nombres reales que tengan Ñ y tildes, no solo con los de los tests sintéticos.


### B9. "Borrar el viejo y después renombrar el nuevo" no es atómico
- **Error:** en `silver/escribir.py: escribir_particion` (sesión 2) el paso final era
  `shutil.rmtree(destino)` y luego `tmp.replace(destino)`, y el docstring lo llamaba "atómico".
  Entre las dos líneas el mes **no existe**; si el renombre falla, queda sin datos.
- **Evidencia (2026-10-08):** al re-migrar el histórico, `os.replace` falló en `2021-10` con
  `PermissionError: [WinError 5] Acceso denegado` (antivirus/indexador, el mismo fenómeno que la
  sesión 5 vio en el manifiesto). Resultado: `anio=2021/mes=10` borrado y `mes=10__tmp` con los
  datos. No se perdió nada solo porque la temporal estaba completa y la migración es re-ejecutable.
- **Origen:** el legado hace lo mismo (`CMG_Build/src/ingesta.py:295-298`: `rmtree(pdir)` y luego
  `os.replace`), y en la sesión 1 lo catalogué en CLAUDE.md §5 como "lo que sí se conserva porque
  ya está bien". Se portó el defecto junto con el patrón.
- **Por qué los tests no lo vieron:** `test_un_fallo_deja_intacto_el_mes_anterior` simulaba un
  fallo **al escribir** la temporal (paso 1), nunca **al renombrar** (paso 3).
- **Corrección:** intercambio en tres renombres: `destino → destino__old`, `tmp → destino`, y
  recién al final se borra `__old`. Si el segundo renombre falla, `__old` vuelve a su lugar. Cada
  renombre reintenta ante `PermissionError`. `limpiar_temporales` restaura un `__old` huérfano si
  el mes falta (proceso muerto justo entre dos renombres).
- **Lección:** "atómico" se prueba inyectando el fallo **en cada paso**, no solo en el primero.
  En Windows un renombre puede fallar por un proceso ajeno, así que el paso que "no puede fallar"
  es justamente el que hay que simular.
---

## C. Método de trabajo

### C1. Verificar el estado del repo antes de afirmar qué falta
- En `ppa-pipeline`, `CLAUDE.md` decía que los esqueletos de H2.3 estaban creados. En disco **no
  existían** `cli.py`, `domain/periodo.py` ni sus tests, y `typer` no estaba en `pyproject.toml`.
- **Lección:** la documentación del proyecto describe la intención; el disco describe la realidad.
  Verificar antes de planificar sobre ella.

### C2. No inventar esquemas de APIs
- Pendiente activo: el host y los nombres de campo de `costo-marginal-real/v4` **no están
  confirmados**. `ppa-pipeline` §4.2.1 registra el path y los parámetros, no la respuesta.
- **Lección:** el mapeo de la respuesta sale de la documentación del portal o de una respuesta
  real. Si se adivina, la normalización trabaja sobre campos que no existen.

### C3. El umbral equivocado puede ser el mío, no el dato
- Dos veces una "anomalía" era mi criterio: una desviación de 7,6e-6 era precisión de float32, y
  un conteo de filas "incompleto" era mi fórmula (2 columnas basura con 27 valores, no un año).
- **Lección:** antes de declarar un bug en los datos, verificar el criterio a mano con un caso.

### C7. Descartar una hipótesis con un argumento sobre un vecindario mal elegido
- **Error:** para descartar que la hora 22 de la API fuera un promedio, argumenté que
  "un promedio no puede exceder a todos sus insumos" y que el valor (214.21126) superaba al máximo
  de las horas vecinas (208.24630). El razonamiento es válido; **el vecindario estaba mal elegido**:
  solo miré las horas 21–24, y la hora **20** vale 217.69248, más alta. La hipótesis **no** quedaba
  descartada.
- **Cómo se resolvió bien:** búsqueda sistemática de 64 combinaciones (promedios de pares y triples
  de las horas 18–24, más ponderados), tolerancia 1e-3 → **0 de 16 casos** reproducidos.
  Recién ahí quedó descartada.
- **Evidencia (2026-10-05):** `scratchpad/probar_operaciones.py`.
- **Lección:** un argumento analítico correcto sobre datos incompletos da una conclusión falsa, y
  suena más convincente que una duda. Cuando la hipótesis es "existe alguna operación que explica
  esto", la forma honesta de rechazarla es **enumerar y probar**, no argumentar sobre una muestra
  del vecindario. Y la hipótesis del usuario (que conoce el dominio) merece el test, no la réplica.

### C5. Concluir un patrón mirando 8 filas de 96
- **Error:** afirmé que "la API descarta la primera hora 23 y etiqueta la hora 24 como 23" después
  de comparar **solo los últimos 8 registros** de un día de 96. El calce de `hra=23` con `hora 24`
  era real, pero la **hora 22 tampoco calzaba** (208.03955 vs 210.07436), así que el desajuste
  empezaba antes y mi explicación era incompleta. Quedó escrita como hecho en `CLAUDE.md` y hubo
  que marcarla como no concluida.
- **Lección:** una muestra del borde sirve para **detectar** una anomalía, nunca para
  **caracterizarla**. Para explicar el patrón hay que traer el conjunto completo y comparar
  intervalo por intervalo. Y mientras no esté, escribirlo como hipótesis marcada, no como hallazgo.

### C6. Decir "no se puede calcular" sin intentar calcularlo
- **Error:** al ver que los días con `hora=24` eran unos viernes y otros sábados, concluí que
  "no siguen un patrón confiable, hay que detectar el día en vez de predecirlo".
- **Realidad:** el usuario aportó la regla de negocio (primer sábado de abril) y
  `zoneinfo("America/Santiago")` la confirma **6 de 6 años**. El cálculo es perfectamente
  confiable; lo que estaba inconsistente eran las **etiquetas de fecha de los datos**
  (3 de 6 años corridos un día).
- **Lección:** distinguir "la regla no existe" de "los datos no la cumplen". Si los datos
  contradicen una regla, la hipótesis por defecto es que **los datos tienen un defecto**, no que
  la regla no sea computable. Y el diseño correcto suele ser **calcular + validar**, no reemplazar
  el cálculo por una detección heurística.

### C8. Escribir el test con la semántica supuesta, no con la documentada
- **Error (2026-10-07), dos veces en la misma tarea:**
  1. En un test del menú usé `"2024-06"` creyendo que era "solo junio". `domain/periodo.py` dice
     en su docstring que es **"desde 2024-06 hasta el último disponible"**, y lo había leído
     minutos antes.
  2. Sembré dos barras en el mismo mes con dos llamadas a `sembrar`, sin recordar que escribir un
     mes **reemplaza la partición entera** (ADR-H06): quedaba solo la segunda barra, y el riesgo
     nodal no tenía intervalos comunes.
- **Evidencia:** los dos tests fallaron al primer intento; el código estaba bien.
- **Lección:** antes de escribir la aserción, releer el contrato de la función que se usa como
  dato de entrada. Un test que falla por un supuesto del test cuesta una vuelta; uno que **pasa**
  por un supuesto equivocado no se detecta nunca. Para el segundo caso, `sembrar` ahora acepta
  `otras={...}` para varias barras en el mismo mes.

### C4. Explicaciones demasiado densas y preguntas que no se entienden
- **Error:** en una misma respuesta mezclé el diagnóstico, la corrección, un concepto nuevo, una
  contradicción propia y una "pregunta de verificación" formulada como acertijo. El usuario
  respondió dos veces seguidas **"no entiendo"**: primero *"¿config ahora está bien?"* y después
  *"no entiendo qué esperas que haga con `os.environ`"*. Las dos veces la información estaba en mi
  mensaje, pero enterrada.
- **Evidencia (2026-10-05):** dos mensajes consecutivos del usuario pidiendo aclaración sobre
  respuestas que yo consideraba completas.
- **Lección, en modo mentor:**
  - Responder **primero** la pregunta literal que se hizo ("¿está bien?" → sí o no, y qué falta),
    y solo después agregar contexto.
  - Un concepto nuevo por respuesta. El resto es ruido que esconde la tarea.
  - Al proponer un cambio: mostrar el **antes y el después**, y el motivo concreto de por qué.
    No basta nombrar un mecanismo.
  - Las preguntas de verificación (§2.2) tienen que ser **respondibles**, no adivinanzas. Si la
    respuesta exige saber lo que todavía no se enseñó, no es una pregunta: es un enigma.
  - Separar lo urgente de lo opcional. Un valor sensible en un archivo versionado y un detalle de
    mantenibilidad no pueden ir con el mismo peso en la misma lista.

---

## D. Prácticas verificadas (no son errores, pero evitan uno)

### D1. La fixture de aislamiento debe barrer el prefijo, no listar variables
- **Aplica a los dos proyectos** (`CMGI_` en `cmg-ingesta`, `PPA_` en `ppa-pipeline`).
- **Problema:** una fixture que borra las variables una por una queda desincronizada en cuanto se
  agrega un campo a `Settings`. Si en la máquina existe la variable que falta en la lista, el test
  deja de estar aislado **en silencio**: `test_valores_por_defecto` empieza a leer el valor real
  en vez del default, y pasa o falla según el computador.
- **Práctica:**

  ```python
  import os

  @pytest.fixture(autouse=True)
  def sin_archivo_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
      monkeypatch.chdir(tmp_path)                 # sin .env a la vista
      for clave in list(os.environ):
          if clave.startswith("<PREFIJO>_"):
              monkeypatch.delenv(clave, raising=False)
  ```

- **Por qué `monkeypatch.delenv` y no `del os.environ[...]`:** monkeypatch **restaura** la variable
  al terminar el test. Un `del` directo modifica el entorno real del proceso y contamina los tests
  siguientes.
- **Por qué `monkeypatch.chdir(tmp_path)`:** `env_file=".env"` es una ruta **relativa**, así que lo
  que aísla del `.env` real es el cambio de directorio, no el `delenv`.
- **Ojo:** el `list()` es por legibilidad, **no** es obligatorio (ver A6).

### D2. `str(obj)`, no `print(obj)`, para testear que un secreto no se filtra
- `print()` devuelve `None`: no hay nada sobre lo que hacer la aserción. `str(obj)` entrega el
  mismo texto que `print` muestra, porque es lo que usa por detrás.
- El test completo verifica **las dos mitades**, o puede pasar por la razón equivocada:

  ```python
  assert "clave-falsa" not in str(config)                        # no se filtra
  assert config.cen_api_key.get_secret_value() == "clave-falsa"  # pero si se guardo
  ```

  Sin la segunda línea, el test también pasaría si el campo no guardara nada.

### D3. `.env.example` se versiona: documenta variables, nunca valores
- Es el único archivo de la familia `.env` que entra a git (`!.env.example` en `.gitignore`).
  Todo lo que se escriba ahí queda público.
- Las claves van **vacías** (`<PREFIJO>_CEN_API_KEY=`), lo que además ejercita
  `env_ignore_empty=True`: vacío cuenta como "no configurado".
- Un placeholder inventado tampoco sirve: quien lee el repo no puede distinguirlo de una
  credencial real.
