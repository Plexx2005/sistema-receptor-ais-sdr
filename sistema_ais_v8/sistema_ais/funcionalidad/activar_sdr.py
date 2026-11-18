import argparse
import os
import time

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import welch

import config
import rutas
from sdr import SDR, cu8_a_complejo


def main():
    ap = argparse.ArgumentParser(description="Activa el RTL-SDR y captura muestras I/Q")
    ap.add_argument("--segundos", type=float, default=2.0, help="duración de la captura")
    ap.add_argument("--frecuencia", type=float, default=config.FC / 1e6, help="frecuencia central (MHz)")
    ap.add_argument("--ganancia", type=float, default=config.GANANCIA, help="ganancia del sintonizador (dB)")
    ap.add_argument("--ppm", type=int, default=config.PPM, help="corrección de frecuencia (ppm)")
    args = ap.parse_args()

    fc = int(round(args.frecuencia * 1e6))
    etiqueta = f"{args.frecuencia:g}"
    es_ais = abs(args.frecuencia - 162.0) < 0.01

    with SDR(fc=fc, ganancia=args.ganancia, ppm=args.ppm) as sdr:
        sdr.imprimir_info()
        crudo = sdr.capturar(args.segundos)

    iq = cu8_a_complejo(crudo)
    print(f"Muestras leídas: {len(iq)}")
    potencia = np.mean(np.abs(iq) ** 2)
    print(f"Potencia media: {10 * np.log10(potencia):.1f} dB (relativa a escala completa)")
    maximo = max(np.max(np.abs(iq.real)), np.max(np.abs(iq.imag)))
    print(f"Valor máximo |I|,|Q|: {maximo:.2f}  (cerca de 1.0 indicaría saturación)")

    os.makedirs(rutas.CARPETA_CAPTURAS, exist_ok=True)
    nombre = os.path.join(rutas.CARPETA_CAPTURAS, time.strftime(f"captura_{etiqueta}MHz_%Y%m%d_%H%M%S.cu8"))
    crudo.tofile(nombre)
    print("Muestras crudas guardadas en", nombre)

    f, p = welch(iq, fs=config.FS, nperseg=4096, return_onesided=False)
    f = np.fft.fftshift(f) / 1e3
    p = 10 * np.log10(np.fft.fftshift(p))

    fig, ejes = plt.subplots(1, 2, figsize=(13, 4))
    for ax, limite, titulo in ((ejes[0], config.FS / 2e3, "Banda completa"),
                               (ejes[1], 100, "Zoom ±100 kHz")):
        ax.plot(f, p)
        if es_ais:
            ax.axvline(-25, color="r", ls="--", label="AIS 1 (161.975 MHz)")
            ax.axvline(+25, color="g", ls="--", label="AIS 2 (162.025 MHz)")
            ax.legend()
        ax.set_xlim(-limite, limite)
        ax.set_xlabel(f"Frecuencia respecto a {args.frecuencia:.3f} MHz (kHz)")
        ax.set_ylabel("Densidad espectral (dB)")
        ax.set_title(titulo)
        ax.grid(True)
    fig.tight_layout()

    png = "espectro_ais.png" if es_ais else f"espectro_{etiqueta}MHz.png"
    ruta = os.path.join(config.CARPETA_WINDOWS, png)
    fig.savefig(ruta, dpi=120)
    print("Espectro guardado en", ruta.replace("/mnt/c/", "C:\\").replace("/", "\\"))


if __name__ == "__main__":
    main()
