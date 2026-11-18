"""Servidor de PRUEBA que imita la API de ingesta que tendrá la EC2 (ver NUBE_API.md).

Sirve para comprobar el envío desde el receptor antes de crear nada en AWS. Guarda una réplica en un
SQLite aparte, con la misma semántica que tendrá RDS: upsert por (estacion, clave), sin duplicados.

  python servidor_nube_prueba.py --clave MI_CLAVE                (escucha en http://localhost:8090)
  python servidor_nube_prueba.py --clave MI_CLAVE --fallar 0.3   (rechaza al azar el 30 % de los lotes)

Luego, en el receptor:  python nube.py --configurar --url http://localhost:8090 --clave MI_CLAVE
"""
import _ruta  # noqa: F401  (añade funcionalidad/ al path)
import argparse
import hmac
import json
import random
import sqlite3
import time

import tornado.ioloop
import tornado.web

from base_datos import TABLAS_NUBE

ORDEN = ("sesiones", "barcos", "mensajes", "metricas", "posiciones_receptor")


class Replica:
    def __init__(self, ruta):
        self.con = sqlite3.connect(ruta)
        for tabla in ORDEN:
            self.con.execute(f"CREATE TABLE IF NOT EXISTS {tabla} (estacion TEXT NOT NULL, clave INTEGER NOT NULL, "
                             "mod_nube REAL, datos TEXT NOT NULL, recibido REAL NOT NULL, PRIMARY KEY (estacion, clave))")
        self.con.commit()

    def guardar(self, estacion, tablas):
        recibidos = {}
        ahora = time.time()
        with self.con:   # una transacción por lote: o se guarda todo o nada
            for tabla in ORDEN:
                filas = tablas.get(tabla) or []
                clave = TABLAS_NUBE[tabla][0]
                for f in filas:
                    self.con.execute(
                        f"INSERT INTO {tabla} (estacion, clave, mod_nube, datos, recibido) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT (estacion, clave) DO UPDATE SET mod_nube = excluded.mod_nube, "
                        "datos = excluded.datos, recibido = excluded.recibido",
                        (estacion, int(f[clave]), f.get("mod_nube"), json.dumps(f, ensure_ascii=False), ahora))
                if filas:
                    recibidos[tabla] = len(filas)
        return recibidos

    def resumen(self):
        return {t: self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ORDEN}


class Base(tornado.web.RequestHandler):
    def initialize(self, opciones, replica):
        self.op = opciones
        self.replica = replica

    def _autorizado(self):
        dada = self.request.headers.get("X-API-Key", "")
        return hmac.compare_digest(dada.encode(), self.op.clave.encode())

    def responder(self, codigo, datos):
        self.set_status(codigo)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps(datos, ensure_ascii=False))


class Salud(Base):
    def get(self):
        if not self._autorizado():
            return self.responder(401, {"ok": False, "error": "clave no válida"})
        self.responder(200, {"ok": True, "version": 1, "servidor": "prueba local", "replica": self.replica.resumen()})


class Lote(Base):
    def post(self):
        if not self._autorizado():
            return self.responder(401, {"ok": False, "error": "clave no válida"})
        if random.random() < self.op.fallar:
            return self.responder(503, {"ok": False, "error": "fallo simulado"})
        try:
            lote = json.loads(self.request.body)
            assert lote.get("version") == 1 and isinstance(lote.get("tablas"), dict) and lote.get("estacion")
        except (ValueError, AssertionError):
            return self.responder(400, {"ok": False, "error": "lote mal formado"})
        recibidos = self.replica.guardar(lote["estacion"], lote["tablas"])
        mods = [f.get("mod_nube") or 0 for filas in lote["tablas"].values() for f in filas]
        retraso = time.time() - max(mods) if mods else 0
        print(f"[{time.strftime('%H:%M:%S')}] lote de '{lote['estacion']}': {recibidos}  "
              f"(retraso desde el guardado local: {retraso:.2f} s)", flush=True)
        self.responder(200, {"ok": True, "recibidos": recibidos})


def main():
    ap = argparse.ArgumentParser(description="Servidor de prueba de la API de ingesta en la nube")
    ap.add_argument("--clave", required=True)
    ap.add_argument("--puerto", type=int, default=8090)
    ap.add_argument("--replica", default="replica_nube.db", help="SQLite donde se guarda la réplica")
    ap.add_argument("--fallar", type=float, default=0.0, help="probabilidad de responder 503 a un lote (0 a 1)")
    op = ap.parse_args()
    replica = Replica(op.replica)
    kw = {"opciones": op, "replica": replica}
    app = tornado.web.Application([(r"/api/v1/salud", Salud, kw), (r"/api/v1/lote", Lote, kw)])
    app.listen(op.puerto, decompress_request=True, max_body_size=20 * 1024 * 1024)
    print(f"Servidor de prueba en http://localhost:{op.puerto}  réplica: {op.replica}  {replica.resumen()}", flush=True)
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
