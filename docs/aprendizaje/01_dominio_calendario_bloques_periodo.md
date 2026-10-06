# M1 — La capa de dominio: calendario, bloques y periodo

> Guía de estudio. Cada sección tiene **qué es**, **por qué así** y un **caso práctico**
> que puedes ejecutar y romper a propósito para ver qué pasa.

Archivos: `src/cmg_ingesta/domain/{calendario,bloques,periodo}.py`
Tests: `tests/domain/test_{calendario,bloques,periodo}.py`

---

## 0. Qué es la "capa de dominio" y por qué va primero

En la arquitectura **Medallion** los datos pasan por Bronze → Silver → Gold. La capa de
**dominio** no es ninguna de esas tres: es el lugar donde viven las **reglas de negocio**, y las
otras tres la usan.

```
                       ┌─────────────────┐
                       │     domain/     │  reglas puras, sin I/O
                       │ calendario      │  <- lo construimos ahora
                       │ bloques         │
                       │ periodo         │
                       └────────┬────────┘
                                │  lo usan todas las capas
         ┌──────────────┬───────┴───────┬──────────────┐
      bronze/        silver/          gold/        reportes/
     crudo tal      limpio y        agregados      excel, csv
     como vino     conformado       de negocio
```

**La regla que define el dominio: ninguna función de `domain/` lee archivos, consulta bases ni
sale a la red.** Recibe datos, devuelve datos. Eso se llama **función pura (pure function)**, y
tiene tres consecuencias prácticas:

1. Se testea en milisegundos, sin preparar nada.
2. Dos llamadas con la misma entrada dan siempre lo mismo. No hay "funcionó ayer".
3. El error, cuando aparece, está en la regla y no en la plomería.

Por eso M1 va primero: es la parte donde **todo el estudio E0 se convierte en código**.

---

## 1. `calendario.py` — el largo de un día

### Qué problema resuelve

Un día del SEN **no siempre tiene 96 cuartos de hora**:

| Día | Horas | Cuartos |
|---|---|---|
| normal | 24 | 96 |
| primer sábado de abril | **25** | **100** |
| domingo DST de septiembre | **23** | **92** |

Si un script asume 96, en abril pierde una hora y en septiembre inventa una. Eso ya pasó: la API
del CEN devuelve 96 en los dos casos (CLAUDE.md §4.5.5).

### La idea central: derivar, no codificar

Hay dos maneras de saber qué día tiene 25 horas.

**Mala:** mantener una lista de fechas.

```python
DIAS_LARGOS = [date(2021, 4, 3), date(2022, 4, 2), ...]   # NO
```

Falla el año que nadie la actualice, y falla si Chile cambia la norma.

**Buena:** preguntárselo al sistema operativo, que ya tiene la base de zonas horarias IANA.

```python
def _medianoche_utc(dia: date) -> datetime:
    return datetime(dia.year, dia.month, dia.day, tzinfo=TZ).astimezone(UTC)

def horas_del_dia(dia: date) -> int:
    duracion = _medianoche_utc(dia + timedelta(days=1)) - _medianoche_utc(dia)
    return int(duracion / timedelta(hours=1))
```

### Por qué UTC y no restar fechas locales

Esto es **lo más importante del módulo**. Un `datetime` con `tzinfo=TZ` es una **etiqueta de reloj
de pared**. Si restas dos medianoches locales, Python te da 24 horas siempre, porque está restando
etiquetas, no tiempo.

**UTC es el único reloj que no salta.** Al convertir los dos extremos a UTC, la resta mide
**tiempo físico transcurrido**, y ahí aparecen las 23 o las 25 horas.

Es el mismo principio que la NTSyCS le exige a los medidores del SEN: sincronización GPS
referenciada a UTC. La función hace lo que hace un medidor.

### 🔬 Caso práctico 1 — compruébalo tú

Crea `estudio/practica_01.py`:

```python
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
UTC = ZoneInfo("UTC")

dia = date(2025, 4, 5)          # el dia de 25 horas
sig = dia + timedelta(days=1)

# A) restando fechas LOCALES (lo que NO hay que hacer)
a = datetime(dia.year, dia.month, dia.day, tzinfo=TZ)
b = datetime(sig.year, sig.month, sig.day, tzinfo=TZ)
print("local:", b - a)

# B) convirtiendo a UTC primero (lo correcto)
print("utc  :", b.astimezone(UTC) - a.astimezone(UTC))
```

