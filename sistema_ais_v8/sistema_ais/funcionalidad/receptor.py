import numpy as np

import config
from ais_msg import decodificar
from canal import BancoCanales, FS_CANAL
from demod import demodular_rafaga
from nmea import a_nmea

LONGITUD_ESPERADA = {1: (168,), 2: (168,), 3: (168,), 4: (168,), 5: (424,), 11: (168,),
                     18: (168,), 19: (312,), 21: tuple(range(272, 361, 8)), 24: (160, 168)}
TIPOS_CON_BARCO = (1, 2, 3, 5, 18, 19, 24)
TIPOS_CON_POSICION = (1, 2, 3, 4, 11, 18, 19, 21)

VENTANA_POT = 48
MARGEN = 96
MIN_RAFAGA = int(0.012 * FS_CANAL)
HUECO_MAX = int(0.002 * FS_CANAL)
MUESTRAS_INICIO = int(0.05 * FS_CANAL)


def posicion_plausible(campos):
    if campos["tipo"] not in TIPOS_CON_POSICION:
        return True
    lat, lon = campos["lat"], campos["lon"]
    if not (-90.0 <= lat <= 90.0 or lat == 91.0):
        return False
    if not (-180.0 <= lon <= 180.0 or lon == 181.0):
        return False
    if "cog" in campos and campos["cog"] > 360.0:
        return False
    if "rumbo" in campos and campos["rumbo"] > 359 and campos["rumbo"] != 511:
        return False
    if campos.get("estado_nav", 0) > 15:
        return False
    if "sog_kn" in campos and 70.0 <= campos["sog_kn"] < 102.3:
        return False
    return True


def validar_mensaje(bits):
    """Bits del mensaje (texto '0'/'1', orden MSB) -> campos decodificados, o None si no es creíble."""
    if len(bits) < 38:
        return None
    tipo = int(bits[:6], 2)
    if tipo not in LONGITUD_ESPERADA or len(bits) not in LONGITUD_ESPERADA[tipo]:
        return None
    if tipo == 24 and bits[38:40] not in ("00", "01"):
        return None
    mmsi = int(bits[8:38], 2)
    if tipo in TIPOS_CON_BARCO and not (200_000_000 <= mmsi <= 799_999_999):
        return None
    try:
        campos = decodificar(bits)
    except ValueError:
        return None
    if not posicion_plausible(campos):
        return None
    return campos


def media_movil(p, k):
    c = np.concatenate(([0.0], np.cumsum(p)))
    m = np.empty_like(p)
    mitad = k // 2
    idx = np.arange(len(p))
    a = np.clip(idx - mitad, 0, len(p))
    b = np.clip(idx - mitad + k, 0, len(p))
    m[:] = (c[b] - c[a]) / np.maximum(b - a, 1)
    return m


def intervalos(mascara):
    d = np.diff(np.concatenate(([0], mascara.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def unir_cercanos(ints, hueco):
    out = []
    for a, b in ints:
        if out and a - out[-1][1] <= hueco:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


class EstadoCanal:
    def __init__(self, nombre):
        self.nombre = nombre
        self.buf = np.zeros(0, dtype=np.complex128)
        self.inicio = 0
        self.piso = None


class ReceptorAIS:
    def __init__(self, umbral_db=3.0, fs=config.FS):
        self.banco = BancoCanales(fs)
        self.canales = {n: EstadoCanal(n) for n in self.banco.canales}
        self.umbral_db = umbral_db
        self.metricas = {"muestras": 0, "rafagas": 0, "tramas_ok": 0,
                         "rafagas_sin_crc": 0, "mensajes_invalidos": 0,
                         "por_canal": {n: 0 for n in self.canales},
                         "por_tipo": {}, "piso_db": {n: None for n in self.canales},
                         "snr_db": None}
        self._n_ok = []
        self.demodulador = demodular_rafaga
        self.ventana_pot = VENTANA_POT

    def procesar(self, iq):
        self.metricas["muestras"] += len(iq)
        eventos = []
        for nombre, nuevas in self.banco.procesar(iq).items():
            eventos += self._procesar_canal(self.canales[nombre], nuevas)
        eventos.sort(key=lambda e: e["t"])
        return eventos

    def _procesar_canal(self, c, nuevas):
        c.buf = np.concatenate([c.buf, nuevas])
        n = len(c.buf)
        p = np.abs(c.buf) ** 2
        ps = media_movil(p, self.ventana_pot)

        if c.piso is None:
            if n < MUESTRAS_INICIO:
                return []
            c.piso = float(np.percentile(ps, 25))
        else:
            ruido = ps < c.piso * 10 ** (self.umbral_db / 10)
            if np.count_nonzero(ruido) >= 200:
                alfa = 1 - np.exp(-len(nuevas) / FS_CANAL)
                c.piso = (1 - alfa) * c.piso + alfa * float(np.mean(p[ruido]))
        self.metricas["piso_db"][c.nombre] = 10 * np.log10(c.piso + 1e-20)

        umbral = c.piso * 10 ** (self.umbral_db / 10)
        ints = unir_cercanos(intervalos(ps > umbral), HUECO_MAX)

        eventos, corte, ultimo_fin = [], None, 0
        for a, b in ints:
            if b >= n - MARGEN:
                corte = max(a - MARGEN, 0)
                break
            if b - a >= MIN_RAFAGA:
                eventos += self._decodificar_rafaga(c, a, b, ps)
            ultimo_fin = b
        if corte is None:
            corte = max(n - 5 * MARGEN, ultimo_fin, 0)

        c.buf = c.buf[corte:]
        c.inicio += corte
        return eventos

    def _decodificar_rafaga(self, c, a, b, ps):
        m = self.metricas
        m["rafagas"] += 1
        seg = c.buf[max(a - MARGEN, 0):b + MARGEN]
        encontrados, desvio = self.demodulador(seg)
        piso = c.piso
        p_senal = float(np.mean(ps[a:b]))
        snr = 10 * np.log10(max(p_senal - piso, 1e-20) / piso)
        t = (c.inicio + a) / FS_CANAL
        eventos = []
        for clave in encontrados:
            bits = clave
            campos = validar_mensaje(bits)
            if campos is None:
                m["mensajes_invalidos"] += 1
                continue
            tipo = campos["tipo"]
            ev = {"t": t, "canal": c.nombre, "snr_db": snr, "desvio_hz": desvio,
                  "bits": bits, "nmea": a_nmea([int(x) for x in bits], c.nombre)}
            ev.update(campos)
            eventos.append(ev)
            m["tramas_ok"] += 1
            m["por_canal"][c.nombre] += 1
            m["por_tipo"][tipo] = m["por_tipo"].get(tipo, 0) + 1
            m["snr_db"] = snr
        if not eventos:
            m["rafagas_sin_crc"] += 1
        return eventos
