"""Envío de los datos del receptor a la nube (EC2 → RDS), opción B: inmediato y con respaldo.

SQLite local sigue siendo la base principal. Cada registro tiene mod_nube (instante de su última modificación,
puesto por triggers). Un hilo aparte envía a la EC2 todo lo que sea más nuevo que lo último confirmado:
  - de inmediato, cuando el receptor avisa que guardó algo (agrupa durante 0,3 s);
  - y en un barrido cada 30 s y al arrancar, que recupera lo que haya quedado pendiente.
Si no hay internet o el servidor falla, reintenta con espera creciente; nada se pierde porque los pendientes
siguen en SQLite. Los reenvíos no duplican: el servidor hace upsert por (estacion, clave).

Configuración: funcionalidad/nube.json (ver nube.ejemplo.json) o:  python nube.py --configurar

Uso por consola:
  python nube.py --estado          configuración y registros pendientes por tabla
  python nube.py --probar          comprueba conexión y clave con el servidor
  python nube.py --enviar-todo     envía ya todo lo pendiente (por ejemplo, el historial) y termina
  python nube.py --reiniciar       vuelve a marcar todo como pendiente (reenvío completo)
  python nube.py --generar-clave   crea una clave aleatoria segura para usar en el PC y en la EC2
"""
import argparse
import gzip
import json
import os
import secrets
import socket
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request

import rutas
from base_datos import TABLAS_NUBE, BaseDatos

ARCHIVO_CONFIG = os.path.join(rutas.CARPETA_FUNCIONALIDAD, "nube.json")
VERSION_API = 1
MAX_FILAS = 500            # filas por tabla en cada lote
ESPERA_AGRUPAR_S = 0.3     # tras un aviso, espera para juntar varios mensajes en un mismo envío
BARRIDO_S = 30.0           # barrido de respaldo
REINTENTOS_S = (5, 10, 20, 40, 80, 160, 300)
AGENTE = "ReceptorAIS-SDR/8 (envio a la nube)"


# ---------------------------------------------------------------- configuración
def cargar_config(ruta=ARCHIVO_CONFIG):
    """Lee nube.json (y variables AIS_NUBE_URL / AIS_NUBE_CLAVE). Devuelve None si no hay configuración."""
    cfg = {}
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            cfg = json.load(f)
    if os.environ.get("AIS_NUBE_URL"):
        cfg["url"] = os.environ["AIS_NUBE_URL"]
    if os.environ.get("AIS_NUBE_CLAVE"):
        cfg["clave"] = os.environ["AIS_NUBE_CLAVE"]
    if not cfg.get("url") or not cfg.get("clave"):
        return None
    if not cfg["url"].startswith(("https://", "http://")):
        raise ValueError("La url de la nube debe empezar por https:// (o http:// solo para pruebas)")
    cfg["url"] = cfg["url"].rstrip("/")
    cfg.setdefault("estacion", "principal")
    cfg.setdefault("activo", True)
    cfg.setdefault("verificar_tls", True)
    cfg.setdefault("timeout_s", 15)
    return cfg


