import argparse
import csv
import io
import json
import os
import queue
import threading
import time
from collections import deque

import tornado.httpclient
import tornado.ioloop
import tornado.web
import tornado.websocket

import config
import espectro
import nube
import rutas
from base_datos import BaseDatos, distancia_km
from decodificar_archivo import resumen
from mmsi_info import info as info_mmsi
from escuchar import CAPACIDAD_COLA, lector_archivo, lector_sdr
from receptor_sensible import crear_receptor
from sdr import cu8_a_complejo

CARPETA = os.path.dirname(os.path.abspath(__file__))
PAGINA = os.path.join(CARPETA, "gui", "index.html")
PAGINA_MAPA = os.path.join(CARPETA, "gui", "mapa.html")
GUI = os.path.join(CARPETA, "gui")
VENDOR = os.path.join(CARPETA, "gui", "vendor")
CACHE_MOSAICOS = os.path.join(CARPETA, "mapa_cache")
URL_MOSAICOS = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
AGENTE = "ReceptorAIS-SDR/5.0 (proyecto academico de comunicaciones; Python tornado)"
MOVIMIENTO_MIN_M = 15.0
INTERVALO_POS_S = 60.0
SIN_DATOS_S = 6.0          # segundos sin muestras del SDR antes de avisar
CALENTAMIENTO_S = 20.0     # segundos de datos antes de juzgar el piso de ruido
PISO_MARGEN_DB = 12.0      # cuánto por debajo de lo habitual se considera anormal
PISO_MINIMO_DB = -50.0     # sin historial: piso por debajo de esto con ganancia alta es sospechoso

LIMITES = {"ganancia": (0.0, 50.0), "ppm": (-200, 200), "umbral": (0.5, 20.0)}


