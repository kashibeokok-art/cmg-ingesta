# Guías de estudio del código

Cada guía explica **qué es**, **por qué así** y trae **casos prácticos** que puedes ejecutar y
romper a propósito para ver qué test te salva.

Conviene leerlas en orden: cada una usa lo de la anterior.

| # | Guía | Cubre | Archivos |
|---|---|---|---|
| 01 | [Dominio: calendario, bloques y periodo](01_dominio_calendario_bloques_periodo.md) | funciones puras, el largo real de un día, bloques A/B/C, parseo de periodos | `domain/` |
| 02 | [Esquema y escritura atómica](02_esquema_y_escritura_atomica.md) | contrato de datos, clave natural, particiones Hive, atomicidad, idempotencia | `domain/esquema.py`, `silver/escribir.py` |
| 03 | [Migración, validación y reportes](03_migracion_validacion_y_reportes.md) | mapeo de fuentes, validación contra calendario, capa Gold, exportes | `extract/`, `quality/`, `gold/`, `reportes/` |
| 04 | [CLI y códigos de salida](04_cli_y_codigos_de_salida.md) | separación de capas, exit codes, stdout vs stderr, tests del CLI | `cli.py`, `conexion.py` |
| 05 | [La página del CEN: descargar, vigilar, ingerir](05_fuente_pagina_descarga_vigilancia_ingesta.md) | Bronze/Silver, inyección de dependencias, deriva, completitud, la trampa de los ceros, ingesta incremental, vista vs tabla | `extract/coordinador_cmg.py`, `quality/deriva.py`, `extract/{pagina_cen,ingerir_cen}.py` |

## Si solo vas a leer una cosa

Los tres conceptos que más veces nos ahorraron un error en este proyecto:

1. **Derivar en vez de codificar** (guía 01, §1). El largo de un día sale de `zoneinfo`, no de
   una lista de fechas. Por eso funciona para cualquier año sin mantenerla.
2. **Validar contra una expectativa** (guía 03, §2). El defecto de fechas de 2021/2022/2024
   llevaba años sin detectarse porque nadie tenía con qué comparar.
3. **Calcular desde el grano más fino** (guía 03, §5). `NoSolar` desde los cuartos y no
   promediando promedios: 135,71 en vez de 150, un 9,5% en el precio que cotizas.

## Y los errores ya cometidos

[`docs/errores_verificados.md`](../errores_verificados.md) tiene los errores que Claude **ya
cometió** en este proyecto, con la evidencia que los desmintió. Leerlo cuesta cinco minutos y
evita repetirlos.
