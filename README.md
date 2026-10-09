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
| `CMGI_DATA_DIR` | carpeta raíz de los datos (por defecto `data`) |
| `CMGI_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` o `ERROR` |
| `CMGI_CEN_API_KEY` | sin uso: se decidió no usar la API (queda por compatibilidad) |

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

La descarga es **reanudable**: si se corta (red, Ctrl+C), lo ya bajado queda registrado y
basta volver a correr el mismo comando. Las fallas pasajeras del sitio (429, 5xx) se
reintentan solas; cada ZIP se verifica antes de registrarse.

**Antes de descargar**, el programa compara el sitio con su línea base
(`config/linea_base_cen.toml`): las marcas del HTML de las que depende, un día de referencia
con sus documentos conocidos, el índice de años y el sitemap. Si algo estructural cambió, se
detiene **sin bajar nada** y deja el detalle en `data/alertas/`. Para descargar igual:
`cmg descargar-cen ... --omitir-chequeo`.

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
uv run pytest          # 552 tests, ninguno toca la red
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