class Sistema:
    def __init__(self, args):
        self.args = args
        self.bd = BaseDatos(args.db)
        self.mutex = threading.RLock()
        self.parar = threading.Event()
        self.clientes = set()
        self.bucle = None
        self.fuente = "archivo" if args.archivo else "sdr"
        self.detalle = os.path.basename(args.archivo) if args.archivo else "RTL-SDR"
        self.cfg = {"ganancia": args.ganancia, "ppm": args.ppm, "umbral": args.umbral}
        self.rx = crear_receptor(args.modo, args.umbral)
        self.cola = queue.Queue(maxsize=CAPACIDAD_COLA)
        self.parar_lector = threading.Event()
        self.hilo_lector = None
        self.lector_estado = {"perdidos": 0, "error": None}
        self.sesion = None
        self.inicio = time.time()
        self.guardados = 0
        self.recientes = deque()
        self.snr_suma = 0.0
        self.snr_n = 0
        self.hilo_proceso = threading.Thread(target=self.procesar, daemon=True)
        self.posicion = self.bd.ultima_posicion_receptor()
        self._ultima_pos_guardada = None
        self.ultimo_bloque = None
        self.primer_bloque = None
        self.piso_ref = None
        self.nube = nube.iniciar(args.db)

    def arrancar_todo(self, bucle):
        self.bucle = bucle
        self.hilo_proceso.start()
        with self.mutex:
            self._arrancar()

    def _arrancar(self):
        self.rx = crear_receptor(self.args.modo, self.cfg["umbral"])
        self.lector_estado = {"perdidos": 0, "error": None}
        self.cola = queue.Queue(maxsize=CAPACIDAD_COLA)
        self.parar_lector = threading.Event()
        self.guardados = 0
        self.recientes.clear()
        self.snr_suma = 0.0
        self.snr_n = 0
        self.inicio = time.time()
        self.ultimo_bloque = None
        self.primer_bloque = None
        self.piso_ref = self.bd.piso_tipico(self.cfg["ganancia"])
        self.sesion = self.bd.abrir_sesion(self.fuente, self.detalle, self.cfg["ganancia"],
                                           self.cfg["ppm"], self.cfg["umbral"], rx=self.posicion)
        self._ultima_pos_guardada = dict(self.posicion, ts=time.time()) if self.posicion else None
        self._avisar_nube()
        argumentos = argparse.Namespace(archivo=self.args.archivo, repetir=self.args.repetir,
                                        ganancia=self.cfg["ganancia"], ppm=self.cfg["ppm"])
        destino = lector_archivo if self.args.archivo else lector_sdr
        self.hilo_lector = threading.Thread(
            target=destino, args=(argumentos, self.cola, self.parar_lector, self.lector_estado), daemon=True)
        self.hilo_lector.start()

    def _parar_lector(self):
        hilo = self.hilo_lector
        if hilo is None:
            return
        self.parar_lector.set()
        limite = time.time() + 4
        while hilo.is_alive() and time.time() < limite:
            try:
                while True:
                    self.cola.get_nowait()
            except queue.Empty:
                pass
            hilo.join(0.1)
        self.hilo_lector = None

    def _cerrar_sesion(self):
        if self.sesion is not None:
            self.bd.guardar_metricas(self.rx.metricas, forzar=True)
            self.bd.cerrar_sesion()
            self.sesion = None
            self._avisar_nube()

    def _avisar_nube(self):
        if self.nube is not None:
            self.nube.avisar()

    def corriendo(self):
        hilo = self.hilo_lector
        return hilo is not None and hilo.is_alive()

    def detener(self):
        with self.mutex:
            self._parar_lector()
            self._cerrar_sesion()

    def finalizar(self):
        self.parar.set()
        self.detener()
        self.hilo_proceso.join(timeout=3)
        if self.nube is not None:
            self.nube.detener(vaciar=True, limite_s=8)
        self.bd.cerrar()

    def procesar(self):
        while not self.parar.is_set():
            cola = self.cola
            try:
                crudo = cola.get(timeout=0.3)
            except queue.Empty:
                continue
            if crudo is None:
                continue
            mensajes = []
            iq = cu8_a_complejo(crudo)
            with self.mutex:
                if cola is not self.cola or self.sesion is None:
                    continue
                self.ultimo_bloque = time.time()
                if self.primer_bloque is None:
                    self.primer_bloque = self.ultimo_bloque
                for ev in self.rx.procesar(iq):
                    ahora = time.time()
                    if self.bd.guardar_evento(ev, ts=ahora, rx=self.posicion):
                        self.guardados += 1
                    self.recientes.append(ahora)
                    self.snr_suma += float(ev["snr_db"])
                    self.snr_n += 1
                    mensajes.append(self.mensaje_evento(ev, ahora))
                self.bd.guardar_metricas(self.rx.metricas)
                if mensajes:
                    self.bd.sincronizar()
            if mensajes:
                self._avisar_nube()
            for m in mensajes:
                self.difundir(m)
            if any(getattr(c, "quiere_espectro", True) for c in self.clientes):
                esp = espectro.calcular(iq)
                if esp is not None:
                    self.difundir(esp, solo_espectro=True)

    def comando(self, d):
        accion = d.get("accion")
        if accion == "detener":
            if self.sesion is None:
                return {"texto": "Ya está detenido", "vista": False}
            self.detener()
            return {"texto": "Receptor detenido. La sesión quedó cerrada en la base de datos.", "vista": True}
        if accion == "iniciar":
            with self.mutex:
                if self.corriendo():
                    return {"texto": "El receptor ya está en marcha", "vista": False}
                self._parar_lector()
                self._cerrar_sesion()
                self._arrancar()
                return {"texto": f"Receptor iniciado (sesión {self.sesion})", "vista": True}
        if accion == "aplicar":
            return self.aplicar(d)
        if accion in ("borrar_vista", "borrar"):
            return self.borrar(d, accion == "borrar")
        if accion == "posicion":
            return self.fijar_posicion(d)
        return {"texto": "Comando desconocido", "vista": False}

    def fijar_posicion(self, d):
        try:
            lat, lon = float(d["lat"]), float(d["lon"])
            precision = None if d.get("precision") in (None, "") else float(d["precision"])
        except (KeyError, TypeError, ValueError):
            return {"texto": "Posición no válida", "vista": False}
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return {"texto": "Posición fuera de rango", "vista": False}
        fuente = str(d.get("fuente") or "manual")[:40]
        ahora = time.time()
        with self.mutex:
            previa = self._ultima_pos_guardada
            movido = previa is None or distancia_km(previa["lat"], previa["lon"], lat, lon) * 1000 >= MOVIMIENTO_MIN_M
            guardar = fuente != "gps" or movido or ahora - previa["ts"] >= INTERVALO_POS_S
            self.posicion = {"lat": lat, "lon": lon, "precision_m": precision, "fuente": fuente, "ts": ahora}
            if guardar:
                self.bd.registrar_posicion(lat, lon, precision, fuente, ts=ahora,
                                           sesion_id=self.sesion if self.sesion is not None else None)
                self._ultima_pos_guardada = dict(self.posicion)
        if guardar:
            self._avisar_nube()
        self.difundir({"k": "receptor", "posicion": self.posicion, "guardada": guardar})
        texto = f"Posición del receptor: {lat:.5f}, {lon:.5f} ({fuente})" if guardar else ""
        return {"texto": texto, "vista": False}

    def borrar(self, d, ejecutar):
        barco = str(d.get("barco") or "").strip()[:60]
        try:
            desde = None if d.get("desde") in (None, "") else float(d["desde"])
            hasta = None if d.get("hasta") in (None, "") else float(d["hasta"])
        except (TypeError, ValueError):
            return {"texto": "Fechas no válidas", "vista": False, "previa": {"modo": "error", "texto": "Fechas no válidas"}}
        if desde is not None and hasta is not None and desde > hasta:
            texto = "La fecha inicial es posterior a la final"
            return {"texto": texto, "vista": False, "previa": {"modo": "error", "texto": texto}}
        try:
            if ejecutar:
                r = self.bd.borrar(barco, desde, hasta)
            else:
                r = self.bd.contar_borrado(barco, desde, hasta)
        except ValueError as e:
            return {"texto": str(e), "vista": False, "previa": {"modo": "error", "texto": str(e)}}
        previa = dict(r, modo="borrado" if ejecutar else "previa")
        if ejecutar:
            texto = f"Se eliminaron {r['mensajes']} mensajes de {r['barcos']} barco(s) de la base de datos."
            previa["texto"] = texto
            return {"texto": texto, "vista": True, "previa": previa}
        return {"texto": "", "vista": False, "previa": previa}

    def aplicar(self, d):
        try:
            nuevo = {"ganancia": float(d["ganancia"]), "ppm": int(round(float(d["ppm"]))),
                     "umbral": float(d["umbral"])}
        except (KeyError, TypeError, ValueError):
            return {"texto": "Valores no válidos", "vista": False}
        for clave, (minimo, maximo) in LIMITES.items():
            if not minimo <= nuevo[clave] <= maximo:
                return {"texto": f"{clave} fuera de rango ({minimo} a {maximo})", "vista": False}
        with self.mutex:
            self.cfg = nuevo
            if self.sesion is None:
                return {"texto": "Ajustes guardados. Se usarán al pulsar Iniciar.", "vista": False}
            self._parar_lector()
            self._cerrar_sesion()
            self._arrancar()
            return {"texto": f"Ajustes aplicados: ganancia {nuevo['ganancia']} dB, ppm {nuevo['ppm']}, "
                             f"umbral {nuevo['umbral']} dB (sesión nueva {self.sesion})", "vista": True}

    def mensaje_evento(self, ev, ahora):
        return {"k": "evento", "msg": self.resumir_evento(ev, ahora), "barco": self.bd.barco(ev["mmsi"])}

    def resumir_evento(self, ev, ahora):
        i = info_mmsi(ev["mmsi"])
        dist = None
        rx = self.posicion
        if rx and ev.get("lat") is not None and abs(ev["lat"]) <= 90 and abs(ev.get("lon", 999)) <= 180:
            dist = distancia_km(rx["lat"], rx["lon"], ev["lat"], ev["lon"])
        return {"ts": ahora, "canal": ev["canal"], "snr_db": float(ev["snr_db"]),
                "desvio_hz": float(ev["desvio_hz"]), "tipo": int(ev["tipo"]), "mmsi": int(ev["mmsi"]),
                "resumen": resumen(ev), "nmea": list(ev["nmea"]), "pais": i["pais"], "categoria": i["categoria"],
                "repeticion": ev.get("repeticion"), "dist_km": dist}

    def avisos(self):
        """Problemas del receptor que el usuario debe ver aunque no haya un error explícito."""
        if self.sesion is None or self.fuente == "archivo" or not self.corriendo():
            return []
        ahora = time.time()
        desde = self.ultimo_bloque or self.inicio
        if ahora - desde > SIN_DATOS_S:
            return [{"tipo": "sin_datos", "texto":
                     f"El SDR no entrega muestras desde hace {ahora - desde:.0f} s. Revisa que el dongle siga conectado "
                     "a WSL (usbipd): desconéctalo, vuelve a conectarlo y pulsa Iniciar."}]
        pisos = [v for v in self.rx.metricas["piso_db"].values() if v is not None]
        if not pisos or self.primer_bloque is None or ahora - self.primer_bloque < CALENTAMIENTO_S:
            return []
        piso = sum(pisos) / len(pisos)
        if self.piso_ref is not None and piso < self.piso_ref - PISO_MARGEN_DB:
            return [{"tipo": "piso_bajo", "texto":
                     f"El piso de ruido ({piso:.1f} dB) está {self.piso_ref - piso:.0f} dB por debajo de lo normal con esta "
                     f"ganancia ({self.piso_ref:.1f} dB). La antena podría estar desconectada, recogida o tapada por un techo "
                     "o paredes: llévala a un sitio abierto."}]
        if self.piso_ref is None and self.cfg["ganancia"] >= 30 and piso < PISO_MINIMO_DB:
            return [{"tipo": "piso_bajo", "texto":
                     f"El piso de ruido ({piso:.1f} dB) es muy bajo para una ganancia de {self.cfg['ganancia']} dB: "
                     "la antena podría estar desconectada o tapada."}]
        return []

    def estado(self):
        rx = self.rx
        m = rx.metricas
        ahora = time.time()
        while self.recientes and ahora - self.recientes[0] > 60:
            try:
                self.recientes.popleft()
            except IndexError:
                break
        segundos = ahora - self.inicio
        mpm = len(self.recientes) * 60 / max(min(60.0, segundos), 5.0) if self.sesion is not None else 0.0
        valida = m["rafagas"] - m["rafagas_sin_crc"]
        tasa = None if m["rafagas"] == 0 else 100.0 * valida / m["rafagas"]
        piso = {k: (None if v is None else round(float(v), 1)) for k, v in dict(m["piso_db"]).items()}
        error = self.lector_estado["error"]
        return {"fuente": self.fuente, "detalle": self.detalle,
                "corriendo": self.corriendo() or (self.sesion is not None and not self.cola.empty()),
                "activo": self.sesion is not None,
                "error": None if error is None else str(error),
                "segundos": segundos if self.sesion is not None else 0.0,
                "mensajes": m["tramas_ok"], "rafagas": m["rafagas"], "sin_crc": m["rafagas_sin_crc"],
                "invalidos": m["mensajes_invalidos"], "piso": piso,
                "perdidos": self.lector_estado["perdidos"], "cola": self.cola.qsize(),
                "guardados": self.guardados, "sesion": self.sesion, "tasa": tasa, "mpm": mpm,
                "snr_medio": None if self.snr_n == 0 else self.snr_suma / self.snr_n,
                "snr_ultimo": None if m["snr_db"] is None else float(m["snr_db"]),
                "por_canal": dict(m["por_canal"]),
                "por_tipo": {str(k): v for k, v in dict(m["por_tipo"]).items()},
                "config": dict(self.cfg), "avisos": self.avisos(),
                "mosaicos_directo": Mosaicos.motivo,
                "nube": self.nube.estado() if self.nube is not None else {"activo": False}}

    def instantanea(self):
        mensajes = []
        sesion = self.sesion if self.sesion is not None else -1
        for fila in reversed(self.bd.ultimos_mensajes(100, sesion)):
            campos = json.loads(fila["campos"]) if fila["campos"] else {}
            i = info_mmsi(fila["mmsi"])
            mensajes.append({"ts": fila["ts"], "canal": fila["canal"], "snr_db": fila["snr_db"],
                             "desvio_hz": fila["desvio_hz"], "tipo": fila["tipo"], "mmsi": fila["mmsi"],
                             "resumen": resumen({"tipo": fila["tipo"], **campos}) if campos else "",
                             "nmea": fila["nmea"].split("\n"), "pais": i["pais"], "categoria": i["categoria"],
                             "repeticion": fila["repeticion"], "dist_km": fila["dist_km"]})
        desde = time.time() - 24 * 3600
        estelas = {str(m): p for m, p in self.bd.estelas(desde).items()}
        return {"k": "inicio", "barcos": self.bd.barcos(), "mensajes": mensajes, "estelas": estelas,
                "estado": self.estado(), "receptor": self.posicion,
                "pista_receptor": self.bd.pista_receptor(desde=desde)}

    def difundir(self, mensaje, solo_espectro=False):
        texto = json.dumps(mensaje, ensure_ascii=False)
        self.bucle.add_callback(self._enviar, texto, solo_espectro)

    def _enviar(self, texto, solo_espectro=False):
        for c in list(self.clientes):
            if solo_espectro and not getattr(c, "quiere_espectro", True):
                continue
            try:
                c.write_message(texto)
            except tornado.websocket.WebSocketClosedError:
                self.clientes.discard(c)

    def tic_estado(self):
        self._enviar(json.dumps({"k": "estado", "estado": self.estado()}, ensure_ascii=False))


