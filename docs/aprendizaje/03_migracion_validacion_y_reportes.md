# M3 y M6 — Migración con validación, y la capa Gold

> Guía de estudio. Archivos: `extract/{cmg_db,migrar}.py`, `quality/checks.py`,
> `silver/leer.py`, `gold/{bloques_mes,riesgo_nodal}.py`, `reportes/exportar.py`.

---

## 0. El recorrido completo, en una imagen

```
  CMG_DB (2021-2024)            data/silver/cmg/             consultas
  ┌────────────────┐            ┌──────────────┐            ┌──────────┐
  │ anio=2021/..   │  extract/  │ anio=2021/.. │   gold/    │ Excel    │
  │ anio=2024/..   │ ─────────► │ anio=2024/.. │ ─────────► │ CSV      │
  └────────────────┘  cmg_db    └──────────────┘ bloques_mes│ Parquet  │
                      migrar           ▲         riesgo     └──────────┘
                         │             │
                  quality/checks   silver/escribir
                  valida ANTES     escritura atomica
                  de escribir
```

**Resultado real de la corrida:** 201.810.260 filas, 48 meses, 213,5 MB, 3,3 minutos.
Y 10 días marcados como defectuosos, que son exactamente los que el estudio E0 había
encontrado a mano.

---

## 1. `extract/cmg_db.py` — el mapeo

### No confiar en las columnas derivadas de la fuente

`CMG_DB` ya trae una columna `bloque`. La migración **no la copia**: la recalcula.

```python
# domain/bloques.py — la usan las dos fuentes (Maestro y página)
def sql_bloque(columna: str = "hora") -> str:
    casos = " ".join(
        f"WHEN {hora} THEN '{bloque}'"
        for hora, bloque in sorted(BLOQUE_POR_HORA.items())
    )
    return f"CASE {columna} {casos} ELSE NULL END"
```

(Antes vivía en `cmg_db.py` como `_sql_bloque`. Cuando llegó la segunda fuente se movió a
`domain/`: si dos módulos necesitan la misma regla, la regla es del dominio, no de uno de ellos.)

Dos razones:

1. **Si la fuente y nosotros discrepamos, mandamos nosotros.** La regla de negocio vive en
   `domain/bloques.py`, no en los datos de entrada.
2. **El SQL se GENERA desde la tabla de Python.** Si mañana cambia `BLOQUE_POR_HORA`, este
   `CASE` cambia con ella. Si lo hubiera escrito a mano habría **dos copias** de la regla, y
   tarde o temprano se separan.

El test `test_el_mapeo_recalcula_el_bloque` siembra una base falsa que dice `'A'` en **todas**
las horas y verifica que el mapeo devuelva `(8, "B")`, `(18, "C")`, etc. O sea: comprueba que
el programa **desconfía** de la fuente.

### Lo que la migración agrega

| Columna | Por qué |
|---|---|
| `es_hora_extra` | explícito en vez de inferido de `hora == 24` en cada consulta |
| `fecha_hora` NULL en la hora extra | la base vieja ponía el día siguiente a las 00:00 |
| `origen` | `CMG_DB` no tiene linaje; por eso tuvimos que mirar *timestamps de archivos* |
| `ingerido_en` | cuándo entró, para auditar |

### La limitación que se hereda

`CMG_DB` guardó el CMg en `FLOAT` de 32 bits. Migrar **no recupera** esa precisión: lo que se
perdió, se perdió. El esquema nuevo usa `DOUBLE` para que no vuelva a pasar con los datos que
vengan de la página.

Vale decirlo en vez de pretender que la migración "limpia" los datos. No los limpia: los
conforma.

---

## 2. `quality/checks.py` — validar es comparar contra una expectativa

### El problema que resuelve

Durante años nadie detectó que la hora extra de abril estaba en el día equivocado en 2021, 2022
y 2024. ¿Por qué? Porque **nadie tenía una expectativa contra la que comparar.**

La validación no es "buscar cosas raras". Es: *el calendario dice que este día tiene 100 cuartos;
el dato tiene 96; repórtalo*.

