import numpy as np

import config

N_FFT = 4096
GRUPO = 8
MEDIO_ZOOM_HZ = 100_000
VENTANA = np.hanning(N_FFT).astype(np.float32)
NORMA = float(VENTANA.sum() ** 2)


def calcular(iq, fs=config.FS, fc=config.FC):
    n_seg = len(iq) // N_FFT
    if n_seg == 0:
        return None
    x = np.asarray(iq[:n_seg * N_FFT], dtype=np.complex64)
    x = x - x.mean()
    seg = x.reshape(n_seg, N_FFT) * VENTANA
    f = np.fft.fftshift(np.fft.fft(seg, axis=1), axes=1)
    p = (f.real ** 2 + f.imag ** 2).mean(axis=0) / NORMA
    paso = fs / N_FFT
    centro = N_FFT // 2
    mitad = int(MEDIO_ZOOM_HZ / paso)
    completo = p.reshape(N_FFT // GRUPO, GRUPO).mean(axis=1)
    zoom = p[centro - mitad:centro + mitad]
    a_db = lambda v: np.round(10 * np.log10(v + 1e-20), 1).tolist()
    return {
        "k": "espectro",
        "fc": fc,
        "completo": a_db(completo),
        "completo_f0": (GRUPO / 2 - 0.5 - centro) * paso,
        "completo_paso": GRUPO * paso,
        "zoom": a_db(zoom),
        "zoom_f0": -mitad * paso,
        "zoom_paso": paso,
    }