class Pagina(tornado.web.RequestHandler):
    def initialize(self, ruta=PAGINA):
        self.ruta = ruta

    def get(self):
        with open(self.ruta, encoding="utf-8") as f:
            self.set_header("Content-Type", "text/html; charset=utf-8")
            self.set_header("Cache-Control", "no-cache")
            self.write(f.read())


class Mosaicos(tornado.web.RequestHandler):
    """Proxy de mosaicos de OpenStreetMap con caché en disco: lo ya visto se muestra sin internet.

    Si este equipo no logra descargarlos (por ejemplo, WSL sin salida a internet), registra el motivo y
    redirige al navegador para que los pida directamente a OpenStreetMap (plan B, sin caché).
    """
    sin_red_hasta = 0.0
    motivo = None
    _ultimo_registro = 0.0

    @classmethod
    def _fallo(cls, motivo):
        cls.sin_red_hasta = time.time() + 120
        cls.motivo = motivo
        if time.time() - cls._ultimo_registro >= 300:
            cls._ultimo_registro = time.time()
            print(f"[mapa] No se pudieron descargar mosaicos de OpenStreetMap desde este equipo: {motivo}. "
                  "El navegador los pedirá directamente (no se guardarán en la caché).", flush=True)

    def _directo(self, z, x, y):
        self.set_header("Cache-Control", "no-store")
        self.redirect(URL_MOSAICOS.format(z=z, x=x, y=y), permanent=False)

    async def get(self, z, x, y):
        z, x, y = int(z), int(x), int(y)
        if not (0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise tornado.web.HTTPError(404)
        ruta = os.path.join(CACHE_MOSAICOS, str(z), str(x), f"{y}.png")
        if os.path.exists(ruta):
            self._servir(ruta)
            return
        if time.time() < Mosaicos.sin_red_hasta:
            self._directo(z, x, y)
            return
        try:
            r = await tornado.httpclient.AsyncHTTPClient().fetch(
                URL_MOSAICOS.format(z=z, x=x, y=y), headers={"User-Agent": AGENTE},
                connect_timeout=4, request_timeout=10)
        except tornado.httpclient.HTTPClientError as e:
            if e.code == 404:
                raise tornado.web.HTTPError(404)
            Mosaicos._fallo(f"OpenStreetMap respondió HTTP {e.code} ({e.message})")
            self._directo(z, x, y)
            return
        except Exception as e:
            Mosaicos._fallo(f"{type(e).__name__}: {e}")
            self._directo(z, x, y)
            return
        if r.body[:8] != b"\x89PNG\r\n\x1a\n":
            Mosaicos._fallo("la respuesta no es una imagen PNG")
            self._directo(z, x, y)
            return
        Mosaicos.motivo = None
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        temporal = ruta + ".tmp"
        with open(temporal, "wb") as f:
            f.write(r.body)
        os.replace(temporal, ruta)
        self._servir(ruta)

    def _servir(self, ruta):
        self.set_header("Content-Type", "image/png")
        self.set_header("Cache-Control", "max-age=604800")
        with open(ruta, "rb") as f:
            self.write(f.read())


class Api(tornado.web.RequestHandler):
    def initialize(self, sistema):
        self.sistema = sistema

    def _numero(self, nombre):
        v = self.get_argument(nombre, "")
        if v == "":
            return None
        try:
            return float(v)
        except ValueError:
            raise tornado.web.HTTPError(400, f"{nombre} no válido")

    def _historial_barco(self, bd):
        mmsi = self._numero("mmsi")
        if mmsi is None:
            raise tornado.web.HTTPError(400, "falta mmsi")
        tipos = [int(t) for t in self.get_argument("tipos", "").split(",") if t.strip().isdigit()]
        limite = self._numero("limite")
        limite = 2000 if limite is None else max(1, min(int(limite), 50000))
        datos = bd.historial_barco(int(mmsi), desde=self._numero("desde"), hasta=self._numero("hasta"),
                                   tipos=tipos or None, limite=limite)
        for m in datos["mensajes"]:
            m["resumen"] = resumen(dict(m["campos"], tipo=m["tipo"]))
        return datos

    def _csv_barco(self, datos):
        salida = io.StringIO()
        w = csv.writer(salida)
        w.writerow(["fecha_hora", "ts", "sesion", "tipo", "canal", "lat", "lon", "vel_kn", "rumbo", "cog", "estado_nav",
                    "dist_km", "rx_lat", "rx_lon", "recepcion", "snr_db", "desvio_hz", "detalle", "nmea"])
        for m in datos["mensajes"]:
            c = m["campos"]
            rec = {0: "directa"}.get(m["repeticion"], "retransmitida" if m["repeticion"] else "")
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["ts"])), f"{m['ts']:.3f}", m["sesion_id"],
                        m["tipo"], m["canal"], c.get("lat", ""), c.get("lon", ""), c.get("sog_kn", ""), c.get("rumbo", ""),
                        c.get("cog", ""), c.get("estado_nav", ""), "" if m["dist_km"] is None else f"{m['dist_km']:.3f}",
                        m["rx_lat"] or "", m["rx_lon"] or "", rec, f"{m['snr_db']:.1f}", f"{m['desvio_hz']:.0f}",
                        m["resumen"], " | ".join(m["nmea"])])
        nombre = f"historial_{datos['barco'].get('mmsi')}.csv"
        self.set_header("Content-Type", "text/csv; charset=utf-8")
        self.set_header("Content-Disposition", f'attachment; filename="{nombre}"')
        self.write("\ufeff" + salida.getvalue())

    def get(self, que):
        bd = self.sistema.bd
        if que == "sesiones":
            datos = [s for s in bd.sesiones() if s["n_mensajes"] > 0]
        elif que == "historico":
            sesion = self._numero("sesion")
            datos = bd.historico(desde=self._numero("desde"), hasta=self._numero("hasta"),
                                 sesion=None if sesion is None else int(sesion))
        elif que == "receptor":
            datos = {"posicion": self.sistema.posicion}
        elif que in ("barco", "barco_csv"):
            datos = self._historial_barco(bd)
            if que == "barco_csv":
                self._csv_barco(datos)
                return
        else:
            raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.set_header("Cache-Control", "no-cache")
        self.write(json.dumps(datos, ensure_ascii=False))


