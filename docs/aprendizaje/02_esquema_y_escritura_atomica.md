# M2 — El contrato de datos y la escritura atómica

> Guía de estudio. Archivos: `src/cmg_ingesta/domain/esquema.py` y
> `src/cmg_ingesta/silver/escribir.py`. Tests: `tests/silver/test_escribir.py`.

---

## 0. Dónde estamos en Medallion

```
 FUENTES          BRONZE              SILVER                 GOLD
 ───────          ──────              ──────                 ────
 CMG_DB      crudo tal como       limpio y            agregados de
 pagina CEN  vino + manifiesto    conformado          negocio
                                  ^^^^^^^^^^
                                  M2 define su CONTRATO
                                  y como se ESCRIBE
```

M2 no mueve datos todavía. Define **dos cosas** sin las cuales M3 no se puede escribir:

1. **Qué forma tienen los datos en Silver** (`esquema.py`)
2. **Cómo se escriben sin poder corromperse** (`escribir.py`)

---

## 1. `esquema.py` — el contrato de datos

### Qué es un "contrato de datos"

Un acuerdo explícito: *toda fila que entre a Silver tiene estas columnas, con estos tipos, y una
fila significa esto*. El nombre técnico es **data contract**.

Sin contrato, cada fuente impone su forma y la capa siguiente se llena de `if`. Con contrato,
**las fuentes se adaptan al esquema, no al revés**. Esa dirección es la clave: tenemos dos fuentes
hoy (`CMG_DB` y la página) y podríamos tener otra mañana; ninguna debería cambiar lo que sabe
hacer `gold/` ni `reportes/`.

### La granularidad (grain)

> **Una fila = una barra en un cuarto de hora.**

Declararlo explícitamente evita el error más caro del modelado dimensional: mezclar
granularidades y sumar de más. Si una fila fuera "una barra en una hora" y otra "una barra en un
cuarto", sumar la columna daría basura. El nombre del error es **double counting**.

### La clave natural es COMPUESTA

```python
CLAVE_NATURAL = ("barra", "fecha", "hora", "minuto")
```

**Ninguna columna sola identifica una fila.** Esa tupla es la que usa la ingesta para deduplicar:
si un archivo nuevo trae una fila con la misma clave, reemplaza a la vieja.

Fíjate que `hora` va de **0 a 24**, no de 0 a 23. La hora 24 es la segunda 23:00 del día largo de
abril. Si la clave usara solo `fecha_hora`, las dos 23:00 colisionarían — y eso es *exactamente* el
bug de la API del CEN (CLAUDE.md §4.5.2). La clave compuesta con `hora` separada es lo que lo
evita.

### Partición en la ruta, no en el archivo

```python
COLUMNAS_PARTICION = ("anio", "mes")
```

Los datos se guardan en `anio=2025/mes=3/data.parquet`. El año y el mes **no están dentro del
archivo**: están en el nombre de la carpeta. Eso se llama **partición estilo Hive**, y sirve para
*partition pruning*: al filtrar `WHERE anio = 2025 AND mes = 3`, DuckDB ni abre los archivos de
los otros 70 meses.

### 🔬 Caso práctico 1 — ve el pruning funcionando

```python
import duckdb
from pathlib import Path

BASE = Path(r"C:\Users\claudio.araya\Desktop\Scripts\CMG_Portable\CMG_DB")
con = duckdb.connect()
patron = str(BASE / "**" / "*.parquet")

# sin filtro de particion: lee todo
print(con.execute(f"""
    EXPLAIN ANALYZE SELECT count(*)
    FROM read_parquet('{patron}', hive_partitioning=true)
    WHERE anio = 2025 AND mes = 3
""").fetchall()[0][1][:1200])
```

**Qué mirar:** en el plan busca `Files Scanned`. Con el filtro escanea **1** archivo; sin él,
**72**. Prueba quitando el `WHERE` y compara el tiempo.

### Por qué `DOUBLE` y no `FLOAT`

```python
"cmg_usd_mwh": "DOUBLE",
```

Un `FLOAT` de 32 bits tiene unos **7 dígitos significativos**. Un valor como `208.03955` necesita
**8**. La base antigua usaba `FLOAT` y guardaba:

```
fuente:  49.10532
base:    49.10531997680664
```

Eso **nos costó tiempo dos veces** en el estudio E0: perseguimos una "diferencia entre fuentes"
que era el tipo de dato. `DOUBLE` cuesta 4 bytes más por fila y elimina el problema.

La lección general: **el tipo de dato es parte del contrato**, no un detalle de implementación.

### 🔬 Caso práctico 2 — comprueba la pérdida de precisión

```python
import numpy as np

valores = [49.10532, 208.03955, 214.21126]
for v in valores:
    f32 = np.float32(v)
    print(f"{v:>12}  float32 -> {float(f32):<22}  "
          f"{'PIERDE' if float(f32) != v else 'ok'}")
```