```powershell
uv run python estudio/practica_01.py
```

**Lo que vas a ver:** el caso A dice `1 day, 0:00:00` y el B dice `1 day, 1:00:00`.
Esa hora de diferencia es exactamente la que la API del CEN pierde.

**Rómpelo:** prueba con `date(2025, 9, 7)`. El caso B debe dar **23 horas**.

### `horas_esperadas` — por qué el total del día no basta

El total de cuartos **no alcanza** para validar. Un día podría tener 96 cuartos repartidos mal:
la hora 5 con 3 cuartos y la hora 6 con 5. El total cuadra y el dato está roto.

Por eso existe `horas_esperadas(dia)`, que devuelve **qué índices de hora debe tener el día**:

```python
dia normal   -> [0, 1, ..., 23]
abril        -> [0, 1, ..., 23, 24]   la 24 es la SEGUNDA 23:00
septiembre   -> [1, 2, ..., 23]       sin la hora 0
```

Y las otras dos funciones se derivan de ahí, así que **no pueden contradecirse**:

```python
def cuartos_esperados(dia): return horas_del_dia(dia) * 4
```

Eso se llama **única fuente de verdad (single source of truth)**. Antes había dos copias del
mismo cálculo: si arreglabas un caso borde en una y olvidabas la otra, `horas_del_dia` podía decir
25 y `cuartos_esperados` 96, y nada avisaba.

### `horas_inexistentes` — el truco de la ida y vuelta

```python
local = datetime(dia.year, dia.month, dia.day, hora, tzinfo=TZ)
vuelta = local.astimezone(UTC).astimezone(TZ)
if vuelta.hour != hora:
    resultado.append(hora)
```

Si una hora **no existió** (el reloj saltó de 00:00 a 01:00), `zoneinfo` la resuelve con el offset
anterior a la transición. Al convertir a UTC y volver, cae en otra hora. Si la hora vuelve
distinta de como entró, es que no existe.

### 🔬 Caso práctico 2 — la hora que no existe

```python
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
UTC = ZoneInfo("UTC")

for hora in (0, 1, 2):
    local = datetime(2025, 9, 7, hora, tzinfo=TZ)
    vuelta = local.astimezone(UTC).astimezone(TZ)
    print(f"pedi hora {hora} -> quedo {vuelta.hour}  "
          f"{'NO EXISTE' if vuelta.hour != hora else 'ok'}")
```

**Lo que vas a ver:** la hora 0 vuelve como 1. Las otras vuelven iguales.

---

## 2. `bloques.py` — A, B, C y los perfiles

### Los bloques

```
A: 00:00-07:59 y 23:00-23:59   (9 horas)
B: 08:00-17:59                 (10 horas)
C: 18:00-22:59                 (5 horas)
```

A está **partido**: agarra la madrugada y la última hora del día. Por eso una cadena de
`if hora < 8 ... elif` se vuelve confusa, y usamos una **tabla de lookup**:

```python
BLOQUE_POR_HORA: dict[int, Bloque] = {
    **{hora: "A" for hora in range(0, 8)},
    **{hora: "B" for hora in range(8, 18)},
    **{hora: "C" for hora in range(18, 23)},
    23: "A",
    24: "A",   # la hora extra de abril
}
```

Dos razones para la tabla:

1. **Se lee como la regla de negocio**, no como lógica.
2. **Se puede vectorizar.** Cuando tengas 300 millones de filas en pandas, vas a escribir
   `df["hora"].map(BLOQUE_POR_HORA)`, que opera sobre la columna completa en C. La alternativa,
   `df.apply(bloque_de_hora, axis=1)`, llama a Python una vez por fila y es entre 50 y 100 veces
   más lenta.

### El guard clause

```python
if hora not in BLOQUE_POR_HORA:
    raise ValueError(
        f"hora fuera de rango: {hora!r}. Se espera 0 a 24 "
        "(¿el dato viene en base 1 y falta restarle 1?)"
    )
```

