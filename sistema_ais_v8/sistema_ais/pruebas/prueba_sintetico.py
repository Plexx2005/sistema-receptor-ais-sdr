import _ruta
import argparse
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import spectrogram

import config
from ais_sintetico import (escenario_base, generar_captura, a_cu8, bits_de_sentencias,
                           niveles_de_mensaje, modular_gmsk)
from nmea import a_nmea

ap = argparse.ArgumentParser()
ap.add_argument("--snr", type=float, default=15.0)
ap.add_argument("--png", default=os.path.join(config.CARPETA_WINDOWS, "sintetico_espectrograma.png"))
args = ap.parse_args()

escenario = escenario_base()

print("--- Transmisor simulado ---")
ev = escenario[0]
bits = bits_de_sentencias(ev["sentencias"])
niveles = niveles_de_mensaje(bits)
print("Bits del mensaje AIS         :", len(bits))
print("Bits transmitidos (con CRC)  :", len(niveles))
print("Duración de la ráfaga        :", f"{len(niveles) / 9600 * 1000:.1f} ms")
regenerada = a_nmea(bits, canal=ev["sentencias"][0].split(",")[4])
print("NMEA regenerado idéntico     :", regenerada == ev["sentencias"])

senal = modular_gmsk(niveles, config.FS)
print("Muestras de la ráfaga        :", len(senal), f"({config.FS // 9600} por símbolo)")
print("Amplitud de la señal (min,max):", f"{np.abs(senal).min():.3f}, {np.abs(senal).max():.3f}  (envolvente constante)")
f_inst = np.diff(np.unwrap(np.angle(senal))) * config.FS / (2 * np.pi)
print("Desviación de frecuencia     :", f"{f_inst.min():.0f} Hz a {f_inst.max():.0f} Hz")

print("--- Captura sintética ---")
iq, verdad = generar_captura(escenario, 3.6, snr_db=args.snr, semilla=1, dc=0.02 * (1 + 1j))
crudo = a_cu8(iq)
print("Muestras I/Q                 :", len(iq))
print("Bytes CU8                    :", crudo.nbytes)
print("Potencia media               :", f"{10 * np.log10(np.mean(np.abs(iq) ** 2)):.1f} dB")
print("Ráfagas incluidas            :", len(verdad))

f, t, S = spectrogram(iq, fs=config.FS, nperseg=2048, noverlap=1024, return_onesided=False, mode="psd")
S = 10 * np.log10(np.fft.fftshift(S, axes=0) + 1e-20)
f = np.fft.fftshift(f) / 1e3
sel = np.abs(f) <= 100
plt.figure(figsize=(11, 5))
plt.imshow(S[sel], aspect="auto", origin="lower", cmap="viridis",
           extent=[t[0], t[-1], f[sel][0], f[sel][-1]])
plt.axhline(-25, color="w", ls="--", lw=0.6)
plt.axhline(+25, color="w", ls="--", lw=0.6)
plt.colorbar(label="dB")
plt.xlabel("Tiempo (s)")
plt.ylabel("Frecuencia respecto a 162.000 MHz (kHz)")
plt.title(f"Captura AIS sintética (SNR {args.snr:.0f} dB): cada ráfaga aparece en -25 kHz (AIS 1) o +25 kHz (AIS 2)")
plt.tight_layout()
plt.savefig(args.png, dpi=120)
print("Imagen guardada en", args.png)
