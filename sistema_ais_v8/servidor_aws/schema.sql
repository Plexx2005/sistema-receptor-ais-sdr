-- Esquema de la réplica en RDS (PostgreSQL 16). Ejecutar UNA vez con el usuario maestro.
-- Mismas columnas que el SQLite del receptor, más "estacion". Las horas (inicio, fin, ts, ...) son
-- segundos Unix en UTC con decimales. Sin claves foráneas a propósito: los lotes son idempotentes y
-- pueden llegar en cualquier orden tras un corte.

CREATE TABLE IF NOT EXISTS sesiones (
  estacion TEXT NOT NULL, id BIGINT NOT NULL,
  inicio DOUBLE PRECISION, fin DOUBLE PRECISION, fuente TEXT, detalle TEXT,
  ganancia REAL, ppm INTEGER, umbral REAL, rx_lat DOUBLE PRECISION, rx_lon DOUBLE PRECISION,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, id));

CREATE TABLE IF NOT EXISTS barcos (
  estacion TEXT NOT NULL, mmsi BIGINT NOT NULL,
  nombre TEXT, indicativo TEXT, destino TEXT, tipo_nave INTEGER,
  lat DOUBLE PRECISION, lon DOUBLE PRECISION, sog_kn REAL, cog REAL, rumbo INTEGER, estado_nav INTEGER,
  canal TEXT, primera_vez DOUBLE PRECISION, ultima_vez DOUBLE PRECISION, ultima_posicion DOUBLE PRECISION,
  n_mensajes INTEGER, pais TEXT, pais_iso TEXT, categoria TEXT, mmsi_valido INTEGER, tipo_nave_desc TEXT,
  rx_lat DOUBLE PRECISION, rx_lon DOUBLE PRECISION, dist_km DOUBLE PRECISION, directo INTEGER,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, mmsi));

CREATE TABLE IF NOT EXISTS mensajes (
  estacion TEXT NOT NULL, id BIGINT NOT NULL,
  sesion_id BIGINT, ts DOUBLE PRECISION, t_rel DOUBLE PRECISION, canal TEXT, snr_db REAL, desvio_hz REAL,
  tipo INTEGER, mmsi BIGINT, nmea TEXT, campos JSONB,
  rx_lat DOUBLE PRECISION, rx_lon DOUBLE PRECISION, dist_km DOUBLE PRECISION, repeticion INTEGER,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, id));

CREATE TABLE IF NOT EXISTS metricas (
  estacion TEXT NOT NULL, id BIGINT NOT NULL,
  sesion_id BIGINT, ts DOUBLE PRECISION, mensajes INTEGER, rafagas INTEGER, sin_crc INTEGER,
  invalidos INTEGER, piso_a_db REAL, piso_b_db REAL, snr_db REAL,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, id));

CREATE TABLE IF NOT EXISTS posiciones_receptor (
  estacion TEXT NOT NULL, id BIGINT NOT NULL,
  sesion_id BIGINT, ts DOUBLE PRECISION, lat DOUBLE PRECISION, lon DOUBLE PRECISION,
  precision_m REAL, fuente TEXT,
  mod_nube DOUBLE PRECISION, recibido TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (estacion, id));

CREATE INDEX IF NOT EXISTS mensajes_mmsi_ts ON mensajes (mmsi, ts);
CREATE INDEX IF NOT EXISTS mensajes_ts      ON mensajes (ts);
CREATE INDEX IF NOT EXISTS barcos_ultima    ON barcos (ultima_vez);
CREATE INDEX IF NOT EXISTS posrx_ts         ON posiciones_receptor (estacion, ts);
CREATE INDEX IF NOT EXISTS metricas_ts      ON metricas (ts);