**Validar la entrada al principio y fallar con un mensaje útil** se llama *guard clause*. El
mensaje no dice solo "error": dice **cuál es la causa más probable**. Y es la causa real: el ZIP
del CEN trae `HORA` en base 1 (1 a 25) y la API en base 0 (0 a 23). Confundirlas corre todo una
hora en silencio.

### Por qué NoSolar se pondera

```python
(A * 9 + C * 5) / 14
```

Un promedio simple `(A + C) / 2` le daría a C casi el doble del peso que le toca, porque A dura
9 horas y C solo 5. El nombre técnico del error es **sesgo de agregación (aggregation bias)**:
promediar promedios de grupos de distinto tamaño.

### 🔬 Caso práctico 3 — ve el sesgo con números

```python
from cmg_ingesta.domain import bloques

a, c = 100.0, 200.0
print("ponderado:", bloques.cmg_no_solar(a, c))
print("simple   :", (a + c) / 2)
```

**Lo que vas a ver:** 135,71 contra 150. Un **10,5% de diferencia** en el precio que le cotizas a
un cliente.

**Rómpelo:** en `bloques.py` cambia `HORAS_C = 5` por `HORAS_C = 9` y corre
`uv run pytest tests/domain/test_bloques.py`. Van a fallar **dos** tests: el de la ponderación y
el de que las horas suman 24. El segundo es el que te dice *por qué* está mal.

---

## 3. `periodo.py` — interpretar lo que escribe el usuario

### El problema de diseño

El usuario escribe `"ultimos 6"`. Para resolverlo hay que saber cuál es el último mes disponible,
y eso está en la base — o sea, es I/O. ¿Entonces `periodo.py` deja de ser puro?

**No: se inyecta.**

```python
def parsear_periodo(texto: str, primero: Mes, ultimo: Mes) -> tuple[Mes, Mes]:
```

Quien llama consulta la base y le pasa el rango. La función sigue siendo pura y testeable sin
preparar nada. Eso es **inyección de dependencias (dependency injection)** en su forma más simple:
pasar el dato en vez de ir a buscarlo.

### Por qué una tupla y no una clase

```python
Mes = tuple[int, int]
```

Un mes es `(2025, 3)`. Es **un dato, no un objeto**: no tiene comportamiento propio ni estado que
cuidar. Una clase ahí sería ceremonia sin beneficio. La regla práctica: *no uses clases donde
bastan funciones.*

### La aritmética de meses

Sumar meses es incómodo porque diciembre + 1 = enero del año siguiente. El truco es pasar a un
número corrido:

```python
def indice(mes):       return mes[0] * 12 + (mes[1] - 1)
def desde_indice(n):   return (n // 12, n % 12 + 1)
```

Con eso "los últimos 6 meses" es una resta, y cruzar el año sale gratis. El test
`test_indice_y_desde_indice_son_inversos` comprueba que la ida y la vuelta coinciden para 36
meses: eso se llama **test de propiedad (property-based)**, porque verifica una *propiedad* y no
un caso suelto.

### El recorte al rango disponible

```python
desde = desde_indice(max(indice(desde), indice(primero)))
hasta = desde_indice(min(indice(hasta), indice(ultimo)))
```

Si pides `"2026"` y la base llega hasta julio, te da enero–julio en vez de un rango vacío o un
error. Pero si pides algo **completamente** fuera, falla con un mensaje que dice qué hay
disponible. La diferencia importa: recortar lo parcialmente válido es cortesía; aceptar lo
totalmente inválido es esconder un error.

### 🔬 Caso práctico 4 — explora el parser

```python
from cmg_ingesta.domain import periodo

PRIMERO, ULTIMO = (2021, 1), (2026, 7)
for texto in ["", "2025", "2025-03", "2025-03 a 2025-08", "ultimos 6",
              "2026", "2018-01 a 2019-12"]:
    try:
        d, h = periodo.parsear_periodo(texto, PRIMERO, ULTIMO)
        print(f"{texto!r:<22} -> {periodo.formatear(d)} .. {periodo.formatear(h)}")
    except ValueError as e:
        print(f"{texto!r:<22} -> ERROR: {e}")
```

