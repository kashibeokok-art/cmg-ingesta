# cmg-ingesta

Base de costos marginales (CMg) cuarto-horarios por barra del Sistema Eléctrico Nacional de
Chile, con las consultas y exportes que se usan para análisis de pricing.

Almacenamiento en Parquet particionado + DuckDB. **201,8 millones de filas en 213 MB**, con
consultas de un mes en menos de un segundo.

## Instalar

Requiere [uv](https://docs.astral.sh/uv/) y Python 3.13.

```powershell
uv sync
```

## Configurar

```powershell
Copy-Item .env.example .env
```

| Variable | Para qué |
|---|---|
| `CMGI_DATA_DIR` | carpeta raíz de los datos (por defecto `data`); tiene que existir, salvo para `cmg migrar-historico` |

Es la única variable. `CMGI_LOG_LEVEL` y `CMGI_CEN_API_KEY` se quitaron (2026-10-13): nadie las
usaba. Si siguen en un `.env` viejo, el programa no arranca hasta comentarlas o borrarlas.

- **Una `CMGI_*` que el programa no conoce lo detiene**, venga del `.env` o del sistema: casi
  siempre es un nombre mal escrito. El mensaje dice cuál es.
- **Las variables de otras herramientas se ignoran**: el `.env` del servidor puede tener también
  los datos de conexión a SQL.
- **La carpeta de datos no se crea sola**, ni siquiera al descargar: si la ruta estuviera mal
  escrita, ~23 GB irían a una carpeta equivocada. En un equipo nuevo, créala una vez con
  `mkdir data`.

`.env` nunca va a git. `.env.example` sí, y por eso **no lleva valores**.

## Usar

### El menú (uso diario)

```powershell
uv run cmg
```

Sin comando, `cmg` abre un menú como el de `CMG_Portable`:

- **Búsqueda difusa de barras:** se escribe parte del nombre (`elvira 13`, `blancas`, `quelon`)
  y aparecen las que calzan. Ignora mayúsculas, puntos, guiones bajos, tildes y la Ñ, y tolera
  errores de tipeo.
- **Selección múltiple:** de la lista de coincidencias se eligen varias con `1,3`, `2-5` o
  `todas`; se puede buscar de nuevo para sumar más, y `quitar` saca una.
- **`v` vuelve al paso anterior** sin perder lo elegido; Enter vacío cancela.
- Antes de generar archivos muestra una previsualización por barra y pide confirmación.

| Opción | Qué hace |
|---|---|
| 1 | Descargar CMg quinceminutal de una o varias barras (Excel / CSV / Parquet) |
| 2 | Ver promedios por bloque en pantalla (una barra en detalle, o varias lado a lado) |
| 3 | Riesgo nodal de una referencia contra una o varias barras, exportado |
| 4 | Actualizar desde la página del Coordinador (sugiere desde qué día) |
| 5 | Revisar si el Coordinador cambió algo |

### Los comandos (para automatizar)

```powershell
# qué datos hay
uv run cmg estado

# cargar el histórico desde la base antigua
uv run cmg migrar-historico "C:\...\CMG_Portable\CMG_DB"

# buscar una barra
uv run cmg barras elvira

# ver el promedio mensual por bloque
uv run cmg bloques A.BLANCAS_____013 --periodo "ultimos 6"

# exportar a archivo
uv run cmg descargar STA.ELVIRA____013 --periodo 2024 --formato excel,csv

# riesgo nodal entre dos barras
uv run cmg riesgo STA.ELVIRA____013 QUELLON_______013 --periodo 2024-06
```

### Datos nuevos (2024-08 en adelante) desde la página del Coordinador

```powershell
# 1. Bronze: baja los ZIP diarios (--hasta por omisión = ayer). Además busca en el
#    sitemap del sitio las REVISIONES (v2, v3) publicadas desde la última vez y baja
#    esos días aunque estén fuera del rango.
uv run cmg descargar-cen --desde 2024-08-01

# 2. Silver: ingiere solo los meses cuyos ZIP cambiaron
uv run cmg ingerir-pagina

# 3. Vigilancia (para tarea programada): cambios de formato, días sin archivo,
#    preliminares que llevan más de 25 días sin definitivo, revisiones sin bajar
uv run cmg vigilar-fuente
```

**Bronze o Silver en otra carpeta** (por ejemplo en la red): `CMGI_BRONZE_DIR` y
`CMGI_SILVER_DIR` en el `.env` son las carpetas finales (Bronze: los ZIP y `manifiesto.json`;
Silver: `anio=…/mes=…`). Tienen que existir: si la red no está conectada o la ruta está mal, el
comando se detiene sin crear nada. Los temporales siguen en `CMGI_DATA_DIR`. Para mudar lo que ya
hay, copiar el contenido de `data\bronze\cen_cmg` y de `data\silver\cmg` a esas carpetas,
comprobar con `uv run cmg estado` y recién ahí borrar la copia local.

**Equipo con poco disco** (`CMGI_CONSERVAR_ZIP=false` en el `.env`): cada ZIP (~11 MB) que ya
quedó en Silver se borra y queda un testigo `<nombre>.zip.testigo.json` de ~300 bytes con su
sha256. El manifiesto no cambia, así que la descarga no lo vuelve a bajar. Si después llega una
revisión de un día del mismo mes, los otros días se toman del Silver que ya estaba. Los ZIP con
problemas (día omitido o sin clasificar) no se borran. Lo que se pierde: un `--forzar` ya no
vuelve a leer esos ZIP, los toma de Silver tal cual. Para no llenar el disco, descargar e ingerir
por tramos (por ejemplo, de a un mes; ~700 MB de ZIP por mes con pre y def).

La página publicó en dos formatos: hasta el 2024-10-08 (operación 2024-08 y 2024-09) las
columnas se llaman `CMgP`/`CMgR` y vienen en mills/kWh (= USD/MWh); después, `CMG_REAL_PRE`/
`CMG_REAL_DEF`. El programa lee los dos (verificado con ZIP reales, error A18). Algunas revisiones
(v2) de 2025-02/03 traen dos columnas para el mismo valor (`CMG_REAL_DEF` y `CMG_REAL_DEF v2`): la
primera repite el v1 y se lee **la última** del encabezado, que es la revisada (error A19). Cada
hallazgo del reporte dice en qué ZIP está, y el reporte agrupa el mismo problema en una tabla.

La descarga es **reanudable**: si se corta (red, Ctrl+C), lo ya bajado queda registrado y
basta volver a correr el mismo comando. Las fallas pasajeras del sitio (429, 5xx) se
reintentan solas; cada ZIP se verifica antes de registrarse.

**Antes de descargar**, el programa compara el sitio con su línea base
(`config/linea_base_cen.toml`): las marcas del HTML de las que depende, un día de referencia
con sus documentos conocidos, el índice de años y el sitemap. Si algo estructural cambió, se
detiene **sin bajar nada** y deja el detalle en `data/alertas/`. Para descargar igual:
`cmg descargar-cen ... --omitir-chequeo`.

### Publicar en PostgreSQL (base `cmg_cen`, para la API)

Silver y Gold se copian a PostgreSQL para que una API en el servidor responda las consultas: quien quiera el dato fino pide Silver; quien quiera el número listo para cotizar, Gold. La base
guarda además **qué documento del Coordinador hay en cada día** y el **historial de
revisiones**: cada vez que un día cambia de versión (preliminar → definitivo, definitivo → v2),
cuántos cuartos de hora cambiaron y cuánto.

Una vez, en el servidor (como administrador de PostgreSQL):

```powershell
psql -h 192.168.0.85 -p 5433 -U postgres -d cmg_cen -f sql/cmg_cen.sql
```

y ponerle clave a los dos usuarios que crea el script: `cmg_carga` (escribe, lo usa
`publicar-sql`) y `cmg_lectura` (solo lee, lo usa la API). En el `.env` del equipo que carga:
`CMGI_PG_URL=postgresql://cmg_carga:CLAVE@192.168.0.85:5433/cmg_cen`.

Después, cada vez que Silver cambie:

```powershell
uv run cmg publicar-sql
```

Sube solo los meses que cambiaron, un mes completo a la vez: mientras carga, la API sigue
viendo la versión anterior. Cada mes muestra su avance (`[3/22] 2023-03: ...`).

Para no subir todo el histórico, `CMGI_DESDE=2023-01` en el `.env`: solo se sube de ese mes en
adelante (y `estado` no cuenta como faltante nada anterior). Si ya había meses anteriores en la base, el programa avisa y no los toca;
`uv run cmg publicar-sql --quitar-anteriores` los borra (cuartos, Gold, días, documentos,
revisiones y las barras que quedan sin datos), todo en una transacción, y luego sube lo que falte.

Medido con datos reales: ~1 minuto y ~390 MB por mes; la consulta de
una barra en un periodo, ~12 ms.

| Tabla o vista | Capa | Qué tiene |
|---|---|---|
| `cmg.cmg_15min` (vista) | Silver | Cada barra cada 15 minutos: barra, fecha, hora, minuto, bloque, CMg, origen |
| `cmg.cmg_mensual` (vista) | Gold | Por barra y mes: A, B, C, Solar, NoSolar, Total, y cuántos días son preliminares |
| `cmg.cmg_mensual_bloque` (vista) | Gold | Por barra, mes y bloque: promedio, desvío, mínimo, máximo, cuartos en cero |
| `cmg.dia` | — | Qué hay cargado hoy en cada día: origen y documento del CEN |
| `cmg.revision` | — | Historial de días que cambiaron de versión, con cuánto cambió el CMg |
| `cmg.documento` | — | Todos los ZIP del CEN que se descargaron, se usen o no |
| `cmg.carga_mes` | — | Qué versión de cada mes de Silver está cargada |

### App de consulta para usuarios: `CMG_Consulta.exe`

Una ventana para quien tenga acceso a la carpeta compartida, sin Python: elige una barra
(búsqueda aproximada, siempre las 10 más parecidas con su %), un rango de días, y descarga un
Excel con el CMg cada 15 minutos y los promedios por bloque; o el riesgo nodal entre dos barras.
Lee PostgreSQL con el usuario `cmg_lectura` en modo solo lectura. Código en
`src/cmg_ingesta/consulta/` (no usa DuckDB ni nada de la ingesta).

```powershell
powershell -File empaquetar/construir_consulta.ps1   # arma empaquetar/dist/CMG_Consulta (~92 MB)
uv run cmg-consulta                                  # la misma ventana, en desarrollo
```

Para instalarla: copiar la carpeta `CMG_Consulta` a la carpeta compartida y, junto al `.exe`,
crear `conexion.ini` desde `conexion.ini.example` con la clave de `cmg_lectura` (no se versiona).
El Excel trae: CMg 15 min con la versión de cada día ("Definitivo v2"), bloques por mes y del
rango (A, B, C, Solar, NoSolar, Total), estadísticas por bloque, versión por día, revisiones del
rango e Info (avisa si hay días preliminares). Un test exige que los bloques y el riesgo den lo
mismo que el Gold del programa; con datos reales de 2022-12 la diferencia es 0.

Diagnóstico sin ventana: `CMG_Consulta.exe --prueba BARRA REFERENCIA dd-mm-aaaa dd-mm-aaaa
CARPETA` escribe los dos Excel y `prueba.log` en CARPETA.

### Archivos que se editan a mano

| Archivo | Para qué |
|---|---|
| `config/nombres_cen.toml` | Las formas de nombre de los ZIP que el programa reconoce, como plantillas (`{tipo}-v{version}_{fecha}`). Las instrucciones están en el mismo archivo. Después de editarlo: `uv run pytest tests/extract/test_catalogo_nombres.py -q` |
| `config/linea_base_cen.toml` | Cómo era el sitio cuando se construyó el programa. Se actualiza solo si el sitio cambia y el programa ya se adaptó. |

Y uno que el programa escribe para que lo revises:

| Archivo | Qué trae |
|---|---|
| `data/bronze/cen_cmg/nombres_no_reconocidos.csv` | Todo enlace a ZIP que no calzó con ninguna forma: día, nombre, URL, etiqueta, texto del bloque, motivo, primera y última vez que se vio. Se abre con Excel. |

Cuando algo requiere revisión manual, el comando sale con código 3 y deja un reporte en
`data/alertas/deriva_*.md` (para leer) y `.json` (para procesar). Un día con hallazgo
**crítico**, o con un archivo que no se pudo clasificar, no entra a Silver.

En Silver, la columna `origen` dice de dónde viene cada fila: `maestro_cmg_db`,
`pagina_cen_def` o `pagina_cen_pre` (preliminar, puede cambiar cuando llegue el definitivo).

Formatos de periodo: `2025` · `2025-03` · `2025-03 a 2025-08` · `ultimos 6` · vacío = todo.

### Códigos de salida

| Código | Significa |
|---|---|
| 0 | todo bien |
| 1 | el pedido estaba bien escrito, pero no se pudo cumplir (sitio caído, base vacía, barra sin datos) |
| 2 | el comando está mal escrito (falta una opción, una fecha no se entiende...) |
| 3 | terminó, pero las validaciones encontraron algo que revisar en `data/alertas/` |

Para una tarea programada: 1 = no se hizo, revisar ya; 2 = la tarea está mal configurada;
3 = se hizo, pero hay que mirar el reporte. El 2 es el que usa click (la librería de debajo de
typer) para sus propios errores de uso, por eso los hallazgos usan el 3.

## Lo que hay que saber de los datos

**Un día no siempre tiene 96 cuartos de hora.** El programa lo calcula con la zona horaria
`America/Santiago` y **valida** que el dato lo cumpla:

| Día | Horas | Cuartos |
|---|---|---|
| normal | 24 | 96 |
| primer sábado de abril | **25** | **100** |
| domingo DST de septiembre | **23** | **92** |

Omitir la hora extra de abril descuadra el balance energético y financiero mensual (NT CyO de
la CNE). Y la hora de septiembre que el reloj se salta viene publicada con valor 0,00: hay que
descartarla, porque diluye el promedio del bloque A un 0,370%.

**Bloques horarios:** A = 23:00–07:59 (9 h) · B = 08:00–17:59 (10 h) · C = 18:00–22:59 (5 h).
Solar = promedio de B. NoSolar = promedio de los cuartos de A y C juntos, **no** el promedio de
sus promedios (serían 150 en vez de 135,71 con A=100 y C=200).

**El 24,7% de los CMg son exactamente 0** (horas de sol), y hay meses mucho peores: en noviembre
2024 el 95,8% del bloque B estuvo en cero. Por eso el riesgo nodal porcentual por intervalo es
`NULL` cuando la referencia vale 0, y el porcentaje confiable se calcula sobre promedios
mensuales.

## Estructura

```
src/cmg_ingesta/
├─ cli.py          comandos para automatizar; sin comando abre el menú
├─ menu/           el menú interactivo: búsqueda difusa, diálogos y flujos
├─ conexion.py     una sola forma de abrir DuckDB
├─ config.py       configuración desde el entorno (12-factor)
├─ domain/         reglas de negocio puras, sin I/O
├─ silver/         limpio y conformado, particionado anio/mes
├─ gold/           agregados de negocio
├─ quality/        validaciones contra el calendario + vigilancia de la fuente (deriva)
├─ extract/        una fuente por módulo: cmg_db (Maestro) y la página del CEN
└─ reportes/       Excel, CSV, Parquet
```

Arquitectura **Medallion**: Bronze (crudo, `data/bronze/cen_cmg/`) → Silver (conformado) →
Gold (negocio).
`domain/` no es una capa: son las reglas que usan todas.

## Desarrollar

```powershell
uv run pytest          # 732 tests, ninguno toca la red (23 necesitan un PostgreSQL de pruebas: CMG_PRUEBA_PG)
uv run mypy            # tipado estricto
uv run ruff check .    # linter
uv run ruff format .   # formateo
```

## Documentación

- [`docs/aprendizaje/`](docs/aprendizaje/README.md) — guías de estudio del código, con casos
  prácticos ejecutables
- [`docs/errores_verificados.md`](docs/errores_verificados.md) — errores ya cometidos, con la
  evidencia que los desmintió
- [`CLAUDE.md`](CLAUDE.md) — memoria del proyecto: alcance, fuentes, decisiones y bitácora
- [`docs/consulta_cen_dst.md`](docs/consulta_cen_dst.md) — la consulta pendiente al CEN sobre
  los días de cambio de hora

## Estado

| Fase | |
|---|---|
| Dominio, esquema y escritura atómica | ✅ |
| Migración del histórico 2021-01 → 2024-07 con validación | ✅ |
| Consultas, exportes y riesgo nodal | ✅ |
| CLI | ✅ |
| Descarga desde la página del CEN (2024-08+) | ✅ |
| Vigilancia de la fuente y completitud | ✅ |
| Ingesta de la página a Silver | ✅ |
| Menú interactivo | ✅ |
| Carga real de 2024-08 a hoy | ⏳ pendiente de aprobación |
