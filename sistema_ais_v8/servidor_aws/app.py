"""Servidor AIS en la EC2: recibe los lotes del receptor (NUBE_API.md), los guarda en RDS y sirve la web pública.

Variables de entorno (archivo /etc/ais/ais.env):
  AIS_CLAVE      clave compartida con el PC (X-API-Key)
  DATABASE_URL   postgresql://ais_app:CONTRASEÑA@<endpoint-rds>:5432/ais?sslmode=require
"""
import hmac
import json
import os
import time
import zlib
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

CLAVE = os.environ["AIS_CLAVE"]
MAX_CUERPO = 20 * 1024 * 1024          # comprimido
MAX_DESCOMPRIMIDO = 200 * 1024 * 1024
WEB = Path(__file__).with_name("web")

# Orden de inserción, clave única y columnas aceptadas (las desconocidas se ignoran).
TABLAS = {
    "sesiones": ("id", "inicio fin fuente detalle ganancia ppm umbral rx_lat rx_lon mod_nube"),
    "barcos": ("mmsi", "nombre indicativo destino tipo_nave lat lon sog_kn cog rumbo estado_nav canal primera_vez "
                       "ultima_vez ultima_posicion n_mensajes pais pais_iso categoria mmsi_valido tipo_nave_desc "
                       "rx_lat rx_lon dist_km directo mod_nube"),
    "mensajes": ("id", "sesion_id ts t_rel canal snr_db desvio_hz tipo mmsi nmea campos rx_lat rx_lon dist_km "
                       "repeticion mod_nube"),
    "metricas": ("id", "sesion_id ts mensajes rafagas sin_crc invalidos piso_a_db piso_b_db snr_db mod_nube"),
    "posiciones_receptor": ("id", "sesion_id ts lat lon precision_m fuente mod_nube"),
}
TABLAS = {t: (k, c.split()) for t, (k, c) in TABLAS.items()}


def _sql_upsert(tabla):
    clave, cols = TABLAS[tabla]
    todas = ["estacion", clave] + cols
    marcas = ", ".join(["%s"] * len(todas))
    act = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols) + ", recibido = now()"
    # Siempre gana la versión más reciente (mod_nube); un reenvío viejo no pisa uno nuevo.
    return (f"INSERT INTO {tabla} ({', '.join(todas)}) VALUES ({marcas}) "
            f"ON CONFLICT (estacion, {clave}) DO UPDATE SET {act} "
            f"WHERE {tabla}.mod_nube IS NULL OR EXCLUDED.mod_nube >= {tabla}.mod_nube")


SQL = {t: _sql_upsert(t) for t in TABLAS}
# check: descarta conexiones muertas (p. ej. tras un reinicio de mantenimiento de RDS).
pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=4, open=True, timeout=10,
                      check=ConnectionPool.check_connection, kwargs={"row_factory": dict_row})
app = FastAPI(title="AIS", docs_url=None, redoc_url=None)


class LoteInvalido(Exception):
    pass


def _json_campos(v):
    if v is None or isinstance(v, (dict, list)):
        return Jsonb(v) if v is not None else None
    try:
        return Jsonb(json.loads(v))
    except (TypeError, ValueError):
        return None


def _filas(tabla, estacion, filas):
    clave, cols = TABLAS[tabla]
    salida = []
    for f in filas:
        if not isinstance(f, dict) or f.get(clave) is None:
            raise LoteInvalido(f"{tabla}: fila sin '{clave}'")
        valores = [f.get(c) for c in cols]
        if tabla == "mensajes":
            i = cols.index("campos")
            valores[i] = _json_campos(valores[i])
        salida.append([estacion, int(f[clave])] + valores)
    return salida


def guardar_lote(estacion, tablas):
    preparadas = {t: _filas(t, estacion, tablas.get(t) or []) for t in TABLAS}
    recibidos = {}
    with pool.connection() as con:          # una transacción: se guarda todo o nada
        with con.cursor() as cur:
            for tabla, filas in preparadas.items():
                if filas:
                    cur.executemany(SQL[tabla], filas)
                    recibidos[tabla] = len(filas)
    return recibidos                         # al salir del bloque ya hizo COMMIT


def _autorizado(request):
    return hmac.compare_digest(request.headers.get("x-api-key", "").encode(), CLAVE.encode())