**Lo que vas a ver:** `"2026"` se recorta a julio, y `"2018-01 a 2019-12"` falla con un mensaje
que te dice el rango real.

---

## 4. Lo que hacen los tests, y por qué hay 106

No son 106 casos escritos a mano: son **parametrizados**. Un solo test con
`@pytest.mark.parametrize` corre una vez por fecha de la lista.

Tres clases de test que vale distinguir:

**a) Casos medidos.** Las fechas de `DIAS_LARGOS` y `DIAS_CORTOS` no las inventé: salieron de
consultar `CMG_DB` y la API durante el estudio E0. El test es la **prueba de regresión** de ese
estudio: si alguien rompe el calendario, estos tests lo detienen.

**b) Casos de borde (boundary values).** `(7, "A")`, `(8, "B")`, `(17, "B")`, `(18, "C")`,
`(22, "C")`, `(23, "A")`. Los bugs viven en los bordes, no en el medio. Nadie se equivoca con la
hora 12.

**c) Invariantes.** Los más valiosos, porque atrapan errores que no imaginaste:

```python
def test_un_año_completo_suma_las_horas_del_calendario():
    # el dia largo aporta +1 hora y el corto -1: se anulan
    assert total_horas == 365 * 24

def test_solo_hay_un_dia_largo_y_uno_corto_por_año():
    # si algun año tuviera dos, la validacion estaria mal pensada
```

El segundo recorre **2021 a 2030** y verifica que cada año tenga exactamente un día de 25 horas y
uno de 23. Eso no es un caso: es una afirmación sobre cómo funciona Chile, puesta a prueba
automáticamente para diez años.

### 🔬 Caso práctico 5 — rompe algo y mira qué test te salva

Cambia en `calendario.py`:

```python
HORA_EXTRA = 24     ->     HORA_EXTRA = 25
```

```powershell
uv run pytest tests/domain/test_calendario.py -q
```

**Lo que vas a ver:** falla `test_dia_largo_abril` porque `horas_esperadas` ya no coincide con
`[*range(24), 24]`. Pero **no** falla `cuartos_esperados`, porque la cantidad no cambió.

Eso te enseña algo real: un test que cuenta no detecta un error de **etiqueta**. Hacen falta los
dos tipos de aserción.

---

## 5. Glosario de lo que aparece acá

| Mecanismo (EN) | En una línea | Dónde |
|---|---|---|
| Pure Function | misma entrada → misma salida, sin I/O | todo `domain/` |
| Single Source of Truth | un solo lugar define cada regla | `horas_esperadas` |
| Guard Clause | validar al entrar y fallar claro | `bloque_de_hora` |
| Vectorization | operar la columna completa, no fila por fila | `BLOQUE_POR_HORA` + `.map()` |
| Aggregation Bias | promediar promedios de grupos distintos | `cmg_no_solar` |
| Dependency Injection | pasar el dato en vez de buscarlo | `parsear_periodo` |
| Boundary Value Testing | probar los bordes (7\|8, 17\|18, 22\|23) | `test_bloques` |
| Property-Based Test | verificar una propiedad, no un caso | `indice` / `desde_indice` |
| Regression Test | congelar lo medido para que nadie lo rompa | fechas de E0 |
| Parametrize | un test, muchos casos | `@pytest.mark.parametrize` |

---

## 6. Autoevaluación

1. ¿Por qué `horas_del_dia` convierte a UTC antes de restar?
2. Un día tiene 96 cuartos. ¿Alcanza para decir que está completo? ¿Por qué?
3. ¿Por qué `BLOQUE_POR_HORA` es un diccionario y no una cadena de `if`?
4. Si `(A + C) / 2` da 150 y lo correcto es 135,71, ¿de dónde sale la diferencia?
5. ¿Por qué `parsear_periodo` recibe `primero` y `ultimo` en vez de consultar la base?
6. ¿Qué atrapa `test_solo_hay_un_dia_largo_y_uno_corto_por_año` que no atraparía un test de una
   fecha suelta?

Respuestas: §1 (UTC), §1 (`horas_esperadas`), §2 (tabla), §2 (ponderación), §3 (inyección),
§4c (invariantes).
