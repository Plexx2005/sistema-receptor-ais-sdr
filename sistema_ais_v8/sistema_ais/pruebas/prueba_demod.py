import _ruta
import argparse
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config
from ais_sintetico import (S1, S2, S3, S4, bits_de_sentencias, niveles_de_mensaje,
                           modular_gmsk, ruido_complejo)
from demod import demodular_rafaga, frecuencia_filtrada, FS_CANAL
from ais_msg import decodificar

ap = argparse.ArgumentParser()
ap.add_argument("--intentos", type=int, default=40)
ap.add_argument("--png", default=os.path.join(config.CARPETA_WINDOWS, "curva_demodulador.png"))
args = ap.parse_args()

rng = np.random.default_rng(3)
mensajes = [bits_de_sentencias(s) for s in ([S1], [S2], [S3], S4)]
ANCHO = 15_000


def rafaga_ruidosa(bits, snr_db, desvio_hz):
    base = modular_gmsk(niveles_de_mensaje(bits), FS_CANAL)
    n = np.arange(len(base))
    amplitud = np.sqrt(10 ** (snr_db / 10) * ANCHO / FS_CANAL)
    senal = amplitud * base * np.exp(1j * (2 * np.pi * desvio_hz * n / FS_CANAL + rng.uniform(0, 6.28)))
    pad = 100
    ruido = ruido_complejo(len(base) + 2 * pad, rng)
    ruido[pad:pad + len(base)] += senal
    return ruido


print("--- Sin ruido ---")
bits = mensajes[0]
x = rafaga_ruidosa(bits, 60, 0.0)
encontrados, desvio = demodular_rafaga(x)
original = "".join(str(b) for b in bits)
print("Mensaje recuperado idéntico:", original in encontrados)
print("Fase de muestreo usada     :", encontrados.get(original))
print("Desvío estimado            :", f"{desvio:.1f} Hz (real 0 Hz)")
print(decodificar(original))

print("--- Con desvío de frecuencia de +2500 Hz y SNR 20 dB ---")
x = rafaga_ruidosa(bits, 20, 2500.0)
encontrados, desvio = demodular_rafaga(x)
print("Mensaje recuperado idéntico:", original in encontrados)
print("Desvío estimado            :", f"{desvio:.0f} Hz (real 2500 Hz)")

print("--- Tasa de decodificación según la relación señal/ruido ---")
print(f"{'SNR (dB)':>9} {'ráfagas':>8} {'decodificadas':>14} {'porcentaje':>11}")
snrs = list(range(4, 19, 2))
tasas = []
for snr in snrs:
    ok = 0
    for k in range(args.intentos):
        b = mensajes[k % len(mensajes)]
        desv = rng.uniform(-3000, 3000)
        enc, _ = demodular_rafaga(rafaga_ruidosa(b, snr, desv))
        ok += "".join(str(v) for v in b) in enc
    tasas.append(100 * ok / args.intentos)
    print(f"{snr:>9} {args.intentos:>8} {ok:>14} {tasas[-1]:>10.0f}%")

plt.figure(figsize=(7, 4))
plt.plot(snrs, tasas, "o-")
plt.xlabel("SNR de la ráfaga en 15 kHz (dB)")
plt.ylabel("Ráfagas decodificadas (%)")
plt.title("Rendimiento del demodulador con señal sintética")
plt.grid(True)
plt.ylim(-2, 102)
plt.tight_layout()
plt.savefig(args.png, dpi=120)
print("Gráfica guardada en", args.png)
