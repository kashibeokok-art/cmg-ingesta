-- =============================================================================
--  Base cmg_cen: CMg cuarto-horario por barra, con documentos y revisiones
-- =============================================================================
--
-- Dos capas de cmg-ingesta, para que cada usuario pida el nivel que necesita:
--
--     Silver  cmg.cmg_15min           cada barra cada 15 minutos (el dato fino)
--     Gold    cmg.cmg_mensual         por barra y mes: A, B, C, Solar, NoSolar, Total
--             cmg.cmg_mensual_bloque  por barra, mes y bloque: promedio, min, max, desvio
--
-- mas dos cosas que Silver no guarda: QUE documento del Coordinador esta cargado en
-- cada dia (cmg.dia), y el HISTORIAL de cada vez que un dia cambio de version
-- (cmg.revision: pre -> def, def -> v2...).
-- La llena el comando `cmg publicar-sql`; la consulta la API.
--
-- Como correrlo (UNA vez, con un usuario administrador, ya creada la base):
--
--     psql -h 192.168.0.85 -p 5433 -U postgres -d cmg_cen -f sql/cmg_cen.sql
--
-- Se puede volver a correr sin romper nada (todo es "si no existe").
--
-- Despues, ponerle clave a los dos usuarios (las claves NO van en este archivo,
-- que esta en git):
--
--     \password cmg_carga      -- la usa `cmg publicar-sql` (escribe)
--     \password cmg_lectura    -- la usa la API (solo lee)
--
-- Tamaño esperado: ~300 millones de filas a 2026-10 (+~58 millones por año).


BEGIN;

-- ------------------------------------------------------------------ usuarios

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cmg_carga') THEN
        CREATE ROLE cmg_carga LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cmg_lectura') THEN
        CREATE ROLE cmg_lectura LOGIN;
    END IF;
END
$$;

-- Todo vive en el esquema `cmg`, y su dueño es cmg_carga: asi la carga puede
-- crear y reemplazar las particiones de cada mes sin ser administradora.
CREATE SCHEMA IF NOT EXISTS cmg AUTHORIZATION cmg_carga;
GRANT USAGE ON SCHEMA cmg TO cmg_lectura;
-- en la base donde se corre el script (cmg_cen, o una de pruebas)
DO $$ BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO cmg_carga, cmg_lectura', current_database());
END $$;

SET ROLE cmg_carga;  -- lo que sigue queda a nombre de cmg_carga

-- ------------------------------------------------------------- dimensiones

-- De donde viene cada fila. Los mismos valores que la columna `origen` de Silver.
CREATE TABLE IF NOT EXISTS cmg.origen (
    origen_id  smallint PRIMARY KEY,
    nombre     text NOT NULL UNIQUE,
    detalle    text NOT NULL
);
INSERT INTO cmg.origen VALUES
    (1, 'maestro_cmg_db', 'Archivo Maestro (CMG_DB), 2021-01 a 2024-07'),
    (2, 'pagina_cen_def', 'Pagina del Coordinador, definitivo'),
    (3, 'pagina_cen_pre', 'Pagina del Coordinador, preliminar: puede cambiar')
ON CONFLICT (origen_id) DO NOTHING;

-- El nombre de cada barra se guarda UNA vez; los datos usan el numero (2 bytes
-- por fila en vez de ~18, en 300 millones de filas).
CREATE TABLE IF NOT EXISTS cmg.barra (
    barra_id  smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre    text NOT NULL UNIQUE                   -- 'A.BLANCAS_____013'
);

-- ------------------------------------------------------------------- datos

-- Una fila por barra y cuarto de hora. Una particion por mes (cmg.cuarto_AAAA_MM),
-- igual que Silver: la carga reemplaza un mes completo de una sola vez.
CREATE TABLE IF NOT EXISTS cmg.cuarto (
    barra_id     smallint NOT NULL REFERENCES cmg.barra,
    fecha        date     NOT NULL,
    hora         smallint NOT NULL CHECK (hora BETWEEN 0 AND 24),  -- 24 = segunda 23:00 de abril
    minuto       smallint NOT NULL CHECK (minuto IN (0, 15, 30, 45)),
    bloque       char(1)  NOT NULL CHECK (bloque IN ('A', 'B', 'C')),
    cmg_usd_mwh  double precision NOT NULL,
    origen_id    smallint NOT NULL REFERENCES cmg.origen,
    PRIMARY KEY (barra_id, fecha, hora, minuto)
) PARTITION BY RANGE (fecha);