**Lo que vas a ver:** los tres pierden precisión en float32.

### `es_hora_extra` y `fecha_hora` nula

La base vieja calculaba `fecha + hora + minuto` incluso para `hora = 24`, lo que daba **el día
siguiente a las 00:00**. Un timestamp que apunta al día equivocado, justo en el intervalo más
delicado del año.

Ahora:

```python
"es_hora_extra": "BOOLEAN",   # True solo en la segunda 23:00
"fecha_hora": "TIMESTAMP",    # NULL cuando es_hora_extra, porque seria ambiguo
```

**Un `NULL` honesto es mejor que un valor plausible y falso.** Si no se puede representar, se dice
que no se puede, y quien consuma decide qué hacer. Lo otro es un error silencioso esperando.

### Las columnas de linaje

```python
COLUMNAS_LINAJE = {"origen": "VARCHAR", "ingerido_en": "TIMESTAMP"}
```

**Linaje (data lineage)** es poder responder *"¿de dónde salió esta fila?"*. Van **en el archivo**
y no en la ruta porque cambian fila a fila: un mes podría completarse desde dos fuentes.

Esto resuelve un problema real que sufrimos: `CMG_DB` **no tiene** columna de origen, así que para
saber qué meses venían del Maestro tuvimos que mirar los *timestamps de los archivos*. Eso es
arqueología, no ingeniería. Con `origen` habría sido una consulta.

---

## 2. `escribir.py` — atomicidad

### El problema, con un bug real

El legado de retiros cargaba con `to_sql(append)`, archivo por archivo, sin transacción. Si fallaba
a la mitad:

1. el mes quedaba **cargado a medias**
2. la verificación *"¿ya existe ese mes?"* decía **sí**
3. el mes quedaba incompleto **para siempre**, y nadie se enteraba

Ese es el peor tipo de bug: no falla, **miente**.

### La solución: escribir aparte y mover

```python
tmp = destino.with_name(destino.name + "__tmp")

shutil.rmtree(tmp, ignore_errors=True)      # 1. temporal limpia
tmp.mkdir(parents=True, exist_ok=True)
con.execute(f"COPY (...) TO '{tmp}/data.parquet' ...")   # 2. escribir TODO

if destino.is_dir():                        # 3. recien ahora reemplazar
    shutil.rmtree(destino)
tmp.replace(destino)
```

El orden es lo único que importa. Si el proceso muere en el paso 2 —que es el largo, el que puede
tardar minutos— **el destino viejo sigue intacto** y la temporal queda huérfana. La siguiente
corrida la limpia con `limpiar_temporales()`.

### Honestidad sobre el límite

Entre el `rmtree(destino)` y el `tmp.replace(destino)` hay una ventana de milisegundos en la que
no existe ninguno de los dos. **No es atomicidad perfecta**: un intercambio de directorios
realmente atómico no existe igual en Windows y Linux.

Lo que sí garantiza: el intervalo de riesgo pasa de **minutos** (lo que tarda escribir el mes) a
**milisegundos** (dos llamadas al sistema de archivos). Eso se llama reducir la **ventana de
vulnerabilidad**, y vale decirlo en vez de prometer algo que no es.

### 🔬 Caso práctico 3 — rompe la escritura a propósito

```python
import duckdb
from pathlib import Path
from cmg_ingesta.silver import escribir

con = duckdb.connect()
base = Path("data/prueba_atomica")

bueno = """
    SELECT 'BARRA_X' AS barra, DATE '2025-03-01' AS fecha,
           CAST(0 AS UTINYINT) AS hora, CAST(0 AS UTINYINT) AS minuto,
           'A' AS bloque, 50.0 AS cmg_usd_mwh, false AS es_hora_extra,
           CAST(NULL AS TIMESTAMP) AS fecha_hora,
           'maestro_cmg_db' AS origen,
           CAST('2026-10-06' AS TIMESTAMP) AS ingerido_en
"""

print("escribo 1 fila:", escribir.escribir_particion(con, bueno, base, 2025, 3))
print("en la base     :", escribir.filas_en_particion(con, base, 2025, 3))

try:
    escribir.escribir_particion(con, "SELECT no_existe", base, 2025, 3)
except duckdb.Error as e:
    print("fallo esperado :", str(e)[:60])

print("en la base     :", escribir.filas_en_particion(con, base, 2025, 3))
print("temporales     :", list(base.rglob("*__tmp")))
```

**Lo que vas a ver:** después del fallo la base sigue con 1 fila y no quedan temporales. El dato
viejo sobrevivió a un error en la escritura nueva.

**Rómpelo:** en `escribir.py` mueve el `shutil.rmtree(destino)` **antes** del `con.execute(...)`
del `COPY`. Corre `uv run pytest tests/silver/test_escribir.py -q`. Va a fallar
`test_un_fallo_deja_intacto_el_mes_anterior`: acabas de reintroducir el bug del legado, y el test
lo detecta.

