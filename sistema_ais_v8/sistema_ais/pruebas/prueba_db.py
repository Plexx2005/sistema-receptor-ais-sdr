import _ruta
import csv
import os
import sqlite3
import sys

import numpy as np

import config
from base_datos import BaseDatos
from receptor import ReceptorAIS
from sdr import cu8_a_complejo

BASE = os.path.dirname(os.path.abspath(__file__))
ARCHIVO = os.path.join(BASE, "sintetico.cu8")
RUTA_DB = os.path.join(BASE, "prueba_ais.db")
RUTA_CSV = os.path.join(BASE, "prueba_mensajes.csv")
fallos = []


def verificar(descripcion, condicion, detalle=""):
    estado = "OK    " if condicion else "FALLÓ "
    print(f"  [{estado}] {descripcion}" + (f"  ({detalle})" if detalle else ""))
    if not condicion:
        fallos.append(descripcion)


def decodificar():
    crudo = np.fromfile(ARCHIVO, dtype=np.uint8)
    n_bytes = 2 * int(0.5 * config.FS)
    rx = ReceptorAIS()
    eventos = []
    for i in range(0, len(crudo) - 1, n_bytes):
        eventos += rx.procesar(cu8_a_complejo(crudo[i:i + n_bytes]))
    return eventos, rx.metricas


def main():
    if not os.path.exists(ARCHIVO):
        print(f"No existe {ARCHIVO}. Ejecuta antes: python ais_sintetico.py")
        sys.exit(1)
    for ruta in (RUTA_DB, RUTA_DB + "-wal", RUTA_DB + "-shm", RUTA_CSV):
        if os.path.exists(ruta):
            os.remove(ruta)

    eventos, metricas = decodificar()
    print(f"Mensajes decodificados de {ARCHIVO}: {len(eventos)}")

    bd = BaseDatos(RUTA_DB)
    sid = bd.abrir_sesion("archivo", ARCHIVO, config.GANANCIA, config.PPM, 3.0, inicio=1_000_000.0)
    nuevos = [bd.guardar_evento(ev, ts=1_000_000.0 + ev["t"]) for ev in eventos]
    bd.guardar_metricas(metricas, ts=1_000_004.0, forzar=True)

    print("--- Escritura ---")
    verificar("Los 10 mensajes se guardan como nuevos", sum(nuevos) == 10, f"{sum(nuevos)} nuevos")
    repetidos = [bd.guardar_evento(ev, ts=1_000_000.0 + ev["t"]) for ev in eventos]
    verificar("Guardar de nuevo los mismos mensajes no duplica", sum(repetidos) == 0,
              f"{sum(repetidos)} duplicados")
    verificar("La tabla mensajes tiene 10 filas", bd.contar("mensajes") == 10, str(bd.contar("mensajes")))

    print("--- Lectura de mensajes ---")
    msgs = bd.ultimos_mensajes(10)
    verificar("Se leen 10 mensajes", len(msgs) == 10)
    por_nmea = {m["nmea"]: m for m in msgs}
    todos_nmea = all("\n".join(ev["nmea"]) in por_nmea for ev in eventos)
    verificar("Cada NMEA guardado coincide con el decodificado", todos_nmea)
    tipo5 = [m for m in msgs if m["tipo"] == 5]
    verificar("El mensaje tipo 5 conserva sus 2 sentencias", len(tipo5) == 1 and tipo5[0]["nmea"].count("\n") == 1)
    verificar("Cada sentencia empieza con !AIVDM", all(l.startswith("!AIVDM") for m in msgs for l in m["nmea"].split("\n")))

    print("--- Barcos ---")
    barcos = {b["mmsi"]: b for b in bd.barcos()}
    verificar("Hay 4 barcos (2 clase A y 2 clase B)", set(barcos) == {730151688, 538004493, 423302100, 271041815}, str(sorted(barcos)))
    b1 = barcos.get(730151688, {})
    b2 = barcos.get(538004493, {})
    verificar("730151688 tiene 3 mensajes", b1.get("n_mensajes") == 3, str(b1.get("n_mensajes")))
    verificar("538004493 tiene 3 mensajes", b2.get("n_mensajes") == 3, str(b2.get("n_mensajes")))
    verificar("El nombre del tipo 5 completa el barco 538004493", b2.get("nombre") == "TEAM DREAM",
              str(b2.get("nombre")))
    verificar("Posición de 538004493 conservada tras el tipo 5",
              b2.get("lat") is not None and abs(b2["lat"] - 10.97973) < 1e-4, str(b2.get("lat")))
    verificar("Posición de 730151688", b1.get("lat") is not None and abs(b1["lat"] - 10.97208) < 1e-4,
              str(b1.get("lat")))
    verificar("primera_vez es anterior a ultima_vez", b1.get("primera_vez") < b1.get("ultima_vez"))

    print("--- Métricas y sesiones ---")
    verificar("Hay 1 fila de métricas", bd.contar("metricas") == 1)
    met = bd.metricas(sid)[0]
    verificar("Las métricas guardan 10 mensajes y 10 ráfagas", met["mensajes"] == 10 and met["rafagas"] == 10,
              f"{met['mensajes']} / {met['rafagas']}")
    verificar("El piso de ruido de A se guardó", met["piso_a_db"] is not None)
    bd.cerrar_sesion(fin=1_000_004.0)
    ses = bd.sesiones()
    verificar("La sesión queda cerrada con fin", len(ses) == 1 and ses[0]["fin"] == 1_000_004.0)
    verificar("La sesión cuenta sus 10 mensajes", ses[0]["n_mensajes"] == 10)

    print("--- Exportación ---")
    filas = bd.exportar_csv(RUTA_CSV, "mensajes")
    with open(RUTA_CSV, encoding="utf-8-sig", newline="") as f:
        leidas = list(csv.DictReader(f))
    verificar("El CSV tiene 10 filas", filas == 10 and len(leidas) == 10)
    verificar("El CSV incluye la columna nmea", "nmea" in leidas[0])
    bd.cerrar()

    print("--- Persistencia ---")
    bd2 = BaseDatos(RUTA_DB)
    verificar("Al reabrir el archivo, los datos siguen ahí",
              bd2.contar("mensajes") == 10 and bd2.contar("barcos") == 4)
    sid2 = bd2.abrir_sesion("archivo", "segunda sesión")
    bd2.guardar_evento(eventos[0], ts=2_000_000.0)
    verificar("Una segunda sesión puede guardar el mismo mensaje", bd2.contar("mensajes") == 11)
    verificar("Los barcos acumulan entre sesiones",
              {b["mmsi"]: b for b in bd2.barcos()}[730151688]["n_mensajes"] == 4)
    bd2.cerrar()

    con = sqlite3.connect(RUTA_DB)
    tablas = sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    con.close()
    print("Tablas en la base de datos:", tablas)

    print()
    if fallos:
        print(f"RESULTADO: {len(fallos)} comprobaciones fallaron")
        sys.exit(1)
    print("RESULTADO: todas las comprobaciones pasaron")
    print(f"Archivos creados: {RUTA_DB}, {RUTA_CSV}")


if __name__ == "__main__":
    main()