### Cómo se cruza Python con SQL

Acá hay un problema de diseño interesante. El calendario vive en Python
(`domain/calendario.py`), pero las validaciones son consultas SQL sobre 200 millones de filas.
¿Cómo se juntan?

**Mal:** reescribir la regla en SQL. Serían dos fuentes de verdad.

**Bien:** materializar el calendario como tabla y hacer `JOIN`.

```python
def crear_tabla_calendario(con, desde, hasta) -> int:
    filas = []
    dia = desde
    while dia <= hasta:
        filas.append({
            "fecha": dia,
            "cuartos_esperados": calendario.cuartos_esperados(dia),
            ...
        })
        dia += timedelta(days=1)
    df = pd.DataFrame(filas)
    con.register("_cal_df", df)
    con.execute(f"CREATE OR REPLACE TEMP TABLE {TABLA_CALENDARIO} AS SELECT * FROM _cal_df")
```

1.461 filas para 4 años. Se genera una vez, y después el SQL hace `JOIN ... USING (fecha)`.
La regla sigue existiendo en un solo lugar.

### La validación clave

```sql
WITH real AS (
    SELECT fecha, count(*) / count(DISTINCT barra) AS cuartos_por_barra
    FROM datos GROUP BY fecha
)
SELECT c.fecha, r.cuartos_por_barra, c.cuartos_esperados,
       r.cuartos_por_barra - c.cuartos_esperados AS diferencia
FROM real r JOIN calendario_esperado c USING (fecha)
WHERE r.cuartos_por_barra <> c.cuartos_esperados
```

La división por `count(DISTINCT barra)` es lo que hace la consulta independiente de cuántas
barras haya. Y `diferencia` es el dato que diagnostica:

| Diferencia | Significa |
|---|---|
| **−4** en abril | falta la hora extra (el bug de la API) |
| **+4** en abril | hay una hora extra que no corresponde (fecha corrida) |
| **+4** en septiembre | está la hora fantasma |

### 🔬 Caso práctico 1 — el defecto aparece como un par

Lo que la migración real reportó para 2024:

```
     fecha  cuartos_por_barra  cuartos_esperados  diferencia  es_dia_largo
2024-04-05              100.0                 96         4.0         False
2024-04-06               96.0                100        -4.0          True
```

El viernes tiene 4 de más y el sábado 4 de menos. **Un corrimiento de un día se delata como
dos hallazgos simétricos**, y eso es más informativo que un solo error: te dice que el dato
existe pero está mal fechado, no que falte.

Córrelo tú:

```powershell
uv run pytest tests/quality/test_checks.py::test_detecta_el_corrimiento_de_fecha_como_un_par -v
```

### Por qué `horas_incompletas` existe además

```python
def test_detecta_una_hora_con_tres_cuartos():
    filas.remove((dia, 5, 30, 50.0))   # a la hora 5 le quedan 3
    filas.append((dia, 6, 7, 50.0))    # y la 6 queda con 5

    assert len(checks.dias_con_largo_incorrecto(con, t)) == 0   # el total cuadra!
    assert set(checks.horas_incompletas(con, t)["hora"]) == {5, 6}
```

El total del día sigue siendo 96 y la primera validación **no ve nada**. Dos validaciones que
miran la misma cosa a distinta granularidad atrapan errores distintos.

Esto salió de una observación tuya: *"ambas son necesarias para saber si alguna hora no tiene
un dato cuarto-horario"*.

### Reportar, nunca corregir

Ninguna función de `checks.py` modifica datos. Todas devuelven lo que encontraron.

Si la validación corrigiera en silencio, sería **peor que no tenerla**: creerías que los datos
están bien porque nadie te avisó. Reportar y seguir es una decisión; corregir callado es
esconder.

---

## 3. `extract/migrar.py` — orquestar sin acoplar

### Validar antes de escribir

```python
con.execute(f"CREATE OR REPLACE TEMP VIEW _entrante AS {consulta}")
res = checks.resumen(con, "_entrante")
escritas = escribir.escribir_particion(con, consulta, destino, anio, mes)
```