### Idempotencia

```python
def test_reescribir_reemplaza_no_acumula():
    escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    escribir_particion(con, consulta_falsa(10), tmp_path, 2025, 3)
    assert filas_en_particion(con, tmp_path, 2025, 3) == 10   # no 20
```

**Idempotencia** = ejecutar N veces produce el mismo resultado que ejecutar una vez. Es lo que
permite reintentar sin miedo: si no sabes si la corrida de ayer terminó, la repites.

Fíjate que se consigue **reemplazando** la partición completa, no comparando fila por fila. Es más
simple y no puede quedar a medias. La complejidad del modo "combinar" vive en la *consulta*, no en
la función de escritura — que así se mantiene con una sola responsabilidad.

### El helper `_sql_str`

```python
def _sql_str(ruta: Path) -> str:
    return "'" + str(ruta).replace("'", "''") + "'"
```

Interpolar una ruta directo en SQL rompe la consulta si la carpeta tiene un apóstrofo, y en el peor
caso permite **inyección SQL**. El test `test_ruta_con_apostrofo_no_rompe_el_sql` lo cubre con una
carpeta llamada `carpeta'con'comillas`.

Es aburrido y es exactamente el tipo de cosa que explota en producción con una ruta de red que
alguien nombró `C:\Clientes\O'Higgins\`.

### El guion bajo del nombre

`_sql_str` empieza con `_` por convención: **es privada del módulo**. No es que Python lo impida —
es una señal a quien lee: *esto es un detalle interno, no lo uses de afuera, puede cambiar*. Las
funciones sin `_` son el **API público** del módulo.

---

## 3. Los tests, por categoría

| Test | Qué protege | Categoría |
|---|---|---|
| `test_escribe_y_cuenta` | el camino feliz | happy path |
| `test_reescribir_reemplaza_no_acumula` | idempotencia | invariante |
| `test_un_fallo_deja_intacto_el_mes_anterior` | **atomicidad** | el más importante |
| `test_no_queda_carpeta_temporal_tras_el_exito` | no deja basura | limpieza |
| `test_limpiar_temporales_no_toca_las_buenas` | el limpiador no borra de más | seguridad |
| `test_meses_escritos_ignora_carpetas_vacias` | una carpeta sin parquet no es un mes | borde |
| `test_ruta_con_apostrofo_no_rompe_el_sql` | escapado de comillas | seguridad |

Nota `test_limpiar_temporales_no_toca_las_buenas`: cuando escribes una función que **borra cosas**,
el test que importa no es "borra lo que debe" sino **"no borra lo que no debe"**. Un limpiador
demasiado entusiasta es peor que no tener limpiador.

Y los datos de prueba se generan con SQL (`consulta_falsa`), **sin depender de `CMG_DB`**. Así los
tests corren en cualquier máquina, en CI, en segundos, y no se rompen si la base cambia.

---

## 4. Glosario

| Mecanismo (EN) | En una línea | Dónde |
|---|---|---|
| Data Contract | esquema y reglas que todo dato debe cumplir | `esquema.py` |
| Grain | qué representa exactamente una fila | `esquema.py` |
| Natural Key / Composite Key | clave de negocio, a veces de varias columnas | `CLAVE_NATURAL` |
| Double Counting | sumar de más por mezclar granularidades | §1 |
| Hive Partitioning | `clave=valor/` en la ruta para leer menos | `COLUMNAS_PARTICION` |
| Partition Pruning | saltarse archivos que el filtro descarta | caso 1 |
| Data Lineage | de dónde salió cada fila | `COLUMNAS_LINAJE` |
| Atomicity | se escribe todo o nada | `escribir_particion` |
| Idempotency | N veces = 1 vez | `test_reescribir...` |
| Single Responsibility | cada función hace una cosa | escribir vs. combinar |
| SQL Injection | datos que se ejecutan como código | `_sql_str` |
| Private by convention | el `_` dice "interno" | `_sql_str` |

---

## 5. Autoevaluación

1. ¿Por qué la clave natural incluye `hora` separada en vez de usar solo `fecha_hora`?
2. ¿Qué habría pasado si `CMG_DB` hubiera tenido columna `origen` desde el principio?
3. ¿Por qué `fecha_hora` es `NULL` en la hora extra en vez de apuntar al día siguiente?
4. En `escribir_particion`, ¿qué pasa si el proceso muere **durante** el `COPY`? ¿Y entre el
   `rmtree` y el `replace`?
5. ¿Por qué los tests generan datos con SQL en vez de leer `CMG_DB`?
6. ¿Por qué el test importante de `limpiar_temporales` es el que verifica que **no** borra?

Respuestas: §1 (clave compuesta), §1 (linaje), §1 (`es_hora_extra`), §2 (orden de los pasos y la
ventana de vulnerabilidad), §3 (independencia de los tests), §3 (borrar es peligroso).