-- ----------------------------------------------------- documentos y revisiones

-- Cada ZIP del Coordinador que se descargo (el manifiesto del Bronze), este o no
-- en uso. Permite responder "que publico el Coordinador para este dia".
CREATE TABLE IF NOT EXISTS cmg.documento (
    nombre         text PRIMARY KEY,       -- Antecedentes_CMG_Real_def_260115.zip
    fecha          date NOT NULL,          -- dia de operacion
    tipo           text NOT NULL CHECK (tipo IN ('pre', 'def', 'desconocido')),
    version        smallint NOT NULL,
    reemision      smallint NOT NULL,
    publicado      date,                   -- fecha de publicacion en la pagina
    descargado_en  timestamp,
    sha256         char(64) NOT NULL,
    bytes          bigint NOT NULL,
    url            text NOT NULL
);
CREATE INDEX IF NOT EXISTS documento_fecha ON cmg.documento (fecha);

-- Que hay cargado HOY en cada dia: su origen y, si viene de la pagina, de que
-- documento. Un dia en 'pagina_cen_pre' todavia puede cambiar.
CREATE TABLE IF NOT EXISTS cmg.dia (
    fecha       date PRIMARY KEY,
    origen_id   smallint NOT NULL REFERENCES cmg.origen,
    documento   text REFERENCES cmg.documento,   -- NULL para el Maestro
    cargado_en  timestamptz NOT NULL
);

-- Historial: una fila cada vez que un dia YA cargado cambio de version, con
-- cuanto cambio el CMg. Es lo que responde "que se reviso y cuando".
CREATE TABLE IF NOT EXISTS cmg.revision (
    revision_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha              date NOT NULL,
    origen_antes       smallint REFERENCES cmg.origen,
    origen_despues     smallint NOT NULL REFERENCES cmg.origen,
    documento_antes    text,
    documento_despues  text,
    publicado          date,                 -- cuando publico el CEN la version nueva
    detectado_en       timestamptz NOT NULL, -- cuando la cargo esta base
    cuartos            integer NOT NULL,     -- cuartos comparados (barra x cuarto de hora)
    cuartos_distintos  integer NOT NULL,     -- cuantos cambiaron de valor
    dif_max_usd        double precision,     -- el mayor cambio, en USD/MWh
    dif_prom_usd       double precision      -- cambio promedio (absoluto) de los que cambiaron
);
CREATE INDEX IF NOT EXISTS revision_fecha ON cmg.revision (fecha);
CREATE INDEX IF NOT EXISTS revision_detectado ON cmg.revision (detectado_en);

-- Que version de cada mes de Silver esta cargada (huella = sha256 del archivo).
CREATE TABLE IF NOT EXISTS cmg.carga_mes (
    anio        smallint NOT NULL,
    mes         smallint NOT NULL CHECK (mes BETWEEN 1 AND 12),
    huella      char(64) NOT NULL,
    filas       bigint NOT NULL,
    cargado_en  timestamptz NOT NULL,
    PRIMARY KEY (anio, mes)
);
-- cuantos dias tiene el mes y cuantos son preliminares (pueden cambiar)
ALTER TABLE cmg.carga_mes ADD COLUMN IF NOT EXISTS dias smallint;
ALTER TABLE cmg.carga_mes ADD COLUMN IF NOT EXISTS dias_preliminar smallint;

-- -------------------------------------------------------------------- Gold

-- Los resumenes del negocio, calculados desde los cuartos de hora (NUNCA
-- promediando promedios) en la MISMA transaccion en que se carga el mes de
-- `cmg.cuarto`: un mes de Gold siempre corresponde a su Silver, y si el mes se
-- revisa (pre -> def, v2), su resumen se rehace solo. Misma regla que
-- gold/bloques_mes.py del programa (un test exige que den lo mismo).

