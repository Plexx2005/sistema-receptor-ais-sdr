# Contrato de la API de ingesta (PC → EC2)

Este es el formato que el receptor (`funcionalidad/nube.py`) ya envía. El servidor de la EC2 debe
implementarlo tal cual. `pruebas/servidor_nube_prueba.py` es una implementación de referencia (con SQLite)
para probar en local.

## Autenticación y transporte
- HTTPS obligatorio en producción (Caddy + Let's Encrypt con el dominio DuckDNS).
- Encabezado `X-API-Key: <clave>` en todas las peticiones. Compararla en tiempo constante
  (`hmac.compare_digest`). Clave incorrecta → **401**.
- La clave se genera con `python nube.py --generar-clave` y se guarda en el PC (`nube.json`) y en la EC2
  (variable de entorno, nunca en el código).

## `GET /api/v1/salud`
Comprueba conexión y clave. Respuesta 200: `{"ok": true, "version": 1, ...}`.

## `POST /api/v1/lote`
- Cuerpo JSON comprimido con gzip (`Content-Type: application/json`, `Content-Encoding: gzip`).
  Tamaño típico < 200 KB; aceptar al menos 20 MB.

```json
{
  "version": 1,
  "estacion": "barranquilla-1",
  "enviado": 1793403268.17,
  "tablas": {
    "sesiones":            [ { "id": 107, "inicio": ..., "fin": ..., "fuente": "sdr", ... , "mod_nube": ... } ],
    "mensajes":            [ { "id": 560, "sesion_id": 107, "ts": ..., "tipo": 18, "mmsi": 730104104,
                               "nmea": "!AIVDM,...", "campos": "{\"lat\": 11.02, ...}", "dist_km": 3.2, ... } ],
    "barcos":              [ { "mmsi": 730104104, "nombre": "...", "lat": ..., "lon": ..., "pais": "Colombia", ... } ],
    "metricas":            [ { "id": 1283, "sesion_id": 107, "ts": ..., "rafagas": ..., ... } ],
    "posiciones_receptor": [ { "id": 3, "sesion_id": 110, "ts": ..., "lat": ..., "lon": ..., ... } ]
  }
}
```

- Cada fila trae **todas las columnas** de su tabla en el SQLite local (mismos nombres), más `mod_nube`.
  `mensajes.campos` es un texto JSON (en PostgreSQL conviene guardarlo como `JSONB`).
- Pueden faltar tablas (solo vienen las que tienen cambios). Pueden llegar columnas nuevas en el futuro:
  ignorar las desconocidas.
- **Semántica: upsert idempotente.** Clave única `(estacion, id)`; en `barcos`, `(estacion, mmsi)`.
  Un mismo registro puede llegar varias veces (reintentos o actualizaciones): siempre gana la última versión.
- Procesar en este orden y **en una sola transacción**: sesiones → barcos → mensajes → metricas →
  posiciones_receptor. Responder 200 **solo después del commit**: el PC marca el lote como enviado al
  recibir el 200.
- Respuesta 200: `{"ok": true, "recibidos": {"mensajes": 12, "barcos": 3}}`.

## Códigos de error y comportamiento del PC
| Respuesta | El PC… |
|---|---|
| 200 `{"ok": true}` | marca como enviado y sigue |
| 401 / 403 | muestra "Rechazado" (clave) y reintenta cada 5 min |
| 400 / 413 / 422 | muestra "Rechazado" con el detalle y reintenta cada 5 min |
| 408, 429, 5xx, sin red, timeout | muestra "Sin conexión" y reintenta a los 5, 10, 20, 40… s (máx. 5 min) |

## Lo que la EC2 no recibe
- Los borrados hechos con "Eliminar registros" en el PC no se propagan (la nube conserva el historial).
- Para reenviar todo: `python nube.py --reiniciar` y luego `--enviar-todo` (no duplica nada).

## Esquema sugerido en PostgreSQL (RDS)
Mismas columnas que en SQLite, con `estacion TEXT NOT NULL` añadida y estas claves:

```sql
CREATE TABLE sesiones (estacion TEXT NOT NULL, id BIGINT NOT NULL, inicio DOUBLE PRECISION, fin DOUBLE PRECISION,
  fuente TEXT, detalle TEXT, ganancia REAL, ppm INTEGER, umbral REAL, rx_lat DOUBLE PRECISION,
  rx_lon DOUBLE PRECISION, mod_nube DOUBLE PRECISION, PRIMARY KEY (estacion, id));
CREATE TABLE mensajes (estacion TEXT NOT NULL, id BIGINT NOT NULL, sesion_id BIGINT, ts DOUBLE PRECISION,
  t_rel DOUBLE PRECISION, canal TEXT, snr_db REAL, desvio_hz REAL, tipo INTEGER, mmsi BIGINT, nmea TEXT,
  campos JSONB, rx_lat DOUBLE PRECISION, rx_lon DOUBLE PRECISION, dist_km DOUBLE PRECISION, repeticion INTEGER,
  mod_nube DOUBLE PRECISION, PRIMARY KEY (estacion, id));
CREATE INDEX ON mensajes (mmsi, ts);
CREATE INDEX ON mensajes (ts);
-- barcos: PRIMARY KEY (estacion, mmsi); metricas y posiciones_receptor: PRIMARY KEY (estacion, id)
```
