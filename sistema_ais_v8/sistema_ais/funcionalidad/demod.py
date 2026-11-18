import numpy as np
from scipy import signal

from ais_bits import nrzi_decodificar, decodificar_trama

FS_CANAL = 48_000
BAUDIOS = 9600
SPS = FS_CANAL // BAUDIOS

FILTRO_IQ = signal.firwin(63, 5_500, fs=FS_CANAL)
FILTRO_FREC = signal.firwin(25, 8_500, fs=FS_CANAL)


def discriminador_fm(x):
    d = np.angle(x[1:] * np.conj(x[:-1]))
    d = np.concatenate(([d[0]], d))
    return d * FS_CANAL / (2 * np.pi)


def frecuencia_filtrada(x):
    x = np.asarray(x, dtype=np.complex128)
    n = np.arange(len(x))
    desvio_grueso = float(np.median(discriminador_fm(x)))
    x = x * np.exp(-2j * np.pi * desvio_grueso * n / FS_CANAL)
    x = np.convolve(x, FILTRO_IQ, mode="same")
    f = discriminador_fm(x)
    ajuste = float(np.median(f))
    g = np.convolve(f - ajuste, FILTRO_FREC, mode="same")
    return g, desvio_grueso + ajuste


def bits_por_fase(g, fase):
    niveles = (g[fase::SPS] > 0).astype(int).tolist()
    return nrzi_decodificar(niveles)


def demodular_rafaga(x):
    g, desvio = frecuencia_filtrada(x)
    encontrados = {}
    for fase in range(SPS):
        bits = bits_por_fase(g, fase)
        for msg in decodificar_trama(bits):
            clave = "".join(str(b) for b in msg)
            encontrados.setdefault(clave, fase)
    return encontrados, desvio
