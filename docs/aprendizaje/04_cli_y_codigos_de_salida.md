# M7 — El CLI: la única capa que habla con el usuario

> Guía de estudio. Archivos: `src/cmg_ingesta/{cli,conexion}.py`.
> Tests: `tests/test_cli.py`.

---

## 0. La idea central

```
┌──────────────────────────────────────────────┐
│  cli.py        <- la UNICA capa que imprime  │
│                   y la unica que pregunta    │
├──────────────────────────────────────────────┤
│  reportes/  gold/  quality/  silver/  domain/ │
│                                              │
│  reciben datos  ->  devuelven datos          │
│  no imprimen, no preguntan, no deciden       │
└──────────────────────────────────────────────┘
```

Eso se llama **separación de responsabilidades (separation of concerns)**, y acá tiene una
consecuencia muy concreta.

### El anti-patrón que corrige

En el programa anterior, [`ingesta.py:234`](../../../CMG_Build/src/ingesta.py):

```python
def ingerir_csv(con, csv_path, ...):
    ...
    resp = input("\n  ¿Continuar con la ingesta? [s/N]: ").strip().lower()
    if resp not in ("s", "si", "y", "yes"):
        print("  Ingesta cancelada.")
        return 0
```

Un `input()` **en medio de la función de negocio**. Consecuencias:

1. **No se puede testear.** Para probar la ingesta hay que simular teclas.
2. **No se puede automatizar.** Un Task Scheduler no tiene a nadie que escriba `s`.
3. **No se puede reusar.** Si otro programa quiere ingerir, se le abre un prompt.

Por eso esa función terminó con un parámetro `auto_si=False` para saltarse la pregunta: el
síntoma de que la decisión estaba en el lugar equivocado.

Acá la pregunta vive en `cli.py` y la función de negocio nunca pregunta. Resultado medible:
**204 tests corren sin capturar `stdout` ni simular una sola tecla.**

---

## 1. Los códigos de salida

```python
SALIDA_OK = 0
SALIDA_ERROR = 1
SALIDA_CON_HALLAZGOS = 2
```

Un programa de línea de comandos se comunica con quien lo invoca de dos formas: lo que imprime
(para humanos) y **su código de salida** (para máquinas).

| Código | Significa | Qué debería hacer el que lo llama |
|---|---|---|
| 0 | todo bien | seguir |
| 1 | error de uso o del programa | detenerse y avisar |
| **2** | terminó, pero las validaciones encontraron algo | **cargó, pero hay que revisar** |

El **2 es el interesante**. Si pones `cmg migrar-historico` en el Task Scheduler:

- exit 1 → *no cargó nada*, hay que intervenir ya
- exit 2 → *cargó, pero el dato tiene problemas conocidos*, revisar el reporte

Sin ese tercer código tendrías que elegir entre mentir (devolver 0 con datos defectuosos) o
alarmar de más (devolver 1 cuando sí cargó).

### Cómo se devuelve en typer

```python
raise typer.Exit(SALIDA_CON_HALLAZGOS)
```

Es una **excepción**, no un `return`. Eso permite salir desde cualquier profundidad sin tener que
propagar un valor de retorno por toda la cadena de llamadas.

### 🔬 Caso práctico 1 — mira los códigos

```powershell
$env:CMGI_DATA_DIR = "data"

cmg estado                      ; "exit=$LASTEXITCODE"   # 0
cmg bloques NO_EXISTE           ; "exit=$LASTEXITCODE"   # 1
cmg bloques BARRA --periodo ayer; "exit=$LASTEXITCODE"   # 1
```

En PowerShell el código del último comando está en `$LASTEXITCODE`. En bash, en `$?`.

**Rómpelo:** en `cli.py`, cambia `raise typer.Exit(SALIDA_ERROR)` por `return` en el comando
`bloques`. Corre `uv run pytest tests/test_cli.py -q`: va a fallar
`test_bloques_barra_inexistente`, porque el programa ahora dice "todo bien" después de no hacer
nada.

---

## 2. `stdout` y `stderr` no son lo mismo

