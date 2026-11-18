import _ruta
import argparse
import time

import numpy as np

import config
from ais_sintetico import escenario_base, generar_captura
from receptor import ReceptorAIS
from receptor_sensible import ReceptorSensible

MODOS = {
    "estandar": lambda u: ReceptorAIS(umbral_db=u),
    "sensible": lambda u: ReceptorSensible(umbral_db=u),
    "sensible_sin_reparo": lambda u: ReceptorSensible(umbral_db=u, reparar=False),
    "sensible_vent48": lambda u: ReceptorSensible(umbral_db=u, ventana_pot=48),
}


def escenario_aleatorio(rng, desvio_max):
    eventos = []
    for e in escenario_base():
        e = dict(e)
        e["desvio_hz"] = float(rng.uniform(-desvio_max, desvio_max))
        eventos.append(e)
    return eventos


def correr(receptor, iq, verdad):
    reales = {v["bits"] for v in verdad}
    recuperados, fantasmas = 0, 0
    for i in range(0, len(iq), config.CHUNK):
        for ev in receptor.procesar(iq[i:i + config.CHUNK]):
            if ev["bits"] in reales:
                recuperados += 1
            else:
                fantasmas += 1
    return recuperados, fantasmas


def main():
    ap = argparse.ArgumentParser(description="Curva de mensajes recuperados según el SNR")
    ap.add_argument("--snr", default="6,8,10,12,14,16", help="SNR en 15 kHz, separados por comas")
    ap.add_argument("--semillas", type=int, default=4)
    ap.add_argument("--umbral", type=float, default=3.0)
    ap.add_argument("--desvio", type=float, default=3000.0, help="desvío máximo aleatorio (Hz)")
    ap.add_argument("--modos", default="estandar,sensible")
    args = ap.parse_args()

    modos = args.modos.split(",")
    snrs = [float(s) for s in args.snr.split(",")]
    encabezado = f"{'SNR (dB)':>8} {'total':>6}"
    for m in modos:
        encabezado += f" | {m + ' ok/fant':>26}"
    print(encabezado)
    tiempos = {m: 0.0 for m in modos}
    for snr in snrs:
        totales = 0
        resultados = {m: [0, 0] for m in modos}
        for semilla in range(args.semillas):
            rng = np.random.default_rng(100 + semilla)
            eventos = escenario_aleatorio(rng, args.desvio)
            iq, verdad = generar_captura(eventos, 3.6, snr_db=snr, semilla=semilla, dc=0.02 * (1 + 1j))
            totales += len(verdad)
            for m in modos:
                t0 = time.time()
                ok, fant = correr(MODOS[m](args.umbral), iq, verdad)
                tiempos[m] += time.time() - t0
                resultados[m][0] += ok
                resultados[m][1] += fant
        fila = f"{snr:>8.0f} {totales:>6}"
        for m in modos:
            fila += f" | {resultados[m][0]:>12} / {resultados[m][1]:<11}"
        print(fila)
    print("Tiempo de proceso (s):", {m: round(t, 1) for m, t in tiempos.items()})


if __name__ == "__main__":
    main()