-- Una fila por barra y mes: lo que se usa para cotizar.
CREATE TABLE IF NOT EXISTS cmg.mes_barra (
    barra_id   smallint NOT NULL REFERENCES cmg.barra,
    fecha      date     NOT NULL CHECK (extract(day FROM fecha) = 1),  -- primer dia del mes
    cuartos    integer  NOT NULL,
    a          double precision,          -- promedio de los cuartos del bloque A
    b          double precision,          -- bloque B (= Solar)
    c          double precision,          -- bloque C
    no_solar   double precision,          -- cuartos de A y C JUNTOS, no el promedio de sus promedios
    total      double precision,
    PRIMARY KEY (barra_id, fecha)
);

-- Una fila por barra, mes y bloque, con la dispersion.
CREATE TABLE IF NOT EXISTS cmg.mes_bloque (
    barra_id       smallint NOT NULL REFERENCES cmg.barra,
    fecha          date     NOT NULL CHECK (extract(day FROM fecha) = 1),
    bloque         char(1)  NOT NULL CHECK (bloque IN ('A', 'B', 'C')),
    cuartos        integer  NOT NULL,
    promedio       double precision NOT NULL,
    desv_estandar  double precision,
    minimo         double precision NOT NULL,
    maximo         double precision NOT NULL,
    en_cero        integer  NOT NULL,     -- cuartos con CMg exactamente 0 (vertimiento solar)
    PRIMARY KEY (barra_id, fecha, bloque)
);

-- ------------------------------------------------------------- para consultar

-- Silver: cada barra cada 15 minutos, con nombres en vez de numeros.
CREATE OR REPLACE VIEW cmg.cmg_15min AS
SELECT
    b.nombre                                   AS barra,
    c.fecha,
    c.hora,
    c.minuto,
    c.bloque,
    c.cmg_usd_mwh,
    (c.hora = 24)                              AS es_hora_extra,
    CASE WHEN c.hora < 24
         THEN c.fecha + make_interval(hours => c.hora, mins => c.minuto)
    END                                        AS fecha_hora,  -- NULL en la hora 24: es ambigua
    o.nombre                                   AS origen
FROM cmg.cuarto c
JOIN cmg.barra  b USING (barra_id)
JOIN cmg.origen o USING (origen_id);

-- Gold: el resumen mensual por barra, con las mismas columnas que el Excel del
-- programa. `dias_preliminar` > 0 avisa que el mes todavia puede cambiar.
CREATE OR REPLACE VIEW cmg.cmg_mensual AS
SELECT
    b.nombre                          AS barra,
    m.fecha,                          -- primer dia del mes: en Excel funcionan =MES() y =AÑO()
    extract(year FROM m.fecha)::int   AS anio,
    extract(month FROM m.fecha)::int  AS mes,
    m.cuartos,
    m.a                               AS "A",
    m.b                               AS "B",
    m.c                               AS "C",
    m.b                               AS "Solar",
    m.no_solar                        AS "NoSolar",
    m.total                           AS "Total",
    k.dias,
    k.dias_preliminar
FROM cmg.mes_barra m
JOIN cmg.barra b USING (barra_id)
LEFT JOIN cmg.carga_mes k
       ON k.anio = extract(year FROM m.fecha) AND k.mes = extract(month FROM m.fecha);

-- Gold: por barra, mes y bloque, con promedio, minimo, maximo y desvio.
CREATE OR REPLACE VIEW cmg.cmg_mensual_bloque AS
SELECT
    b.nombre AS barra,
    m.fecha,
    m.bloque,
    m.cuartos,
    m.promedio,
    m.desv_estandar,
    m.minimo,
    m.maximo,
    m.en_cero
FROM cmg.mes_bloque m
JOIN cmg.barra b USING (barra_id);

-- ------------------------------------------------------------------ permisos

GRANT SELECT ON ALL TABLES IN SCHEMA cmg TO cmg_lectura;
-- y lo que cmg_carga cree despues (las particiones de cada mes nuevo)
ALTER DEFAULT PRIVILEGES FOR ROLE cmg_carga IN SCHEMA cmg GRANT SELECT ON TABLES TO cmg_lectura;

RESET ROLE;
COMMIT;
