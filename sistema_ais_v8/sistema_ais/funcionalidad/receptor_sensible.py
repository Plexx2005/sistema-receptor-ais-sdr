import itertools

import numpy as np
from scipy import signal

import config
from ais_bits import buscar_flags, extraer_mensaje, nrzi_decodificar, quitar_stuffing
from canal import FS_CANAL
from demod import FILTRO_FREC, SPS, discriminador_fm
from receptor import ReceptorAIS

VARIANTES = ((5_500.0, FILTRO_FREC), (7_000.0, signal.firwin(25, 6_000, fs=FS_CANAL)))
FILTROS_IQ = {c: signal.firwin(63, c, fs=FS_CANAL) for c, _ in VARIANTES}
BAUDIOS = 9_600
N_ESPECTRO = 8192
RELACION_MIN_LINEAS = 6.0
SOBREMUESTREO = 2
SPS_FINO = SPS * SOBREMUESTREO
CANDIDATOS = 8
MAX_ERRORES = 2
RANGOS_TRAMA = ((184, 232), (328, 400), (440, 540))
MEJORES_PARA_REPARAR = 2
VENTANA_SENSIBLE = 48


def mascara_rafaga(x):
    p = np.abs(x) ** 2
    k = 24
    ps = np.convolve(p, np.ones(k) / k, mode="same")
    ruido = float(np.median(np.concatenate((ps[:40], ps[-40:]))))
    pico = float(np.percentile(ps, 95))
    return ps > ruido + 0.4 * (pico - ruido)


def mediana_activa(f, mascara):
    if int(mascara.sum()) >= 20:
        return float(np.median(f[mascara]))
    return float(np.median(f))


def desvio_por_cuadrado(x, mascara):
    z = (x * mascara) ** 2
    Z = np.abs(np.fft.fft(z * np.hanning(len(z)), N_ESPECTRO)) ** 2
    paso = FS_CANAL / N_ESPECTRO
    f0 = np.arange(-9_000.0, 9_000.0, paso)
    i1 = np.round((2 * f0 - BAUDIOS / 2) / paso).astype(int) % N_ESPECTRO
    i2 = np.round((2 * f0 + BAUDIOS / 2) / paso).astype(int) % N_ESPECTRO
    suma = Z[i1] + Z[i2]
    k = int(np.argmax(suma))
    if suma[k] < RELACION_MIN_LINEAS * float(np.median(suma)):
        return None
    return float(f0[k])


def frecuencia_variante(x, corte, filtro_frec, desvio, mascara):
    n = np.arange(len(x))
    y = x * np.exp(-2j * np.pi * desvio * n / FS_CANAL)
    y = np.convolve(y, FILTROS_IQ[corte], mode="same")
    f = discriminador_fm(y)
    ajuste = mediana_activa(f, mascara)
    g = np.convolve(f - ajuste, filtro_frec, mode="same")
    return g, desvio + ajuste


def tramas_validas(bits):
    salida = {}
    flags = buscar_flags(bits)
    for a, b in zip(flags, flags[1:]):
        msg = extraer_mensaje(quitar_stuffing(bits[a + 8:b]))
        if msg is not None:
            salida["".join(str(v) for v in msg)] = (a, b)
    return salida


def reparar_trama(niveles, confianza, a, b):
    largo = b - a - 8
    if not any(lo <= largo <= hi for lo, hi in RANGOS_TRAMA):
        return None
    indices = np.arange(a + 8, b)
    orden = indices[np.argsort(confianza[indices])[:CANDIDATOS]]
    previo = int(niveles[a + 7])
    base = niveles[a + 8:b].copy()
    for k in range(1, MAX_ERRORES + 1):
        for combo in itertools.combinations(orden, k):
            prueba = base.copy()
            for i in combo:
                prueba[i - (a + 8)] ^= 1
            bits = nrzi_decodificar(prueba.tolist(), previo=previo)
            msg = extraer_mensaje(quitar_stuffing(bits))
            if msg is not None:
                return "".join(str(v) for v in msg)
    return None


def demodular_sensible(x, reparar=True):
    x = np.asarray(x, dtype=np.complex128)
    mascara = mascara_rafaga(x)
    desvio0 = None
    if int(mascara.sum()) >= 20:
        desvio0 = desvio_por_cuadrado(x, mascara.astype(np.float64))
    if desvio0 is None:
        desvio0 = mediana_activa(discriminador_fm(x), mascara)
    encontrados = {}
    candidatos = []
    desvio_final = desvio0
    for numero, (corte, filtro_frec) in enumerate(VARIANTES):
        g, desvio = frecuencia_variante(x, corte, filtro_frec, desvio0, mascara)
        if numero == 0:
            desvio_final = desvio
        g_fino = np.interp(np.arange(0, len(g) - 1 + 1e-9, 1.0 / SOBREMUESTREO), np.arange(len(g)), g)
        for fase in range(SPS_FINO):
            muestras = g_fino[fase::SPS_FINO]
            niveles = (muestras > 0).astype(np.int8)
            bits = nrzi_decodificar(niveles.tolist())
            for clave in tramas_validas(bits):
                encontrados.setdefault(clave, fase)
            if reparar:
                candidatos.append((float(np.mean(np.abs(muestras))), niveles, np.abs(muestras), fase))
    if reparar and not encontrados:
        candidatos.sort(key=lambda c: -c[0])
        for _, niveles, confianza, fase in candidatos[:MEJORES_PARA_REPARAR]:
            bits = nrzi_decodificar(niveles.tolist())
            flags = buscar_flags(bits)
            for a, b in zip(flags, flags[1:]):
                clave = reparar_trama(niveles, confianza, a, b)
                if clave is not None:
                    encontrados.setdefault(clave, fase)
    return encontrados, desvio_final


class ReceptorSensible(ReceptorAIS):
    def __init__(self, umbral_db=3.0, fs=config.FS, reparar=True, ventana_pot=VENTANA_SENSIBLE):
        super().__init__(umbral_db=umbral_db, fs=fs)
        self.demodulador = lambda seg: demodular_sensible(seg, reparar)
        self.ventana_pot = ventana_pot


def crear_receptor(modo, umbral_db):
    if modo == "sensible":
        return ReceptorSensible(umbral_db=umbral_db)
    return ReceptorAIS(umbral_db=umbral_db)