Una **vista** no copia datos: es la consulta con un nombre. Así se valida *lo que entraría* sin
materializar nada ni escribir dos veces.

### El callback `avisar`

```python
Avisar = Callable[[str], None]

def _nada(_mensaje: str) -> None:
    """Callback por defecto: no avisa nada."""

def migrar_historico(con, origen, destino, avisar: Avisar = _nada) -> list[FilaReporte]:
```

`migrar_historico` **no imprime**. Recibe una función y la llama.

- En la consola: `migrar_historico(..., avisar=print)`
- En los tests: `avisos = []; migrar_historico(..., avisar=avisos.append)` y después se afirma
  sobre la lista
- Con logging: `avisar=log.info`

Eso es **inyección de dependencias**. Si la función llamara a `print` directo, el test tendría
que capturar `stdout`, que es frágil y lento. El test
`test_migracion_limpia_temporales_huerfanas` usa justamente `avisos.append`.

### `TypedDict`, y por qué un `# type: ignore` es una señal

Escribí primero:

```python
def resumen(con, fuente) -> dict[str, object]:     # ← mal
...
return any(int(res[k]) > 0 for k in claves)  # type: ignore[call-overload]
```

Con `dict[str, object]`, cada lectura devuelve `object`, y `int(object)` no compila. Puse tres
`# type: ignore` para callar a mypy.

**Eso era la señal de que el tipo estaba mal, no de que mypy se equivocara.** La solución:

```python
class Resumen(TypedDict):
    filas: int
    dias_con_largo_incorrecto: int
    detalle_largo: pd.DataFrame
    ...
```

Los tres `ignore` desaparecieron solos. Y ojo: **`TypedDict` no es programación orientada a
objetos.** No se instancia, no tiene métodos, no hay herencia. Es un `dict` normal en ejecución;
`TypedDict` solo le dice a mypy qué claves tiene. Se sigue escribiendo `{"filas": 10, ...}`.

Regla práctica: **cuando necesites un `# type: ignore`, para y revisa el tipo.**

---

## 4. `silver/leer.py` — filtrar donde sirve

### El filtro tiene que ser sobre las columnas de partición

```python
def sql_filtro_periodo(desde: Mes, hasta: Mes) -> str:
    return (f"((anio * 100 + mes) BETWEEN {desde[0] * 100 + desde[1]} "
            f"AND {hasta[0] * 100 + hasta[1]})")
```

Esto se ve raro —¿por qué `anio * 100 + mes`?— y tiene una razón: convierte un rango de dos
dimensiones en uno de una sola, así `2024-11 a 2025-02` queda `BETWEEN 202411 AND 202502` en vez
de tres condiciones con `OR`.

Lo importante: **el filtro es sobre `anio` y `mes`, que son las columnas de PARTICIÓN.** Solo
así DuckDB descarta archivos enteros sin abrirlos. Un filtro equivalente sobre `fecha` obligaría
a abrir los 48 archivos y leer la columna.

### 🔬 Caso práctico 2 — mide el pruning

```python
import duckdb
from pathlib import Path
from cmg_ingesta.silver import leer

BASE = Path("data/silver/cmg")
con = duckdb.connect()

for nombre, filtro in [
    ("con filtro de particion", leer.sql_filtro_periodo((2024, 3), (2024, 3))),
    ("filtro sobre fecha",      "fecha BETWEEN DATE '2024-03-01' AND DATE '2024-03-31'"),
]:
    plan = con.execute(
        f"EXPLAIN ANALYZE SELECT count(*) FROM {leer.sql_dataset(BASE)} WHERE {filtro}"
    ).fetchall()[0][1]
    linea = [x for x in plan.splitlines() if "Files" in x or "Total Time" in x]
    print(f"{nombre:<26} {linea}")
```

**Qué mirar:** `Files Scanned`. Con el filtro de partición debería escanear **1**; con el filtro
sobre `fecha`, **48**.

---

