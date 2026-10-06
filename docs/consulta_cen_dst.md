# Consulta al CEN: días de cambio de hora en `costo-marginal-real/v4`

Borrador del correo y canales de contacto. Hallazgos que lo originan: `CLAUDE.md` §4.5.2 a §4.5.5.

⚠️ **No incluir la `user_key` en el correo.** Si piden identificar la cuenta, dar el nombre de la
aplicación del Portal del Desarrollador, no la clave.

---

## Correo

**Para:** `soporte.sip@coordinador.cl`
**Asunto:** API `costo-marginal-real/v4` — número de periodos en los días de cambio de hora

Estimados,

Estoy consumiendo la API pública del SIP `costo-marginal-real/v4/findByDate` en resolución
cuarto-horaria y detecté que, en los dos días de cambio de hora, la respuesta entrega **96
registros por barra**, el largo de un día normal, en circunstancias de que el día local tiene 25 o
23 horas. Agradecería confirmar si es el comportamiento esperado.

**Caso 1 — Día de 25 horas (primer sábado de abril). Faltan 4 intervalos.**

Para `bar_transf=A.BLANCAS_____013`, `startDate=endDate=2025-04-05`, la API devuelve 96 registros
con `hra` de 0 a 23. El archivo mensual `cmg2504_def_15minutal.csv` publicado por el Coordinador
entrega 100 registros para esa barra y día, con `HORA` de 1 a 25.

Al comparar intervalo por intervalo (tolerancia 1e-4):

- Las horas 0 a 21 coinciden exactamente entre ambas fuentes (88 intervalos).
- Los valores que la API entrega en `hra=23` corresponden a `HORA=25` del archivo mensual, es
  decir a la **segunda** hora 23:00 (huso GMT-4 entrante). Los 4 intervalos de la primera hora
  23:00 (`HORA=24`, GMT-3 saliente) no aparecen en la respuesta.
- Adicionalmente, los 4 intervalos de `hra=22` difieren del archivo mensual y no corresponden a
  ninguna otra hora de esa barra en el mes:

| min | API `hra=22` | `cmg2504_def` `HORA=23` (hora 22) |
|---|---|---|
| 0 | 208.03955 | 210.07436 |
| 15 | 209.42545 | 207.61723 |
| 30 | 214.21126 | 207.53861 |
| 45 | 214.02866 | 207.36169 |

El patrón se repite de forma idéntica en `STA.ELVIRA____013`, `LATORRE_______066` y
`QUELLON_______013`. Todos los registros vienen marcados `version: REAL-DEF`.

**Caso 2 — Día de 23 horas (domingo de septiembre). Sobran 4 intervalos.**

Para `bar_transf=A.BLANCAS_____013`, `startDate=endDate=2025-09-07`, la API devuelve 96 registros
incluyendo `hra=0` con `fecha_minuto` `2025-09-07 00:00` a `00:45` y valor `0.00000` en los cuatro,
marcados `version: REAL-DEF`. Ese día el reloj salta de 00:00 a 01:00, por lo que la hora 0 no
existió y el día tiene 92 intervalos reales.

**Consultas concretas**

1. ¿Es esperado que `costo-marginal-real/v4` entregue 96 periodos cuarto-horarios en estos dos
   días, o existe algún parámetro o endpoint que permita obtener los periodos efectivos
   (100 y 92 respectivamente)?
2. En el día de 25 horas, ¿cuál es el criterio de la API para resolver las dos filas con estampa
   23:00? Lo observado es que se conserva la de GMT-4 y se descarta la de GMT-3.
3. En el día de 25 horas, ¿a qué corresponde el valor de `hra=22`, que difiere del archivo mensual
   definitivo de la misma barra y fecha?

Pregunto porque las transferencias económicas y los balances mensuales deben iterar sobre los 25
periodos de ese día, y un conteo de 96 intervalos no permite reconstruirlos. La referencia
documental que estoy usando es "Documentación API Pública Sistema de Información Pública" v2.0
(julio 2023), que no aborda el tratamiento de los husos horarios ni de los días de 23 y 25 horas.

Quedo atento. Saludos cordiales,

[nombre] · [empresa] · [cargo]
Aplicación registrada en el Portal del Desarrollador: [nombre de la aplicación]

---

## Canales de contacto del CEN

| Canal | Dato | Para qué |
|---|---|---|
| **Soporte SIP** | `soporte.sip@coordinador.cl` | el canal específico de la API pública del SIP ⚠️ no verificado directamente |
| **Mesa de ayuda (tickets)** | https://coordinador.plataformagroup.cl/ | sistema de tickets ServiceTonic, deja trazabilidad |
| **Centro de Ayuda** | +56 2 2424 6300 | lun–jue 9:00–15:00, vie 9:00–12:00 |
| **Canales de Ayuda** | https://www.coordinador.cl/atencion-y-contacto/ | índice oficial, con el correo de cada plataforma |
| **Portal del Desarrollador** | https://portal.api.coordinador.cl/ | la cuenta, la `user_key` y la documentación interactiva |
| **Solicitud de acceso API SIP** | https://www.coordinador.cl/api-del-sistema-de-informacion-publica/ | formulario de acceso |

**Área responsable** (según la portada de `USO_DE_APIS_CEN_v1.0.pdf`): **Subgerencia de Ingeniería
de Software y Arquitectura**, Gerencia de Tecnología y Sistemas. Es la que implementa los cambios
de modelo de seguridad de las APIs.

**Autores documentados de la especificación del SIP** (registro de cambios del PDF v2.0), útiles
como referencia al citar el documento: Maximiliano Salinas (v1.1, diciembre 2021) y
Luis Núñez P. (v2.0, julio 2023). No hay correos individuales publicados en el documento.

**Recomendación:** abrir **ticket en la mesa de ayuda** y además enviar el correo a
`soporte.sip@coordinador.cl`. El ticket da número de seguimiento; el correo llega al equipo
específico. Si la respuesta es que el dato viene así desde el balance y no desde la API, el tema
deja de ser de Tecnología y pasa al área de operación/mercados, y conviene pedir la derivación
explícitamente.

---

## Hallazgo lateral: el rate limit SÍ está documentado

De "Documentación API Pública SIP" v2.0, §3.4:

- **60 consultas por hora por defecto**, por token. Configurable por cuenta: hay que solicitarlo
  al Coordinador.
- Cada cuenta tiene **a lo más un token activo**.
- Headers documentados: `X-Rate-Limit`, `X-Rate-Limit-Reset`, `X-Rate-Limit-Remaining`
  (el ejemplo del documento muestra `X-Rate-Limit: 600`).

⚠️ **Las respuestas de `v4` que medimos no traen esos headers.** Así que no se puede conocer el
límite en tiempo de ejecución: hay que asumir 60/hora y pedir ampliación si se necesita más.
Esto cierra la dimensión 5 del estudio, que había quedado abierta.

También notable: el ejemplo de CMg del documento usa el endpoint antiguo con campos
`"hora": 1` (**1-based**), `costo_en_dolares` y `costo_en_pesos`, y paginación con
`limit`/`offset`. El `v4` que usamos tiene `hra` (**0-based**), `cmg_usd_mwh_`, `cmg_clp_kwh_` y
paginación con `page`/`limit`/`totalPages`. Son convenciones distintas: **el documento v2.0 no
describe `v4`.**