def guardar_config(cfg, ruta=ARCHIVO_CONFIG):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(ruta, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- cliente HTTP
class ErrorNube(Exception):
    def __init__(self, texto, estado="sin_conexion", codigo=None):
        super().__init__(texto)
        self.estado = estado        # sin_conexion (reintentable) | rechazado (clave o datos)
        self.codigo = codigo


class ClienteNube:
    def __init__(self, cfg):
        self.url = cfg["url"]
        self.clave = cfg["clave"]
        self.timeout = float(cfg.get("timeout_s", 15))
        self.contexto = None if cfg.get("verificar_tls", True) else ssl._create_unverified_context()

    def _peticion(self, metodo, ruta, cuerpo=None):
        encabezados = {"X-API-Key": self.clave, "User-Agent": AGENTE, "Accept": "application/json"}
        datos = None
        if cuerpo is not None:
            datos = gzip.compress(json.dumps(cuerpo, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            encabezados.update({"Content-Type": "application/json", "Content-Encoding": "gzip"})
        req = urllib.request.Request(self.url + ruta, data=datos, method=metodo, headers=encabezados)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.contexto) as r:
                texto = r.read()
        except urllib.error.HTTPError as e:
            detalle = e.read()[:300].decode("utf-8", "replace")
            if e.code in (401, 403):
                raise ErrorNube(f"el servidor rechazó la clave (HTTP {e.code})", "rechazado", e.code)
            if 400 <= e.code < 500 and e.code not in (408, 429):
                raise ErrorNube(f"el servidor rechazó el lote (HTTP {e.code}): {detalle}", "rechazado", e.code)
            raise ErrorNube(f"error del servidor (HTTP {e.code})", "sin_conexion", e.code)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError) as e:
            motivo = getattr(e, "reason", e)
            raise ErrorNube(f"sin conexión con {self.url}: {motivo}")
        try:
            return json.loads(texto or b"{}")
        except ValueError:
            raise ErrorNube("respuesta del servidor no es JSON")

    def salud(self):
        return self._peticion("GET", "/api/v1/salud")

    def enviar_lote(self, lote):
        r = self._peticion("POST", "/api/v1/lote", lote)
        if not r.get("ok"):
            raise ErrorNube(f"el servidor no confirmó el lote: {r}", "sin_conexion")
        return r


# ---------------------------------------------------------------- lectura de pendientes en SQLite
def conectar(ruta_db):
    BaseDatos(ruta_db).cerrar()   # asegura la migración (columnas mod_nube y triggers) antes de leer
    con = sqlite3.connect(ruta_db, timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("CREATE TABLE IF NOT EXISTS nube_sync (tabla TEXT PRIMARY KEY, mod REAL NOT NULL DEFAULT 0, "
                "clave INTEGER NOT NULL DEFAULT 0)")
    for tabla in TABLAS_NUBE:
        con.execute("INSERT OR IGNORE INTO nube_sync (tabla) VALUES (?)", (tabla,))
    con.commit()
    return con


def marcas(con):
    return {f["tabla"]: (f["mod"], f["clave"]) for f in con.execute("SELECT tabla, mod, clave FROM nube_sync")}


def pendientes(con):
    m = marcas(con)
    total = {}
    for tabla, (clave, _) in TABLAS_NUBE.items():
        mod, k = m.get(tabla, (0, 0))
        total[tabla] = con.execute(
            f"SELECT COUNT(*) FROM {tabla} WHERE mod_nube > ? OR (mod_nube = ? AND {clave} > ?)", (mod, mod, k)
        ).fetchone()[0]
    return total


def armar_lote(con, estacion, max_filas=MAX_FILAS):
    """Lote con lo pendiente de cada tabla (en orden: sesiones antes que los mensajes que las usan)."""
    m = marcas(con)
    tablas, nuevas, lleno = {}, {}, False
    for tabla, (clave, _) in TABLAS_NUBE.items():
        mod, k = m.get(tabla, (0, 0))
        filas = con.execute(
            f"SELECT * FROM {tabla} WHERE mod_nube > ? OR (mod_nube = ? AND {clave} > ?) "
            f"ORDER BY mod_nube, {clave} LIMIT ?", (mod, mod, k, max_filas)).fetchall()
        if filas:
            tablas[tabla] = [dict(f) for f in filas]
            nuevas[tabla] = (filas[-1]["mod_nube"], filas[-1][clave])
            lleno = lleno or len(filas) == max_filas
    lote = {"version": VERSION_API, "estacion": estacion, "enviado": time.time(), "tablas": tablas}
    return lote, nuevas, lleno


def confirmar(con, nuevas):
    for tabla, (mod, k) in nuevas.items():
        con.execute("UPDATE nube_sync SET mod = ?, clave = ? WHERE tabla = ?", (mod, k, tabla))
    con.commit()


# ---------------------------------------------------------------- sincronizador en segundo plano
class Sincronizador(threading.Thread):
    def __init__(self, ruta_db, cfg, registro=print):
        super().__init__(daemon=True, name="nube")
        self.ruta_db = ruta_db
        self.cfg = cfg
        self.cliente = ClienteNube(cfg)
        self.registro = registro
        self._aviso = threading.Event()
        self._parar = threading.Event()
        self._bloqueo = threading.Lock()
        self._estado = {"activo": True, "estado": "iniciando", "url": cfg["url"], "estacion": cfg["estacion"],
                        "pendientes": None, "ultimo_ok": None, "error": None, "proximo_intento": None,
                        "enviados": 0, "lotes": 0, "ms_ultimo": None}
        self._fallos = 0
        self._no_antes_de = 0.0

    # --- API para el receptor
    def avisar(self):
        """Llamar después de guardar algo en SQLite (y confirmarlo): dispara un envío inmediato."""
        self._aviso.set()

    def estado(self):
        with self._bloqueo:
            return dict(self._estado)

    def detener(self, vaciar=True, limite_s=8.0):
        """Detiene el hilo; si vaciar, intenta un último envío de lo pendiente durante limite_s segundos."""
        self._ultimo_vaciado = vaciar
        self._limite_final = time.time() + limite_s
        self._parar.set()
        self._aviso.set()
        self.join(limite_s + 2)

    # --- bucle
    def _actualizar(self, **cambios):
        with self._bloqueo:
            self._estado.update(cambios)

    def run(self):
        try:
            con = conectar(self.ruta_db)
        except sqlite3.Error as e:
            self._actualizar(estado="error_local", error=f"no se pudo abrir la base de datos: {e}")
            return
        espera = 0.0
        while not self._parar.is_set():
            disparado = self._aviso.wait(timeout=espera)
            if self._parar.is_set():
                break
            if disparado:
                time.sleep(ESPERA_AGRUPAR_S)
                self._aviso.clear()
            ahora = time.time()
            if ahora < self._no_antes_de:
                espera = self._no_antes_de - ahora
                continue
            self._ciclo(con)
            espera = BARRIDO_S if self._fallos == 0 else max(0.5, self._no_antes_de - time.time())
        if getattr(self, "_ultimo_vaciado", False) and self._fallos == 0:
            self._ciclo(con, limite=self._limite_final)
        con.close()

    def _ciclo(self, con, limite=None):
        try:
            self._vaciar(con, limite)
            self._fallos = 0
            self._no_antes_de = 0.0
            pend = pendientes(con)
            self._actualizar(estado="al_dia" if not sum(pend.values()) else "pendiente", pendientes=pend,
                             error=None, proximo_intento=None)
        except ErrorNube as e:
            self._fallos += 1
            demora = REINTENTOS_S[min(self._fallos - 1, len(REINTENTOS_S) - 1)]
            if e.estado == "rechazado":
                demora = REINTENTOS_S[-1]
            self._no_antes_de = time.time() + demora
            try:
                pend = pendientes(con)
            except sqlite3.Error:
                pend = None
            if self._fallos == 1 or e.estado == "rechazado":
                self.registro(f"[nube] {e}. Se reintentará en {demora} s; los datos quedan guardados en local.")
            self._actualizar(estado=e.estado, error=str(e), pendientes=pend, proximo_intento=self._no_antes_de)
        except sqlite3.Error as e:
            self._no_antes_de = time.time() + 10
            self._actualizar(estado="error_local", error=f"base de datos local ocupada o con error: {e}")

    def _vaciar(self, con, limite=None):
        while True:
            lote, nuevas, lleno = armar_lote(con, self.cfg["estacion"])
            if not nuevas:
                return
            self._actualizar(estado="enviando")
            t0 = time.time()
            self.cliente.enviar_lote(lote)
            confirmar(con, nuevas)
            n = sum(len(v) for v in lote["tablas"].values())
            with self._bloqueo:
                self._estado["enviados"] += n
                self._estado["lotes"] += 1
                self._estado["ultimo_ok"] = time.time()
                self._estado["ms_ultimo"] = round((time.time() - t0) * 1000)
            if not lleno or (limite and time.time() > limite) or (self._parar.is_set() and not limite):
                return


def iniciar(ruta_db, registro=print):
    """Crea y arranca el sincronizador si hay configuración activa; si no, devuelve None."""
    try:
        cfg = cargar_config()
    except (ValueError, OSError) as e:
        registro(f"[nube] configuración no válida ({ARCHIVO_CONFIG}): {e}")
        return None
    if not cfg or not cfg.get("activo", True):
        return None
    s = Sincronizador(ruta_db, cfg, registro)
    s.start()
    registro(f"[nube] Envío activo a {cfg['url']} (estación '{cfg['estacion']}').")
    return s


# ---------------------------------------------------------------- consola
def _configurar(args):
    previa = {}
    if os.path.exists(ARCHIVO_CONFIG):
        with open(ARCHIVO_CONFIG, encoding="utf-8") as f:
            previa = json.load(f)
    pedir = lambda texto, actual: (input(f"{texto} [{actual}]: ").strip() or actual) if actual else input(f"{texto}: ").strip()
    url = args.url or pedir("URL del servidor en la nube (https://tu-nombre.duckdns.org)", previa.get("url", ""))
    clave = args.clave or pedir("Clave de API (la misma configurada en la EC2)", previa.get("clave", ""))
    estacion = args.estacion or pedir("Nombre de esta estación receptora", previa.get("estacion", "barranquilla-1"))
    cfg = dict(previa, url=url.rstrip("/"), clave=clave, estacion=estacion, activo=True)
    guardar_config(cfg)
    print(f"Guardado en {ARCHIVO_CONFIG}. El receptor empezará a enviar la próxima vez que lo abras.")


def main():
    ap = argparse.ArgumentParser(description="Envío de los datos del receptor AIS a la nube")
    ap.add_argument("--db", default=rutas.DB_DEFECTO)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estado", action="store_true")
    g.add_argument("--probar", action="store_true")
    g.add_argument("--enviar-todo", action="store_true")
    g.add_argument("--reiniciar", action="store_true")
    g.add_argument("--configurar", action="store_true")
    g.add_argument("--generar-clave", action="store_true")
    ap.add_argument("--url")
    ap.add_argument("--clave")
    ap.add_argument("--estacion")
    args = ap.parse_args()

    if args.generar_clave:
        print(secrets.token_urlsafe(32))
        return
    if args.configurar:
        _configurar(args)
        return
    cfg = cargar_config()
    if args.estado:
        con = conectar(args.db)
        if cfg:
            print(f"Servidor : {cfg['url']}\nEstación : {cfg['estacion']}\nClave    : …{cfg['clave'][-4:]}"
                  f"\nActivo   : {'sí' if cfg.get('activo', True) else 'no'}")
        else:
            print(f"Sin configurar: crea {ARCHIVO_CONFIG} o ejecuta python nube.py --configurar")
        pend = pendientes(con)
        print("Pendientes de enviar:", pend, f"(total {sum(pend.values())})")
        return
    if not cfg:
        raise SystemExit(f"Sin configurar: ejecuta python nube.py --configurar")
    cliente = ClienteNube(cfg)
    if args.probar:
        try:
            t0 = time.time()
            r = cliente.salud()
            print(f"Conexión correcta con {cfg['url']} ({(time.time() - t0) * 1000:.0f} ms): {r}")
        except ErrorNube as e:
            raise SystemExit(f"Falló: {e}")
        return
    con = conectar(args.db)
    if args.reiniciar:
        if input("Se volverá a enviar TODO el historial (el servidor no duplica). ¿Continuar? [s/N]: ").strip().lower() != "s":
            return
        con.execute("UPDATE nube_sync SET mod = 0, clave = 0")
        con.commit()
        print("Listo: todo quedó pendiente. Envíalo con --enviar-todo o abriendo el receptor.")
        return
    if args.enviar_todo:
        total = sum(pendientes(con).values())
        print(f"Pendientes: {total} registros. Enviando…")
        enviados = 0
        while True:
            lote, nuevas, lleno = armar_lote(con, cfg["estacion"])
            if not nuevas:
                break
            try:
                cliente.enviar_lote(lote)
            except ErrorNube as e:
                raise SystemExit(f"Se detuvo: {e}. Lo enviado quedó confirmado; vuelve a ejecutarlo para continuar.")
            confirmar(con, nuevas)
            enviados += sum(len(v) for v in lote["tablas"].values())
            print(f"  {enviados} / {total}", end="\r", flush=True)
        print(f"\nListo: {enviados} registros enviados. Pendientes: {sum(pendientes(con).values())}")


if __name__ == "__main__":
    main()
