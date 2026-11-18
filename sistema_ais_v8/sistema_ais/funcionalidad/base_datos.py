import csv
import json
import math
import sqlite3
import threading
import time

from mmsi_info import info as info_mmsi, tipo_nave_texto

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sesiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio REAL NOT NULL,
    fin REAL,
    fuente TEXT NOT NULL,
    detalle TEXT,
    ganancia REAL,
    ppm INTEGER,
    umbral REAL
);
CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sesion_id INTEGER NOT NULL REFERENCES sesiones(id),
    ts REAL NOT NULL,
    t_rel REAL NOT NULL,
    canal TEXT NOT NULL,
    snr_db REAL,
    desvio_hz REAL,
    tipo INTEGER NOT NULL,
    mmsi INTEGER NOT NULL,
    nmea TEXT NOT NULL,
    campos TEXT,
    UNIQUE (sesion_id, canal, t_rel, nmea)
);
CREATE INDEX IF NOT EXISTS idx_mensajes_mmsi ON mensajes(mmsi);
CREATE INDEX IF NOT EXISTS idx_mensajes_ts ON mensajes(ts);
CREATE TABLE IF NOT EXISTS barcos (
    mmsi INTEGER PRIMARY KEY,
    nombre TEXT,
    indicativo TEXT,
    destino TEXT,
    tipo_nave INTEGER,
    lat REAL,
    lon REAL,
    sog_kn REAL,
    cog REAL,
    rumbo INTEGER,
    estado_nav INTEGER,
    canal TEXT,
    primera_vez REAL NOT NULL,
    ultima_vez REAL NOT NULL,
    ultima_posicion REAL,
    n_mensajes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS metricas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sesion_id INTEGER NOT NULL REFERENCES sesiones(id),
    ts REAL NOT NULL,
    mensajes INTEGER,
    rafagas INTEGER,
    sin_crc INTEGER,
    invalidos INTEGER,
    piso_a_db REAL,
    piso_b_db REAL,
    snr_db REAL
);
CREATE INDEX IF NOT EXISTS idx_metricas_sesion ON metricas(sesion_id, ts);
CREATE TABLE IF NOT EXISTS posiciones_receptor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sesion_id INTEGER REFERENCES sesiones(id),
    ts REAL NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    precision_m REAL,
    fuente TEXT
);
CREATE INDEX IF NOT EXISTS idx_posrx_ts ON posiciones_receptor(ts);
CREATE TABLE IF NOT EXISTS nube_sync (
    tabla TEXT PRIMARY KEY,
    mod REAL NOT NULL DEFAULT 0,
    clave INTEGER NOT NULL DEFAULT 0
);
"""

# Envío a la nube (v8): cada tabla lleva mod_nube = instante de su última modificación, puesto por triggers de
# SQLite (así nada se escapa, lo modifique quien lo modifique). El sincronizador envía lo que tenga
# (mod_nube, clave) mayor que lo último confirmado por el servidor (tabla nube_sync).
TABLAS_NUBE = {"sesiones": ("id", "COALESCE(fin, inicio)"), "mensajes": ("id", "ts"), "barcos": ("mmsi", "ultima_vez"),
               "metricas": ("id", "ts"), "posiciones_receptor": ("id", "ts")}
AHORA_SQL = "((julianday('now') - 2440587.5) * 86400.0)"

# Columnas añadidas en la v5. Se agregan solas a bases de datos anteriores.
COLUMNAS_NUEVAS = {
    "sesiones": [("rx_lat", "REAL"), ("rx_lon", "REAL")],
    "mensajes": [("rx_lat", "REAL"), ("rx_lon", "REAL"), ("dist_km", "REAL"), ("repeticion", "INTEGER")],
    "barcos": [("pais", "TEXT"), ("pais_iso", "TEXT"), ("categoria", "TEXT"), ("mmsi_valido", "INTEGER"),
               ("tipo_nave_desc", "TEXT"), ("rx_lat", "REAL"), ("rx_lon", "REAL"), ("dist_km", "REAL"),
               ("directo", "INTEGER")],
}


def distancia_km(lat1, lon1, lat2, lon2):
    """Distancia por el círculo máximo (fórmula del haversine), en km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, a)))


