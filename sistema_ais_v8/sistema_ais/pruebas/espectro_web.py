import _ruta
import argparse

import numpy as np
import matplotlib
matplotlib.use("WebAgg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from scipy.signal import welch

import config
from sdr import SDR

NPERSEG = 4096
BYTES_LEER = 131072


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frecuencia", type=float, default=config.FC / 1e6, help="MHz")
    ap.add_argument("--ganancia", type=float, default=config.GANANCIA, help="dB")
    ap.add_argument("--ppm", type=int, default=config.PPM)
    args = ap.parse_args()
    fc = int(round(args.frecuencia * 1e6))

    with SDR(fc=fc, ganancia=args.ganancia, ppm=args.ppm, chunk=BYTES_LEER) as sdr:
        sdr.imprimir_info()
        bloques = sdr.bloques(BYTES_LEER)

        fig, ax = plt.subplots(figsize=(10, 5))
        fig.canvas.manager.set_window_title("Espectro en vivo - RTL-SDR")
        linea, = ax.plot([], [], lw=0.9)
        ax.set_xlim(-config.FS / 2e3, config.FS / 2e3)
        ax.set_ylim(-120, -40)
        ax.set_xlabel(f"Frecuencia relativa a {fc / 1e6:.3f} MHz (kHz)")
        ax.set_ylabel("Potencia (dB)")
        ax.grid(alpha=0.3)
        estado = {"suave": None}

        def actualizar(_):
            iq = next(bloques)
            f, p = welch(iq, fs=config.FS, nperseg=NPERSEG, return_onesided=False)
            f = np.fft.fftshift(f) / 1e3
            p = 10 * np.log10(np.fft.fftshift(p) + 1e-20)
            if estado["suave"] is None:
                estado["suave"] = p
            else:
                estado["suave"] = 0.7 * estado["suave"] + 0.3 * p
            linea.set_data(f, estado["suave"])
            return linea,

        ani = FuncAnimation(fig, actualizar, interval=50, blit=False,
                            cache_frame_data=False)
        try:
            plt.show()
        except KeyboardInterrupt:
            pass
    print("Dispositivo cerrado.")


if __name__ == "__main__":
    main()
