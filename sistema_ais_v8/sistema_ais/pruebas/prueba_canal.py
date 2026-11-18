import _ruta
import argparse
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import welch

import config
from ais_sintetico import escenario_base, generar_captura
from canal import BancoCanales, FS_CANAL

ap = argparse.ArgumentParser()
ap.add_argument("--png", default=os.path.join(config.CARPETA_WINDOWS, "canalizacion.png"))
args = ap.parse_args()

escenario = escenario_base()
iq, verdad = generar_captura(escenario, 3.6, snr_db=70, semilla=1, dc=0.0)

banco = BancoCanales()
canales = {n: [] for n in banco.canales}
bloque = 131072
for i in range(0, len(iq), bloque):
    for n, s in banco.procesar(iq[i:i + bloque]).items():
        canales[n].append(s)
canales = {n: np.concatenate(v) for n, v in canales.items()}

print("Muestras de entrada  :", len(iq), f"a {config.FS / 1e6:.3f} MS/s")
for n, s in canales.items():
    print(f"Muestras canal {n}     :", len(s), f"a {FS_CANAL / 1e3:.0f} kS/s (factor {len(iq) // len(s)})")

ventanas = {"A": [], "B": []}
for v in verdad:
    dur = (len(v["bits"]) + 58) / 9600
    a = int((v["t"] + 0.004) * FS_CANAL)
    b = int((v["t"] + dur - 0.004) * FS_CANAL)
    ventanas[v["canal"]].append((a, b))


def potencia(s, ints):
    return np.mean(np.concatenate([np.abs(s[a:b]) ** 2 for a, b in ints]))


print("--- Rechazo del canal adyacente ---")
print(f"{'Canal':>6} {'P ráfagas propias':>19} {'P ráfagas del otro canal':>26} {'rechazo (dB)':>13}")
for n in ("A", "B"):
    otro = "B" if n == "A" else "A"
    p_propia = potencia(canales[n], ventanas[n])
    p_otra = potencia(canales[n], ventanas[otro])
    print(f"{n:>6} {10 * np.log10(p_propia):>16.1f} dB {10 * np.log10(p_otra):>23.1f} dB {10 * np.log10(p_propia / p_otra):>13.1f}")

fig, ejes = plt.subplots(1, 3, figsize=(15, 4))
f, p = welch(iq, fs=config.FS, nperseg=8192, return_onesided=False)
ejes[0].plot(np.fft.fftshift(f) / 1e3, 10 * np.log10(np.fft.fftshift(p)), lw=0.7)
ejes[0].set_xlim(-100, 100)
for x in (-25, 25):
    ejes[0].axvline(x, color="r", ls="--", lw=0.8)
ejes[0].set_title("Entrada: 1.536 MS/s (ambos canales)")
for ax, n, otro in ((ejes[1], "A", "B"), (ejes[2], "B", "A")):
    for ints, etiqueta, color in ((ventanas[n], f"ráfagas del canal {n}", "tab:green"),
                                  (ventanas[otro], f"ráfagas del canal {otro}", "tab:red")):
        seg = np.concatenate([canales[n][a:b] for a, b in ints])
        f, p = welch(seg, fs=FS_CANAL, nperseg=256, return_onesided=False)
        ax.plot(np.fft.fftshift(f) / 1e3, 10 * np.log10(np.fft.fftshift(p) + 1e-20), label=etiqueta, color=color)
    ax.set_title(f"Salida del canal {n}: 48 kS/s")
    ax.legend()
for ax in ejes:
    ax.set_xlabel("Frecuencia (kHz)")
    ax.set_ylabel("dB")
    ax.grid(True)
fig.tight_layout()
fig.savefig(args.png, dpi=120)
print("Imagen guardada en", args.png)