## 5. `gold/` — de los cuartos al negocio

### La regla de oro: todo desde los 15 minutos

```sql
avg(CASE WHEN bloque = 'A' THEN cmg_usd_mwh END) AS "A",
avg(CASE WHEN bloque IN ('A', 'C') THEN cmg_usd_mwh END) AS "NoSolar",
```

`NoSolar` **no** se calcula como `(A * 9 + C * 5) / 14` a partir de los promedios ya
calculados: se promedia directamente sobre los cuartos de A y C juntos.

Como todos los cuartos duran 15 minutos, `AVG` **ya es** el promedio ponderado por tiempo. No
hay que ponderar a mano.

### 🔬 Caso práctico 3 — ve la diferencia

```python
from cmg_ingesta.gold import bloques_mes
# con A=100 y C=200 constantes:
#   promedio simple de los promedios: (100 + 200) / 2        = 150.00
#   promedio de los cuartos:          (100*9 + 200*5) / 14   = 135.71
```

Son **9,5% de diferencia** en el precio que cotizas. El test
`test_no_solar_sale_de_los_cuartos_no_de_promediar_promedios` afirma las dos cosas: que da
135,71 **y que no da 150**.

### El caso de abril, que es más fino

El día de 25 horas agrega 4 cuartos al bloque A. Ese mes A pesa **10 horas**, no 9:

```python
def test_la_hora_extra_de_abril_entra_al_bloque_a():
    real = (100.0 * 10 + 200.0 * 5) / 15
    assert fila["NoSolar"] == pytest.approx(real)
    assert fila["NoSolar"] != pytest.approx((100.0 * 9 + 200.0 * 5) / 14)
```

**El valor de los cuartos es el correcto. La fórmula (A×9 + C×5)/14 es un atajo que supone un
día normal.** Calcular desde el grano fino maneja el caso borde sin que nadie lo programe: eso
es la ventaja de no tomar atajos.

### `riesgo_nodal.py` — dos decisiones

**`INNER JOIN`, a propósito:**

```sql
FROM ref r JOIN comp c USING (fecha, hora, minuto)
```

Solo se comparan intervalos donde **ambas** barras tienen dato. Con `LEFT JOIN`, un intervalo
que le falta a una barra aparecería con la otra mitad nula → riesgo que se ve como cero. Un
cero falso es indistinguible de un cero real. El test
`test_riesgo_solo_usa_intervalos_con_dato_en_ambas` lo fija.

**El porcentaje es `NULL` cuando la referencia es 0:**

```sql
CASE WHEN r.cmg_referencia <> 0
     THEN (c.cmg_comparada - r.cmg_referencia) / r.cmg_referencia
     ELSE NULL END AS riesgo_pct
```

El **24,7%** de los CMg de la base son exactamente 0 (horas de sol). Y hay meses mucho peores:
en noviembre 2024, el **95,8%** del bloque B estuvo en cero.

Por eso el porcentaje confiable se calcula sobre los **promedios mensuales**, donde el
denominador casi nunca es cero. Y `intervalos_sin_porcentaje()` siempre informa sobre cuántas
filas el porcentaje no existe: un indicador que no se puede calcular hay que **declararlo**, no
rellenarlo con cero.

---

## 6. `reportes/exportar.py` — el archivo que alguien va a abrir

Cuatro decisiones, todas por cómo se usan los archivos de verdad:

**La fecha es una fecha.**

```python
make_date(anio, mes, 1) AS fecha
```

Si exportas `"2025-03"` como texto, en Excel no funcionan `=MES()` ni `=AÑO()`. Fue un reclamo
concreto tuyo en el programa anterior. El test
`test_la_fecha_del_resumen_es_un_datetime_en_excel` abre el `.xlsx` y verifica el **tipo de la
celda**, no el texto.

**El CSV usa `;` y coma decimal.**

```python
df.to_csv(ruta, sep=";", decimal=",", encoding="utf-8-sig")
```