class Apagar(tornado.web.RequestHandler):
    def initialize(self, token):
        self.token = token

    def post(self):
        if not self.token or self.get_argument("token", "") != self.token:
            raise tornado.web.HTTPError(403)
        self.write("ok")
        self.finish()
        bucle = tornado.ioloop.IOLoop.current()
        bucle.add_callback(bucle.stop)


class Canal(tornado.websocket.WebSocketHandler):
    def initialize(self, sistema):
        self.sistema = sistema

    def open(self):
        self.quiere_espectro = self.get_argument("espectro", "1") != "0"
        self.sistema.clientes.add(self)
        self.write_message(json.dumps(self.sistema.instantanea(), ensure_ascii=False))

    def on_close(self):
        self.sistema.clientes.discard(self)

    def check_origin(self, origin):
        return True

    async def on_message(self, mensaje):
        try:
            d = json.loads(mensaje)
        except ValueError:
            return
        if not isinstance(d, dict) or d.get("k") != "cmd":
            return
        bucle = tornado.ioloop.IOLoop.current()
        resultado = await bucle.run_in_executor(None, self.sistema.comando, d)
        if "previa" in resultado:
            self.write_message(json.dumps(dict(resultado["previa"], k="borrar_res"), ensure_ascii=False))
            if resultado["previa"]["modo"] != "borrado":
                return
        self.sistema._enviar(json.dumps({"k": "aviso", "texto": resultado["texto"]}, ensure_ascii=False))
        if resultado["vista"]:
            self.sistema._enviar(json.dumps(self.sistema.instantanea(), ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="Interfaz web del receptor AIS")
    ap.add_argument("--archivo", help="reproduce un .cu8 en tiempo real en lugar de usar el SDR")
    ap.add_argument("--repetir", action="store_true", help="repite el archivo sin parar")
    ap.add_argument("--ganancia", type=float, default=config.GANANCIA)
    ap.add_argument("--ppm", type=int, default=config.PPM)
    ap.add_argument("--umbral", type=float, default=3.0, help="umbral de detección sobre el ruido (dB)")
    ap.add_argument("--modo", choices=("estandar", "sensible"), default="sensible", help="demodulador a usar")
    ap.add_argument("--db", default=rutas.DB_DEFECTO, help="archivo SQLite (por defecto funcionalidad/ais.db)")
    ap.add_argument("--token", default=None, help="clave para apagar el servidor desde la aplicación de escritorio")
    ap.add_argument("--puerto", type=int, default=8000)
    args = ap.parse_args()
    args.archivo = rutas.resolver(args.archivo)

    if not os.path.exists(PAGINA):
        print(f"Falta {PAGINA}. Copia la carpeta gui junto a este script.")
        return

    sistema = Sistema(args)
    tornado.httpclient.AsyncHTTPClient.configure(None, max_clients=4)
    rutas_web = [
        (r"/", Pagina),
        (r"/mapa", Pagina, {"ruta": PAGINA_MAPA}),
        (r"/ws", Canal, {"sistema": sistema}),
        (r"/api/(sesiones|historico|receptor|barco|barco_csv)", Api, {"sistema": sistema}),
        (r"/tiles/(\d+)/(\d+)/(\d+)\.png", Mosaicos),
        (r"/vendor/(.*)", tornado.web.StaticFileHandler, {"path": VENDOR}),
        (r"/gui/(.*\.(?:js|css))", tornado.web.StaticFileHandler, {"path": GUI}),
    ]
    if args.token:
        rutas_web.append((r"/apagar", Apagar, {"token": args.token}))
    app = tornado.web.Application(rutas_web)
    app.listen(args.puerto)
    bucle = tornado.ioloop.IOLoop.current()
    sistema.arrancar_todo(bucle)
    tornado.ioloop.PeriodicCallback(sistema.tic_estado, 1000).start()
    print(f"Interfaz en http://localhost:{args.puerto}   Mapa: http://localhost:{args.puerto}/mapa   (Ctrl+C para detener)")
    print(f"Fuente: {sistema.detalle}   Base de datos: {args.db} (sesión {sistema.sesion})")
    try:
        bucle.start()
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
    finally:
        guardados = sistema.guardados
        sistema.finalizar()
        print(f"Última sesión: {guardados} mensajes guardados en {args.db}.")


if __name__ == "__main__":
    main()
