import numpy as np
from scipy import signal

import config

FS_CANAL = 48_000
OFFSETS = {"A": -25_000.0, "B": +25_000.0}


def fir_pasabajos(fs, f_pasa, f_corte, atenuacion_db=60):
    ancho = (f_corte - f_pasa) / (fs / 2)
    n, beta = signal.kaiserord(atenuacion_db, ancho)
    n |= 1
    return signal.firwin(n, (f_pasa + f_corte) / 2, window=("kaiser", beta), fs=fs)


class Decimador:
    def __init__(self, b, q):
        self.b = np.asarray(b, dtype=np.float64)
        self.q = q
        self.zi = np.zeros(len(b) - 1, dtype=np.complex128)
        self.fase = 0

    def procesar(self, x):
        y, self.zi = signal.lfilter(self.b, 1.0, x, zi=self.zi)
        salida = y[self.fase::self.q]
        self.fase = (self.fase - len(y)) % self.q
        return salida


class Canalizador:
    def __init__(self, fs1, offset_hz):
        self.w = 2 * np.pi * offset_hz / fs1
        self.fase = 0.0
        self.etapa_b = Decimador(fir_pasabajos(fs1, 14e3, 84e3), 4)
        self.etapa_c = Decimador(fir_pasabajos(fs1 / 4, 13e3, 36e3), 2)

    def procesar(self, x):
        n = len(x)
        fases = self.fase + self.w * np.arange(n)
        self.fase = (self.fase + self.w * n) % (2 * np.pi)
        base = x * np.exp(-1j * fases)
        return self.etapa_c.procesar(self.etapa_b.procesar(base))


class BancoCanales:
    def __init__(self, fs=config.FS, offsets=OFFSETS):
        if fs != 1_536_000:
            raise ValueError("El banco de canales está diseñado para 1.536 MS/s")
        self.fs = fs
        self.comun = Decimador(fir_pasabajos(fs, 60e3, fs / 4 - 60e3), 4)
        self.canales = {nombre: Canalizador(fs / 4, off) for nombre, off in offsets.items()}

    def procesar(self, iq):
        x = self.comun.procesar(np.asarray(iq, dtype=np.complex128))
        return {nombre: c.procesar(x) for nombre, c in self.canales.items()}