def _pos_valida(lat, lon):
    return lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180

TABLAS = ("sesiones", "mensajes", "barcos", "metricas", "posiciones_receptor")
TIPOS_BARCO = (1, 2, 3, 5, 18, 19, 24)
TIPOS_POSICION_BARCO = (1, 2, 3, 18, 19)
TIPOS_CON_POSICION = (1, 2, 3, 4, 11, 18, 19, 21)
CLAVES_BASE = ("t", "canal", "snr_db", "desvio_hz", "bits", "nmea", "tipo", "mmsi")


def _repeticion_de_nmea(nmea):
    """Indicador de repetición (bits 6-7 del mensaje) leído de la sentencia NMEA guardada."""
    try:
        payload = nmea.split("\n")[0].split(",")[5]
        v = ord(payload[1]) - 48
        if v > 40:
            v -= 8
        return (v >> 4) & 0b11
    except (IndexError, TypeError):
        return None


def _valido(valor, maximo):
    if valor is None or valor >= maximo:
        return None
    return valor


def _texto(valor):
    return valor if valor else None


class BaseDatos:
    def __init__(self, ruta="ais.db", lote_s=1.0, intervalo_metricas_s=10.0):
        self.ruta = ruta
        self.lote_s = lote_s
        self.intervalo_metricas_s = intervalo_metricas_s
        self._bloqueo = threading.RLock()
        self.con = sqlite3.connect(ruta, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(ESQUEMA)
        self._migrar()
        self.con.commit()
        self.sesion_id = None
        self._ultimo_commit = time.time()
        self._ultima_metrica = 0.0

    def _migrar(self):
        for tabla, columnas in COLUMNAS_NUEVAS.items():
            existentes = {r[1] for r in self.con.execute(f"PRAGMA table_info({tabla})")}
            for nombre, tipo in columnas:
                if nombre not in existentes:
                    self.con.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")
        for fila in self.con.execute("SELECT mmsi, tipo_nave FROM barcos WHERE pais IS NULL").fetchall():
            self._guardar_info_barco(fila["mmsi"], fila["tipo_nave"])
        pendientes = self.con.execute("SELECT id, nmea FROM mensajes WHERE repeticion IS NULL").fetchall()
        for fila in pendientes:
            self.con.execute("UPDATE mensajes SET repeticion = ? WHERE id = ?",
                             (_repeticion_de_nmea(fila["nmea"]), fila["id"]))
        if pendientes:
            self.con.execute("UPDATE barcos SET directo = (SELECT MAX(CASE WHEN m.repeticion = 0 THEN 1 ELSE 0 END) "
                             "FROM mensajes m WHERE m.mmsi = barcos.mmsi) WHERE directo IS NULL")
        self._migrar_nube()

    def _migrar_nube(self):
        for tabla, (clave, inicial) in TABLAS_NUBE.items():
            existentes = {r[1] for r in self.con.execute(f"PRAGMA table_info({tabla})")}
            if "mod_nube" not in existentes:
                self.con.execute(f"ALTER TABLE {tabla} ADD COLUMN mod_nube REAL")
            self.con.execute(f"UPDATE {tabla} SET mod_nube = COALESCE({inicial}, 0) WHERE mod_nube IS NULL")
            self.con.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabla}_nube ON {tabla}(mod_nube, {clave})")
            self.con.execute(
                f"CREATE TRIGGER IF NOT EXISTS nube_ins_{tabla} AFTER INSERT ON {tabla} BEGIN "
                f"UPDATE {tabla} SET mod_nube = {AHORA_SQL} WHERE {clave} = NEW.{clave}; END")
            self.con.execute(
                f"CREATE TRIGGER IF NOT EXISTS nube_upd_{tabla} AFTER UPDATE ON {tabla} "
                f"WHEN NEW.mod_nube IS OLD.mod_nube BEGIN "
                f"UPDATE {tabla} SET mod_nube = {AHORA_SQL} WHERE {clave} = NEW.{clave}; END")

    def _guardar_info_barco(self, mmsi, tipo_nave):
        i = info_mmsi(mmsi, tipo_nave)
        self.con.execute("UPDATE barcos SET pais = ?, pais_iso = ?, categoria = ?, mmsi_valido = ?, tipo_nave_desc = ? "
                         "WHERE mmsi = ?", (i["pais"], i["pais_iso"], i["categoria"], int(i["mmsi_valido"]),
                                            i["tipo_nave_desc"], mmsi))

    def _confirmar(self, forzar=False):
        ahora = time.time()
        if forzar or ahora - self._ultimo_commit >= self.lote_s:
            self.con.commit()
            self._ultimo_commit = ahora

    def abrir_sesion(self, fuente, detalle="", ganancia=None, ppm=None, umbral=None, inicio=None, rx=None):
        with self._bloqueo:
            inicio = inicio if inicio is not None else time.time()
            cur = self.con.execute(
                "INSERT INTO sesiones (inicio, fuente, detalle, ganancia, ppm, umbral) VALUES (?, ?, ?, ?, ?, ?)",
                (inicio, fuente, detalle, ganancia, ppm, umbral))
            self.sesion_id = cur.lastrowid
            if rx:
                self.registrar_posicion(rx["lat"], rx["lon"], rx.get("precision_m"),
                                        "inicio de sesión (última conocida)", ts=inicio)
            self._ultima_metrica = 0.0
            self._confirmar(forzar=True)
            return self.sesion_id

    def cerrar_sesion(self, fin=None):
        with self._bloqueo:
            if self.sesion_id is None:
                return
            self.con.execute("UPDATE sesiones SET fin = ? WHERE id = ?",
                             (fin if fin is not None else time.time(), self.sesion_id))
            self._confirmar(forzar=True)
            self.sesion_id = None

    def registrar_posicion(self, lat, lon, precision_m=None, fuente=None, ts=None, sesion_id="actual"):
        """Guarda una posición del receptor. La primera de cada sesión queda también en la tabla sesiones."""
        if not _pos_valida(lat, lon):
            raise ValueError("Posición fuera de rango")
        ts = time.time() if ts is None else ts
        sid = self.sesion_id if sesion_id == "actual" else sesion_id
        with self._bloqueo:
            self.con.execute("INSERT INTO posiciones_receptor (sesion_id, ts, lat, lon, precision_m, fuente) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (sid, ts, lat, lon, precision_m, fuente))
            if sid is not None:
                self.con.execute("UPDATE sesiones SET rx_lat = ?, rx_lon = ? WHERE id = ? AND rx_lat IS NULL",
                                 (lat, lon, sid))
            self._confirmar(forzar=True)

    def piso_tipico(self, ganancia, minimo=5):
        """Piso de ruido habitual (mediana) con esta ganancia, en sesiones reales que sí decodificaron mensajes."""
        filas = self._filas(
            "SELECT m.piso_a_db AS a, m.piso_b_db AS b FROM metricas m JOIN sesiones s ON s.id = m.sesion_id "
            "WHERE ABS(s.ganancia - ?) < 0.05 AND s.fuente != 'archivo' AND m.mensajes > 0 "
            "AND m.piso_a_db IS NOT NULL AND m.piso_b_db IS NOT NULL", (float(ganancia),))
        if len(filas) < minimo:
            return None
        valores = sorted((f["a"] + f["b"]) / 2 for f in filas)
        return valores[len(valores) // 2]

    def ultima_posicion_receptor(self):
        filas = self._filas("SELECT lat, lon, precision_m, fuente, ts FROM posiciones_receptor ORDER BY ts DESC, id DESC LIMIT 1")
        return filas[0] if filas else None

    def pista_receptor(self, desde=None, hasta=None, sesion=None):
        condiciones, params = [], []
        if sesion is not None:
            condiciones.append("sesion_id = ?")
            params.append(sesion)
        if desde is not None:
            condiciones.append("ts >= ?")
            params.append(desde)
        if hasta is not None:
            condiciones.append("ts <= ?")
            params.append(hasta)
        donde = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
        return self._filas("SELECT sesion_id, ts, lat, lon, precision_m, fuente FROM posiciones_receptor"
                           + donde + " ORDER BY ts", params)

    def guardar_evento(self, ev, ts=None, rx=None):
        """Guarda un mensaje. rx = posición del receptor {'lat', 'lon'} en el momento de la captura."""
        if self.sesion_id is None:
            raise RuntimeError("No hay una sesión abierta")
        ts = time.time() if ts is None else ts
        campos = {k: v for k, v in ev.items() if k not in CLAVES_BASE}
        rx_lat = rx_lon = dist = None
        if rx and _pos_valida(rx.get("lat"), rx.get("lon")):
            rx_lat, rx_lon = rx["lat"], rx["lon"]
            if ev["tipo"] in TIPOS_CON_POSICION and _pos_valida(ev.get("lat"), ev.get("lon")):
                dist = round(distancia_km(rx_lat, rx_lon, ev["lat"], ev["lon"]), 3)
        with self._bloqueo:
            cur = self.con.execute(
                "INSERT OR IGNORE INTO mensajes "
                "(sesion_id, ts, t_rel, canal, snr_db, desvio_hz, tipo, mmsi, nmea, campos, rx_lat, rx_lon, dist_km, repeticion) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.sesion_id, ts, round(float(ev["t"]), 3), ev["canal"],
                 float(ev["snr_db"]), float(ev["desvio_hz"]), int(ev["tipo"]), int(ev["mmsi"]),
                 "\n".join(ev["nmea"]), json.dumps(campos, ensure_ascii=False), rx_lat, rx_lon, dist,
                 ev.get("repeticion")))
            nuevo = cur.rowcount == 1
            if nuevo and ev["tipo"] in TIPOS_BARCO:
                self._actualizar_barco(ev, ts, rx_lat, rx_lon, dist)
            self._confirmar()
            return nuevo

    def _actualizar_barco(self, ev, ts, rx_lat=None, rx_lon=None, dist=None):
        lat = lon = sog = cog = rumbo = estado = None
        pos_ts = None
        if ev["tipo"] in TIPOS_POSICION_BARCO:
            lat = ev.get("lat")
            lon = ev.get("lon")
            if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
                lat = lon = None
            else:
                pos_ts = ts
            sog = _valido(ev.get("sog_kn"), 102.3)
            cog = _valido(ev.get("cog"), 360.0)
            rumbo = _valido(ev.get("rumbo"), 511)
            estado = ev.get("estado_nav")
        nombre = indicativo = destino = tipo_nave = None
        if ev["tipo"] in (5, 19, 24):
            nombre = _texto(ev.get("nombre"))
            indicativo = _texto(ev.get("indicativo"))
            destino = _texto(ev.get("destino"))
            tipo_nave = ev.get("tipo_nave") or None
        if lat is None:
            rx_lat = rx_lon = dist = None
        directo = None if ev.get("repeticion") is None else int(ev.get("repeticion") == 0)
        self.con.execute(
            "INSERT INTO barcos (mmsi, nombre, indicativo, destino, tipo_nave, lat, lon, sog_kn, cog, rumbo, "
            "estado_nav, canal, primera_vez, ultima_vez, ultima_posicion, n_mensajes, rx_lat, rx_lon, dist_km, directo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?) "
            "ON CONFLICT(mmsi) DO UPDATE SET "
            "nombre = COALESCE(excluded.nombre, nombre), "
            "indicativo = COALESCE(excluded.indicativo, indicativo), "
            "destino = COALESCE(excluded.destino, destino), "
            "tipo_nave = COALESCE(excluded.tipo_nave, tipo_nave), "
            "lat = COALESCE(excluded.lat, lat), "
            "lon = COALESCE(excluded.lon, lon), "
            "sog_kn = CASE WHEN excluded.lat IS NOT NULL THEN excluded.sog_kn ELSE sog_kn END, "
            "cog = CASE WHEN excluded.lat IS NOT NULL THEN excluded.cog ELSE cog END, "
            "rumbo = CASE WHEN excluded.lat IS NOT NULL THEN excluded.rumbo ELSE rumbo END, "
            "estado_nav = CASE WHEN excluded.lat IS NOT NULL THEN excluded.estado_nav ELSE estado_nav END, "
            "rx_lat = CASE WHEN excluded.lat IS NOT NULL THEN excluded.rx_lat ELSE rx_lat END, "
            "rx_lon = CASE WHEN excluded.lat IS NOT NULL THEN excluded.rx_lon ELSE rx_lon END, "
            "dist_km = CASE WHEN excluded.lat IS NOT NULL THEN excluded.dist_km ELSE dist_km END, "
            "directo = MAX(COALESCE(directo, 0), COALESCE(excluded.directo, 0)), "
            "canal = excluded.canal, "
            "ultima_vez = MAX(ultima_vez, excluded.ultima_vez), "
            "ultima_posicion = COALESCE(excluded.ultima_posicion, ultima_posicion), "
            "n_mensajes = n_mensajes + 1",
            (ev["mmsi"], nombre, indicativo, destino, tipo_nave, lat, lon, sog, cog, rumbo, estado,
             ev["canal"], ts, ts, pos_ts, rx_lat, rx_lon, dist, directo))
        fila = self.con.execute("SELECT tipo_nave, pais, tipo_nave_desc FROM barcos WHERE mmsi = ?", (ev["mmsi"],)).fetchone()
        if fila["pais"] is None or (fila["tipo_nave"] and fila["tipo_nave_desc"] != tipo_nave_texto(fila["tipo_nave"])):
            self._guardar_info_barco(ev["mmsi"], fila["tipo_nave"])

    def guardar_metricas(self, m, ts=None, forzar=False):
        if self.sesion_id is None:
            return False
        ts = time.time() if ts is None else ts
        with self._bloqueo:
            if not forzar and ts - self._ultima_metrica < self.intervalo_metricas_s:
                return False
            self._ultima_metrica = ts
            piso = m.get("piso_db", {})
            piso_a = piso.get("A")
            piso_b = piso.get("B")
            self.con.execute(
                "INSERT INTO metricas (sesion_id, ts, mensajes, rafagas, sin_crc, invalidos, piso_a_db, piso_b_db, snr_db) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.sesion_id, ts, m["tramas_ok"], m["rafagas"], m["rafagas_sin_crc"],
                 m["mensajes_invalidos"],
                 None if piso_a is None else float(piso_a),
                 None if piso_b is None else float(piso_b),
                 None if m["snr_db"] is None else float(m["snr_db"])))
            self._confirmar(forzar=True)
            return True

    def sincronizar(self):
        with self._bloqueo:
            self._confirmar(forzar=True)

    def _filas(self, sql, params=()):
        with self._bloqueo:
            return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def sesiones(self):
        return self._filas(
            "SELECT s.*, (SELECT COUNT(*) FROM mensajes m WHERE m.sesion_id = s.id) AS n_mensajes "
            "FROM sesiones s ORDER BY s.id")

    def barcos(self, desde=None):
        if desde is None:
            return self._filas("SELECT * FROM barcos ORDER BY ultima_vez DESC")
        return self._filas("SELECT * FROM barcos WHERE ultima_vez >= ? ORDER BY ultima_vez DESC", (desde,))

    def estelas(self, desde, max_puntos=500, max_filas=20000):
        filas = self._filas(
            "SELECT mmsi, ts, campos FROM mensajes WHERE tipo IN (1, 2, 3, 18, 19) AND ts >= ? "
            "ORDER BY ts DESC LIMIT ?", (desde, max_filas))
        estelas = {}
        for f in reversed(filas):
            try:
                campos = json.loads(f["campos"]) if f["campos"] else {}
            except ValueError:
                continue
            lat = campos.get("lat")
            lon = campos.get("lon")
            if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
                continue
            estelas.setdefault(f["mmsi"], []).append([f["ts"], lat, lon])
        return {mmsi: puntos[-max_puntos:] for mmsi, puntos in estelas.items()}

    def historico(self, desde=None, hasta=None, sesion=None, max_puntos=2000):
        """Estado de los barcos reconstruido solo con los mensajes de un rango o una sesión."""
        condiciones, params = [f"tipo IN ({','.join(str(t) for t in TIPOS_BARCO)})"], []
        if sesion is not None:
            condiciones.append("sesion_id = ?")
            params.append(sesion)
        if desde is not None:
            condiciones.append("ts >= ?")
            params.append(desde)
        if hasta is not None:
            condiciones.append("ts <= ?")
            params.append(hasta)
        filas = self._filas("SELECT ts, tipo, mmsi, canal, campos, rx_lat, rx_lon, dist_km, repeticion "
                            "FROM mensajes WHERE " + " AND ".join(condiciones) + " ORDER BY ts, id", params)
        fijos = {b["mmsi"]: b for b in self.barcos()}
        barcos, estelas = {}, {}
        for f in filas:
            m = f["mmsi"]
            c = json.loads(f["campos"]) if f["campos"] else {}
            base = fijos.get(m, {"mmsi": m})
            b = barcos.get(m)
            if b is None:
                b = {k: base.get(k) for k in ("mmsi", "nombre", "indicativo", "destino", "tipo_nave", "pais",
                                              "pais_iso", "categoria", "mmsi_valido", "tipo_nave_desc")}
                b.update(lat=None, lon=None, sog_kn=None, cog=None, rumbo=None, estado_nav=None, rx_lat=None,
                         rx_lon=None, dist_km=None, ultima_posicion=None, primera_vez=f["ts"], n_mensajes=0, directo=0)
                barcos[m] = b
            b["n_mensajes"] += 1
            b["ultima_vez"] = f["ts"]
            b["canal"] = f["canal"]
            if f["repeticion"] == 0:
                b["directo"] = 1
            if f["tipo"] in TIPOS_POSICION_BARCO and _pos_valida(c.get("lat"), c.get("lon")):
                b.update(lat=c["lat"], lon=c["lon"], sog_kn=_valido(c.get("sog_kn"), 102.3),
                         cog=_valido(c.get("cog"), 360.0), rumbo=_valido(c.get("rumbo"), 511),
                         estado_nav=c.get("estado_nav"), rx_lat=f["rx_lat"], rx_lon=f["rx_lon"],
                         dist_km=f["dist_km"], ultima_posicion=f["ts"])
                estelas.setdefault(m, []).append([f["ts"], c["lat"], c["lon"]])
        receptor = self.pista_receptor(desde, hasta, sesion)
        return {"barcos": list(barcos.values()),
                "estelas": {str(k): v[-max_puntos:] for k, v in estelas.items()},
                "receptor": receptor, "mensajes": len(filas)}

    def historial_barco(self, mmsi, desde=None, hasta=None, tipos=None, limite=2000):
        """Todos los mensajes de un MMSI (los más recientes hasta `limite`), con resumen y trayectoria."""
        condiciones, params = ["mmsi = ?"], [int(mmsi)]
        if desde is not None:
            condiciones.append("ts >= ?")
            params.append(desde)
        if hasta is not None:
            condiciones.append("ts <= ?")
            params.append(hasta)
        if tipos:
            condiciones.append(f"tipo IN ({','.join('?' * len(tipos))})")
            params.extend(int(t) for t in tipos)
        donde = " WHERE " + " AND ".join(condiciones)
        agregado = self._filas(
            "SELECT COUNT(*) AS n, MIN(ts) AS primero, MAX(ts) AS ultimo, COUNT(DISTINCT sesion_id) AS sesiones, "
            "SUM(CASE WHEN repeticion = 0 THEN 1 ELSE 0 END) AS directos, "
            "SUM(CASE WHEN repeticion > 0 THEN 1 ELSE 0 END) AS retransmitidos, "
            "MIN(dist_km) AS dist_min, MAX(dist_km) AS dist_max, AVG(snr_db) AS snr_medio "
            "FROM mensajes" + donde, params)[0]
        por_tipo = {f["tipo"]: f["n"] for f in self._filas(
            "SELECT tipo, COUNT(*) AS n FROM mensajes" + donde + " GROUP BY tipo ORDER BY tipo", params)}
        por_sesion = self._filas(
            "SELECT sesion_id, COUNT(*) AS n, MIN(ts) AS primero, MAX(ts) AS ultimo FROM mensajes" + donde +
            " GROUP BY sesion_id ORDER BY sesion_id", params)
        filas = self._filas(
            "SELECT id, sesion_id, ts, canal, snr_db, desvio_hz, tipo, nmea, campos, rx_lat, rx_lon, dist_km, repeticion "
            "FROM mensajes" + donde + " ORDER BY ts DESC, id DESC LIMIT ?", params + [int(limite)])
        mensajes, trayectoria = [], []
        for f in reversed(filas):
            c = json.loads(f["campos"]) if f["campos"] else {}
            f["campos"] = c
            f["nmea"] = f["nmea"].split("\n")
            mensajes.append(f)
            if f["tipo"] in TIPOS_CON_POSICION and _pos_valida(c.get("lat"), c.get("lon")):
                trayectoria.append([f["ts"], c["lat"], c["lon"]])
        recorrido = sum(distancia_km(a[1], a[2], b[1], b[2]) for a, b in zip(trayectoria, trayectoria[1:]))
        velocidades = [m["campos"]["sog_kn"] for m in mensajes
                       if m["campos"].get("sog_kn") is not None and m["campos"]["sog_kn"] < 102.3]
        barco = self.barco(mmsi)
        if barco is None:
            barco = dict(info_mmsi(mmsi), mmsi=int(mmsi))
        resumen = dict(agregado, por_tipo=por_tipo, mostrados=len(mensajes), recorrido_km=recorrido,
                       vel_max=max(velocidades) if velocidades else None,
                       vel_media=sum(velocidades) / len(velocidades) if velocidades else None)
        return {"barco": barco, "resumen": resumen, "sesiones": por_sesion,
                "mensajes": mensajes, "trayectoria": trayectoria}

    def barco(self, mmsi):
        filas = self._filas("SELECT * FROM barcos WHERE mmsi = ?", (mmsi,))
        return filas[0] if filas else None

    def ultimos_mensajes(self, n=50, sesion=None):
        if sesion is None:
            return self._filas("SELECT * FROM mensajes ORDER BY id DESC LIMIT ?", (n,))
        return self._filas("SELECT * FROM mensajes WHERE sesion_id = ? ORDER BY id DESC LIMIT ?", (sesion, n))

    def metricas(self, sesion=None):
        if sesion is None:
            return self._filas("SELECT * FROM metricas ORDER BY id")
        return self._filas("SELECT * FROM metricas WHERE sesion_id = ? ORDER BY id", (sesion,))

    def contar(self, tabla):
        if tabla not in TABLAS:
            raise ValueError("Tabla desconocida: " + tabla)
        with self._bloqueo:
            return self.con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]

    def exportar_csv(self, ruta, tabla="mensajes"):
        if tabla not in TABLAS:
            raise ValueError("Tabla desconocida: " + tabla)
        with self._bloqueo:
            self.con.commit()
            cur = self.con.execute(f"SELECT * FROM {tabla} ORDER BY 1")
            columnas = [d[0] for d in cur.description]
            filas = cur.fetchall()
        with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(columnas)
            for fila in filas:
                w.writerow(list(fila))
        return len(filas)

    def _seleccion(self, barco, desde, hasta):
        texto = (barco or "").strip()
        if not texto and desde is None and hasta is None:
            raise ValueError("Indica un barco o un rango de tiempo")
        condiciones = []
        params = []
        coincidencias = []
        if texto:
            if texto.isdigit():
                mmsis = {int(texto)}
                coincidencias = self._filas("SELECT mmsi, nombre FROM barcos WHERE mmsi = ?", (int(texto),))
            else:
                coincidencias = self._filas(
                    "SELECT mmsi, nombre FROM barcos WHERE nombre LIKE ? ORDER BY nombre", (f"%{texto}%",))
                mmsis = {c["mmsi"] for c in coincidencias}
            if not mmsis:
                return None, None, [], set()
            marcas = ",".join("?" * len(mmsis))
            condiciones.append(f"mmsi IN ({marcas})")
            params.extend(sorted(mmsis))
        if desde is not None:
            condiciones.append("ts >= ?")
            params.append(float(desde))
        if hasta is not None:
            condiciones.append("ts <= ?")
            params.append(float(hasta))
        return " AND ".join(condiciones), params, coincidencias, {c["mmsi"] for c in coincidencias}

    def _afectados(self, donde, params, coincidencias, barco, desde, hasta):
        filas = self._filas(f"SELECT DISTINCT mmsi FROM mensajes WHERE {donde}", params)
        afectados = {f["mmsi"] for f in filas}
        if (barco or "").strip() and desde is None and hasta is None:
            afectados |= {c["mmsi"] for c in coincidencias}
        return afectados

    def _nombres(self, afectados, limite=6):
        nombres = []
        for mmsi in sorted(afectados)[:limite]:
            b = self.barco(mmsi)
            nombre = b["nombre"] if b and b["nombre"] else None
            nombres.append(f"{nombre} ({mmsi})" if nombre else str(mmsi))
        if len(afectados) > limite:
            nombres.append(f"y {len(afectados) - limite} más")
        return nombres

    def contar_borrado(self, barco=None, desde=None, hasta=None):
        with self._bloqueo:
            donde, params, coincidencias, _ = self._seleccion(barco, desde, hasta)
            if donde is None:
                return {"mensajes": 0, "barcos": 0, "nombres": []}
            n = self.con.execute(f"SELECT COUNT(*) FROM mensajes WHERE {donde}", params).fetchone()[0]
            afectados = self._afectados(donde, params, coincidencias, barco, desde, hasta)
            return {"mensajes": n, "barcos": len(afectados), "nombres": self._nombres(afectados)}

    def _reconstruir_barco(self, mmsi):
        filas = self._filas(
            "SELECT ts, tipo, canal, campos, rx_lat, rx_lon, dist_km, repeticion FROM mensajes "
            "WHERE mmsi = ? AND tipo IN (1, 2, 3, 5, 18, 19, 24) ORDER BY ts, id", (mmsi,))
        self.con.execute("DELETE FROM barcos WHERE mmsi = ?", (mmsi,))
        for f in filas:
            ev = {"tipo": f["tipo"], "mmsi": mmsi, "canal": f["canal"]}
            ev.update(json.loads(f["campos"]) if f["campos"] else {})
            if f["repeticion"] is not None:
                ev["repeticion"] = f["repeticion"]
            self._actualizar_barco(ev, f["ts"], f["rx_lat"], f["rx_lon"], f["dist_km"])

    def borrar(self, barco=None, desde=None, hasta=None):
        with self._bloqueo:
            donde, params, coincidencias, _ = self._seleccion(barco, desde, hasta)
            if donde is None:
                return {"mensajes": 0, "barcos": 0, "nombres": []}
            afectados = self._afectados(donde, params, coincidencias, barco, desde, hasta)
            nombres = self._nombres(afectados)
            self.con.commit()
            try:
                cur = self.con.execute(f"DELETE FROM mensajes WHERE {donde}", params)
                borrados = cur.rowcount
                for mmsi in afectados:
                    self._reconstruir_barco(mmsi)
                self.con.commit()
            except Exception:
                self.con.rollback()
                raise
            self._ultimo_commit = time.time()
            return {"mensajes": borrados, "barcos": len(afectados), "nombres": nombres}

    def cerrar(self):
        with self._bloqueo:
            if self.sesion_id is not None:
                self.cerrar_sesion()
            self.con.commit()
            self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()