```python
typer.echo(f"Error: {e}", err=True)      # va a stderr
typer.echo(df.to_string())               # va a stdout
```

**Los datos van a `stdout`; los mensajes para el humano van a `stderr`.** La razón práctica:

```powershell
cmg bloques BARRA_1 > resultado.txt
```

Con esa redirección, `resultado.txt` queda con la tabla **limpia**, sin mensajes de error
mezclados. Si todo fuera a `stdout`, el archivo tendría basura.

### Esto hizo fallar 4 tests

```python
assert "no reconocido" in r.stdout     # falla: esta en stderr
assert "no reconocido" in r.stderr     # correcto
```

`CliRunner` separa las dos corrientes. Es el mismo detalle que ya habías anotado en
`ppa-pipeline` H2.2, y se olvida siempre.

---

## 3. `conexion.py` — una sola forma de abrir DuckDB

El legado tenía **dos** funciones para lo mismo:

| | `core.abrir_duckdb` | `comun.conexion.abrir` |
|---|---|---|
| threads | parámetro | fijo en 4 |
| memory_limit | `"4GB"` | opcional |
| temp_directory | `.tmp` | `_duckdb_tmp` |
| crea carpetas | no | **sí, al llamarse** |

O sea que el mismo dato se leía con límites distintos según por dónde entraras. Acá hay una
sola función.

### El detalle que importa: `temp_directory`

```python
con.execute("SET temp_directory=?", [str(spill)])
```

Cuando una consulta no cabe en memoria, DuckDB **desborda a disco**. Sin esta línea desborda al
temporal del sistema, que en Windows está en `C:`. Con una base de 200 millones de filas eso
puede llenar el disco de arranque — y ya nos quedamos con 9,9 GB libres una vez en esta sesión.

Fíjate que se pasa como **parámetro** (`?`) y no interpolado: una ruta con apóstrofo rompería el
SQL.

---

## 4. `Annotated`, la forma moderna de declarar argumentos

```python
def descargar(
    barra: Annotated[str, typer.Argument(help="Nombre exacto de la barra.")],
    periodo_texto: Annotated[str, typer.Option("--periodo", help="...")] = "",
) -> None:
```

`Annotated[str, ...]` dice dos cosas a la vez: **para mypy** el tipo es `str`, y **para typer**
los metadatos de cómo se expone en la línea de comandos.

El estilo antiguo era `barra: str = typer.Argument(...)`, que le miente al verificador de tipos:
declara `str` pero el valor por defecto es un objeto de typer. Con `mypy --strict` eso da error.

### El `try` / `finally` que siempre cierra

```python
con = conexion.abrir(cfg.data_dir)
try:
    ...
except ValueError as e:
    typer.echo(f"Error: {e}", err=True)
    raise typer.Exit(SALIDA_ERROR) from e
finally:
    con.close()
```

El `finally` cierra la conexión **pase lo que pase**, incluso saliendo por la excepción.

Y el `from e` preserva la excepción original en la cadena (*exception chaining*): si algo
inesperado pasa, el traceback muestra la causa real y no solo el `typer.Exit`.

---

## 5. Los tests del CLI

### `CliRunner` invoca sin lanzar un proceso

```python
runner = CliRunner()
r = runner.invoke(cli.app, ["bloques", "BARRA_1"])
assert r.exit_code == cli.SALIDA_OK
```

No arranca `python` ni una terminal: llama a la función en el mismo proceso y captura las
corrientes. Por eso los 17 tests del CLI corren en 7 segundos.

### La fixture de aislamiento

```python
@pytest.fixture(autouse=True)
def entorno(monkeypatch, tmp_path) -> Path:
    for clave in list(os.environ):
        if clave.startswith("CMGI_"):
            monkeypatch.delenv(clave, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CMGI_DATA_DIR", str(tmp_path / "data"))
    return tmp_path
```

Tres cosas, y cada una evita un problema real:

1. **Borra todas las `CMGI_*`** barriendo el prefijo, no nombrándolas una por una. Así no se
   desincroniza cuando agregues un campo a `Settings`.