def _error(codigo, texto):
    return JSONResponse({"ok": False, "error": texto}, status_code=codigo)


# ------------------------------------------------------------------ API de ingesta (con clave)
@app.get("/api/v1/salud")
def salud(request: Request):
    if not _autorizado(request):
        return _error(401, "clave no válida")
    with pool.connection() as con:
        con.execute("SELECT 1")
    return {"ok": True, "version": 1, "servidor": "ec2", "hora": time.time()}


@app.post("/api/v1/lote")
async def lote(request: Request):
    if not _autorizado(request):
        return _error(401, "clave no válida")
    if int(request.headers.get("content-length") or 0) > MAX_CUERPO:
        return _error(413, "lote demasiado grande")
    cuerpo = await request.body()
    if len(cuerpo) > MAX_CUERPO:
        return _error(413, "lote demasiado grande")
    try:
        if request.headers.get("content-encoding", "").lower() == "gzip":
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            cuerpo = d.decompress(cuerpo, MAX_DESCOMPRIMIDO)
            if d.unconsumed_tail:
                return _error(413, "lote descomprimido demasiado grande")
        datos = json.loads(cuerpo)
        estacion = str(datos.get("estacion") or "").strip()[:64]
        if datos.get("version") != 1 or not estacion or not isinstance(datos.get("tablas"), dict):
            raise LoteInvalido("faltan version/estacion/tablas")
        recibidos = await run_in_threadpool(guardar_lote, estacion, datos["tablas"])
    except (zlib.error, ValueError, LoteInvalido) as e:
        return _error(400, f"lote mal formado: {e}")
    return {"ok": True, "recibidos": recibidos}


# ------------------------------------------------------------------ API pública (sin clave, solo lectura)
def _consulta(sql, params=()):
    with pool.connection() as con:
        return con.execute(sql, params).fetchall()


@app.get("/api/v1/publico/receptor")
def receptor():
    """Ubicación EXACTA del receptor de cada estación (decisión del dueño del sistema)."""
    filas = _consulta("""
        SELECT DISTINCT ON (estacion) estacion, lat, lon, ts, fuente FROM (
            SELECT estacion, lat, lon, ts, fuente FROM posiciones_receptor WHERE lat IS NOT NULL
            UNION ALL
            SELECT estacion, rx_lat, rx_lon, inicio, 'sesion' FROM sesiones WHERE rx_lat IS NOT NULL
        ) p ORDER BY estacion, ts DESC""")
    return {"receptores": filas}


@app.get("/api/v1/publico/barcos")
def barcos(horas: float = Query(24, gt=0, le=24 * 365)):
    filas = _consulta("""
        SELECT estacion, mmsi, nombre, indicativo, destino, tipo_nave_desc, categoria, pais, pais_iso,
               lat, lon, sog_kn, cog, rumbo, estado_nav, primera_vez, ultima_vez, ultima_posicion,
               n_mensajes, dist_km, directo
        FROM barcos WHERE ultima_vez >= %s ORDER BY ultima_vez DESC LIMIT 2000""",
                      (time.time() - horas * 3600,))
    return {"ahora": time.time(), "barcos": filas}


@app.get("/api/v1/publico/barco/{mmsi}")
def barco(mmsi: int, horas: float = Query(72, gt=0, le=24 * 365)):
    pista = _consulta("""
        SELECT ts, (campos->>'lat')::float AS lat, (campos->>'lon')::float AS lon,
               (campos->>'sog_kn')::float AS sog_kn
        FROM mensajes
        WHERE mmsi = %s AND ts >= %s AND jsonb_typeof(campos->'lat') = 'number'
          AND abs((campos->>'lat')::float) <= 90 AND abs((campos->>'lon')::float) <= 180
        ORDER BY ts LIMIT 5000""", (mmsi, time.time() - horas * 3600))
    return {"mmsi": mmsi, "pista": pista}


@app.get("/api/v1/publico/resumen")
def resumen():
    f = _consulta("""
        SELECT (SELECT count(*) FROM barcos) AS barcos,
               (SELECT count(*) FROM mensajes WHERE ts >= %s) AS mensajes_24h,
               (SELECT max(ts) FROM mensajes) AS ultimo_mensaje,
               (SELECT count(DISTINCT estacion) FROM sesiones) AS estaciones""", (time.time() - 86400,))[0]
    return f


@app.get("/")
def inicio():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})
