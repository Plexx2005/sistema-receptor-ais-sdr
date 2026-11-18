import argparse
import os
import queue
import threading
import time

import numpy as np

import config
import nube
import rutas
from base_datos import BaseDatos
from receptor_sensible import crear_receptor
from sdr import SDR, cu8_a_complejo
from decodificar_archivo import resumen

CAPACIDAD_COLA = 100


def lector_sdr(args, cola, parar, estado):
    try:
        with SDR(fc=config.FC, ganancia=args.ganancia, ppm=args.ppm, chunk=config.CHUNK) as sdr:
            sdr.imprimir_info()
            while not parar.is_set():
                crudo = sdr.leer_bytes(config.CHUNK)
                try:
                    cola.put_nowait(crudo)
                except queue.Full:
                    estado["perdidos"] += 1
    except Exception as e:
        estado["error"] = e
    finally:
        cola.put(None)


def lector_archivo(args, cola, parar, estado):
    crudo = np.fromfile(args.archivo, dtype=np.uint8)
    duracion_bloque = config.CHUNK / 2 / config.FS
    try:
        while not parar.is_set():
            for i in range(0, len(crudo), config.CHUNK):
                if parar.is_set():
                    break
                try:
                    cola.put_nowait(crudo[i:i + config.CHUNK])
                except queue.Full:
                    estado["perdidos"] += 1
                time.sleep(duracion_bloque)
            if not args.repetir:
                break
    finally:
        cola.put(None)


def main():
    ap = argparse.ArgumentParser(description="Receptor AIS en tiempo real (SDR o archivo .cu8 reproducido)")
    ap.add_argument("--archivo", help="reproduce un .cu8 en tiempo real en lugar de usar el SDR")
    ap.add_argument("--repetir", action="store_true", help="repite el archivo sin parar")
    ap.add_argument("--ganancia", type=float, default=config.GANANCIA)
    ap.add_argument("--ppm", type=int, default=config.PPM)
    ap.add_argument("--umbral", type=float, default=3.0, help="umbral de detección sobre el ruido (dB)")
    ap.add_argument("--modo", choices=("estandar", "sensible"), default="sensible", help="demodulador a usar")
    ap.add_argument("--informe", type=float, default=10.0, help="segundos entre líneas de estado")
    ap.add_argument("--db", help="archivo SQLite donde guardar mensajes, barcos y métricas (ej. ais.db)")
    ap.add_argument("--lat", type=float, help="latitud del receptor (por defecto, la última guardada en la BD)")
    ap.add_argument("--lon", type=float, help="longitud del receptor")
    args = ap.parse_args()
    args.archivo = rutas.resolver(args.archivo)

    cola = queue.Queue(maxsize=CAPACIDAD_COLA)
    parar = threading.Event()
    estado = {"perdidos": 0, "error": None}
    destino = lector_archivo if args.archivo else lector_sdr
    hilo = threading.Thread(target=destino, args=(args, cola, parar, estado), daemon=True)
    hilo.start()

    rx = crear_receptor(args.modo, args.umbral)
    bd = None
    sinc = None
    guardados = 0
    pos_rx = {"lat": args.lat, "lon": args.lon, "fuente": "manual"} if args.lat is not None and args.lon is not None else None
    if args.db:
        bd = BaseDatos(args.db)
        if pos_rx is None:
            pos_rx = bd.ultima_posicion_receptor()
        sid = bd.abrir_sesion("archivo" if args.archivo else "sdr", os.path.basename(args.archivo) if args.archivo else "RTL-SDR",
                              args.ganancia, args.ppm, args.umbral, rx=pos_rx)
        print(f"Guardando en {args.db} (sesión {sid})")
        if pos_rx:
            print(f"Receptor en {pos_rx['lat']:.5f}, {pos_rx['lon']:.5f}")
        sinc = nube.iniciar(args.db)
    inicio = time.time()
    ultimo_informe = inicio
    bloques = 0
    print("Escuchando AIS 1 (161.975 MHz) y AIS 2 (162.025 MHz). Ctrl+C para detener.")
    try:
        while True:
            try:
                crudo = cola.get(timeout=1.0)
            except queue.Empty:
                continue
            if crudo is None:
                break
            bloques += 1
            eventos = rx.procesar(cu8_a_complejo(crudo))
            for ev in eventos:
                print(f"[{time.strftime('%H:%M:%S')}] canal {ev['canal']}  SNR {ev['snr_db']:4.1f} dB  "
                      f"tipo {ev['tipo']}  MMSI {ev['mmsi']}")
                r = resumen(ev)
                if r:
                    print("    ", r)
                for s in ev["nmea"]:
                    print("    ", s)
                if bd is not None:
                    guardados += bd.guardar_evento(ev, rx=pos_rx)
            ahora = time.time()
            if bd is not None:
                bd.guardar_metricas(rx.metricas, ts=ahora)
                if eventos and sinc is not None:
                    bd.sincronizar()
                    sinc.avisar()
            if ahora - ultimo_informe >= args.informe:
                ultimo_informe = ahora
                m = rx.metricas
                piso = {k: round(float(v), 1) for k, v in m["piso_db"].items() if v is not None}
                extra = f" | guardados {guardados}" if bd is not None else ""
                print(f"-- {ahora - inicio:5.0f} s | mensajes {m['tramas_ok']} | ráfagas {m['rafagas']} "
                      f"| sin CRC {m['rafagas_sin_crc']} | piso {piso} dB | cola {cola.qsize()} "
                      f"| bloques perdidos {estado['perdidos']}{extra}")
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
    finally:
        parar.set()
        hilo.join(timeout=3)
        if bd is not None:
            bd.guardar_metricas(rx.metricas, forzar=True)
            bd.cerrar_sesion()
            if sinc is not None:
                sinc.avisar()
                sinc.detener(vaciar=True, limite_s=8)
            bd.cerrar()
    if estado["error"]:
        print("Error del lector:", estado["error"])
    m = rx.metricas
    print(f"Total: {m['tramas_ok']} mensajes con CRC válido, {m['rafagas']} ráfagas, "
          f"{estado['perdidos']} bloques perdidos.")
    if bd is not None:
        print(f"Guardados en {args.db}: {guardados} mensajes.")


if __name__ == "__main__":
    main()