2. **`chdir(tmp_path)`** para que no se lea tu `.env` real.
3. **Apunta `CMGI_DATA_DIR` a una carpeta temporal**, así los tests no escriben en tu base.

Sin esto, un test que corre bien en tu máquina falla en CI, o peor: **te sobrescribe los datos**.

### Verificar el código de salida, no solo el texto

```python
def test_migrar_con_hallazgos_sale_con_codigo_2(con, entorno):
    # siembra un septiembre con la hora fantasma
    ...
    r = runner.invoke(cli.app, ["migrar-historico", str(vieja)])
    assert r.exit_code == cli.SALIDA_CON_HALLAZGOS
    assert "validaciones encontraron problemas" in r.stdout
```

El texto es para el humano; **el código es el contrato con la máquina**. Un test que solo mira
el texto deja pasar un cambio que rompa la automatización.

### 🔬 Caso práctico 2 — agrega un comando

Agrega un comando que muestre las barras más caras de un mes:

```python
@app.command()
def ranking(
    periodo_texto: Annotated[str, typer.Option("--periodo")] = "",
    top: Annotated[int, typer.Option(help="Cuantas mostrar.")] = 20,
) -> None:
    """Las barras con mayor CMg promedio en el periodo."""
    cfg = _settings()
    base = conexion.ruta_silver(cfg.data_dir)
    con = conexion.abrir(cfg.data_dir)
    try:
        primero, ultimo = leer.rango_disponible(con, base)
        desde, hasta = periodo.parsear_periodo(periodo_texto, primero, ultimo)
        # TODO: la consulta. Pista: agrupa por barra, usa leer.sql_filtro_periodo
        #       y leer.sql_dataset, y ordena descendente con LIMIT.
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(SALIDA_ERROR) from e
    finally:
        con.close()
```

Fíjate en el patrón que se repite en los 5 comandos: abrir, resolver el periodo, consultar,
cerrar en `finally`, y traducir `ValueError` a exit 1. Esa repetición es deliberada y legible;
abstraerla en un decorador la volvería más corta y más difícil de seguir.

Después agrega su test en `tests/test_cli.py` usando `con_base` y verifica el exit code.

---

## 6. Glosario

| Mecanismo (EN) | En una línea | Dónde |
|---|---|---|
| Separation of Concerns | cada capa hace una cosa | `cli.py` vs. el resto |
| Exit Code | cómo un programa le habla a la máquina | `SALIDA_*` |
| stdout vs stderr | datos vs. mensajes | `err=True` |
| Exception for control flow | `typer.Exit` sale desde cualquier nivel | los comandos |
| Exception Chaining | `raise ... from e` preserva la causa | los `except` |
| try/finally | liberar recursos pase lo que pase | `con.close()` |
| Annotated types | tipo para mypy + metadatos para typer | las firmas |
| Spill directory | dónde desborda DuckDB si no cabe en RAM | `conexion.abrir` |
| Test Isolation | el test no toca tu entorno ni tus datos | fixture `entorno` |
| CliRunner | invocar el CLI sin lanzar un proceso | `tests/test_cli.py` |

---

## 7. Autoevaluación

1. ¿Por qué `input()` dentro de `ingerir_csv()` hacía imposible testear esa función?
2. ¿Qué diferencia práctica hay entre salir con 1 y salir con 2?
3. Redirigís la salida a un archivo con `>`. ¿Por qué los errores no deberían aparecer ahí?
4. ¿Qué pasa si no se configura `temp_directory` en DuckDB y corrés una consulta grande?
5. ¿Por qué `Annotated[str, typer.Option(...)]` y no `str = typer.Option(...)`?
6. La fixture `entorno` borra las variables barriendo el prefijo. ¿Qué problema evita respecto a
   nombrarlas una por una?
7. Un test del CLI verifica el texto pero no el `exit_code`. ¿Qué cambio podría pasar sin que
   nadie lo note?

Respuestas: §0 (anti-patrón), §1 (tabla de códigos), §2 (redirección), §3 (`temp_directory`),
§4 (`Annotated`), §5 (fixture), §5 (contrato con la máquina).