Para que Excel en configuración chilena lo abra en columnas con doble clic. El `utf-8-sig`
agrega el BOM, que es lo que le dice a Excel que el archivo es UTF-8 y los acentos no salgan
rotos.

**Nunca se sobrescribe.**

```python
while candidato.exists():
    version += 1
    candidato = carpeta / f"{base}_v{version}{extension}"
```

Perder un exporte por repetir una consulta es barato de evitar.

**Las hojas que no caben se informan, no se tragan.**

```python
def escribir_excel(hojas, ruta) -> tuple[Path, list[str]]:
    ...
    return ruta, omitidas
```

Devolver la lista de omitidas en vez de ignorarlas es la diferencia entre un límite conocido y
un dato perdido en silencio. (Yo había escrito primero un parámetro `avisar` sin usar: código
muerto que ruff no marca porque la regla `ARG` no está activa.)

### 🔬 Caso práctico 4 — exporta y revisa el Excel

```powershell
uv run python -c "import duckdb; from pathlib import Path; from cmg_ingesta.reportes import exportar; print(exportar.exportar_cmg(duckdb.connect(), Path('data/silver/cmg'), 'A.BLANCAS_____013', (2024,1), (2024,12), Path('data/descargas')))"
```

Ábrelo y prueba `=MES(C2)` en una celda vacía de la hoja `Bloques`. Debería devolver el número
del mes. Después cambia `make_date(anio, mes, 1) AS fecha` por
`(anio || '-' || mes) AS fecha` en `bloques_mes.py`, re-exporta, y prueba `=MES()` otra vez:
ahora da `#¡VALOR!`. Ese es el bug que el test previene.

---

## 7. Glosario

| Mecanismo (EN) | En una línea | Dónde |
|---|---|---|
| Schema Mapping | adaptar la fuente al contrato propio | `cmg_db.sql_desde_cmg_db` |
| Generated SQL | generar el SQL desde la regla en Python | `bloques.sql_bloque` |
| Data Quality Check | comparar el dato contra una expectativa | `quality/checks.py` |
| Reference Table | materializar lógica como tabla para hacer JOIN | `crear_tabla_calendario` |
| Report, don't fix | reportar sin corregir en silencio | todo `checks.py` |
| SQL View | una consulta con nombre, sin copiar datos | `_entrante` |
| Dependency Injection | pasar la función en vez de llamar a `print` | `avisar` |
| TypedDict | dict con claves y tipos declarados, sin clases | `Resumen`, `FilaReporte` |
| Partition Pruning | descartar archivos por el filtro | `sql_filtro_periodo` |
| Time-Weighted Average | `AVG` sobre intervalos iguales ya pondera | `gold/bloques_mes` |
| Aggregation Bias | promediar promedios de grupos distintos | `NoSolar` |
| Inner vs Left Join | comparar solo donde hay ambos datos | `riesgo_nodal` |
| Undefined Measure | declarar `NULL` en vez de inventar 0 | `riesgo_pct` |
| Factory Fixture | una fixture que devuelve una función | `tests/conftest.py` |

---

## 8. Autoevaluación

1. ¿Por qué la migración recalcula `bloque` si la fuente ya lo trae?
2. ¿Por qué el calendario se materializa como tabla en vez de reescribirse en SQL?
3. Un día tiene 96 cuartos y `dias_con_largo_incorrecto` no reporta nada. ¿Puede estar roto?
4. ¿Qué significa una `diferencia` de +4 en abril? ¿Y de −4?
5. ¿Por qué `migrar_historico` recibe `avisar` en vez de usar `print`?
6. Encontraste un `# type: ignore` en código nuevo. ¿Qué haces antes de aceptarlo?
7. ¿Por qué `riesgo_pct` es `NULL` y no 0 cuando la referencia vale 0?
8. ¿Por qué `NoSolar` de un mes de abril no coincide con `(A×9 + C×5)/14`?

Respuestas: §1, §2 (tabla de referencia), §2 (`horas_incompletas`), §2 (tabla de diferencias),
§3 (inyección), §3 (`TypedDict`), §5 (medida indefinida), §5 (caso de abril).
