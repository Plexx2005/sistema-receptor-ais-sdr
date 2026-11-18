import argparse
import json
import os
import time

import numpy as np

import config
import rutas
from base_datos import BaseDatos
from receptor_sensible import crear_receptor
from sdr import cu8_a_complejo


def resumen(ev):
    t = ev["tipo"]
    try:
        if t in (1, 2, 3):
            return (f"pos lat {ev['lat']:.5f} lon {ev['lon']:.5f}  vel {ev['sog_kn']} kn  "
                    f"rumbo {ev['rumbo']}°  estado {ev['estado_nav']}")
        if t in (18, 19):
            r = (f"pos lat {ev['lat']:.5f} lon {ev['lon']:.5f}  vel {ev['sog_kn']} kn  "
                 f"rumbo {ev['rumbo']}°  (clase B)")
            return r + (f"  nombre {ev['nombre']}" if t == 19 else "")
        if t == 5:
            return f"nombre {ev['nombre']}  indicativo {ev['indicativo']}  destino {ev['destino']}"
        if t == 24:
            if ev.get("parte") == "A":
                return f"clase B parte A  nombre {ev['nombre']}"
            return f"clase B parte B  indicativo {ev['indicativo']}  tipo de nave {ev['tipo_nave']}"
        if t in (4, 11):
            return f"estación base  lat {ev['lat']:.5f} lon {ev['lon']:.5f}  UTC {ev['fecha_utc']}"
        if t == 21:
            return f"ayuda a la navegación {ev['nombre']}  lat {ev['lat']:.5f} lon {ev['lon']:.5f}"
    except KeyError:
        return ""
    return ""


def main():
    ap = argparse.ArgumentParser(description="Decodifica mensajes AIS de una captura .cu8")
    ap.add_argument("archivo")
    ap.add_argument("--bloque", type=float, default=0.5, help="segundos procesados por iteración")
    ap.add_argument("--umbral", type=float, default=3.0, help="umbral de detección sobre el ruido (dB)")
    ap.add_argument("--modo", choices=("estandar", "sensible"), default="sensible", help="demodulador a usar")
    ap.add_argument("--verdad", help="archivo *_verdad.json de una captura sintética")
    ap.add_argument("--db", help="archivo SQLite donde guardar mensajes, barcos y métricas (ej. ais.db)")
    args = ap.parse_args()
    args.archivo = rutas.resolver(args.archivo)
    args.verdad = rutas.resolver(args.verdad)

    crudo = np.fromfile(args.archivo, dtype=np.uint8)
    n_bytes = 2 * int(args.bloque * config.FS)
    rx = crear_receptor(args.modo, args.umbral)
    eventos = []
    t0 = time.time()
    for i in range(0, len(crudo) - 1, n_bytes):
        eventos += rx.procesar(cu8_a_complejo(crudo[i:i + n_bytes]))
    dt = time.time() - t0
    duracion = len(crudo) / 2 / config.FS

    guardados = None
    if args.db:
        inicio_captura = os.path.getmtime(args.archivo) - duracion
        with BaseDatos(args.db) as bd:
            sid = bd.abrir_sesion("archivo", os.path.basename(args.archivo), config.GANANCIA,
                                  config.PPM, args.umbral, inicio=inicio_captura)
            guardados = sum(bd.guardar_evento(ev, ts=inicio_captura + ev["t"]) for ev in eventos)
            bd.guardar_metricas(rx.metricas, ts=inicio_captura + duracion, forzar=True)
            bd.cerrar_sesion(fin=inicio_captura + duracion)

    print(f"Archivo: {args.archivo}  ({duracion:.2f} s de señal, procesada en {dt:.2f} s)")
    print("--- Mensajes AIS decodificados ---")
    for ev in eventos:
        print(f"[{ev['t']:7.3f} s] canal {ev['canal']}  SNR {ev['snr_db']:5.1f} dB  desvío {ev['desvio_hz']:+6.0f} Hz  "
              f"tipo {ev['tipo']}  MMSI {ev['mmsi']}")
        r = resumen(ev)
        if r:
            print("             ", r)
        for s in ev["nmea"]:
            print("             ", s)
    if not eventos:
        print("(ningún mensaje con CRC válido)")

    m = rx.metricas
    print("--- Métricas ---")
    print("Ráfagas detectadas          :", m["rafagas"])
    print("Mensajes con CRC válido     :", m["tramas_ok"])
    print("Ráfagas sin mensaje válido  :", m["rafagas_sin_crc"])
    print("Mensajes por canal          :", m["por_canal"])
    print("Mensajes por tipo           :", m["por_tipo"])
    print("Piso de ruido por canal (dB):", {k: round(float(v), 1) for k, v in m["piso_db"].items() if v is not None})
    print(f"Velocidad de proceso        : {duracion / dt:.1f} veces el tiempo real")
    if guardados is not None:
        print(f"Guardados en {args.db}      : {guardados} mensajes (sesión {sid})")

    if args.verdad:
        with open(args.verdad) as f:
            verdad = json.load(f)
        ok = 0
        print("--- Comparación con lo transmitido ---")
        for v in verdad:
            hallado = [e for e in eventos if e["bits"] == v["bits"] and e["canal"] == v["canal"]]
            ok += bool(hallado)
            igual = bool(hallado) and hallado[0]["nmea"] == v["sentencias"]
            estado = "RECIBIDO, NMEA idéntico" if igual else ("RECIBIDO" if hallado else "perdido")
            print(f"  t={v['t']:.2f} s canal {v['canal']}: {estado}")
        extras = len(eventos) - ok
        print(f"Recibidos {ok} de {len(verdad)}; mensajes no transmitidos: {extras}")


if __name__ == "__main__":
    main()
