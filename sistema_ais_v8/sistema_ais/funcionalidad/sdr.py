import ctypes
import ctypes.util

import numpy as np

import config

SINTONIZADORES = {0: "desconocido", 1: "E4000", 2: "FC0012", 3: "FC0013",
                  4: "FC2580", 5: "R820T", 6: "R828D"}


def cargar_libreria():
    nombre = ctypes.util.find_library("rtlsdr") or "librtlsdr.so"
    lib = ctypes.CDLL(nombre)
    p = ctypes.c_void_p
    lib.rtlsdr_open.argtypes = [ctypes.POINTER(p), ctypes.c_uint32]
    lib.rtlsdr_close.argtypes = [p]
    lib.rtlsdr_set_center_freq.argtypes = [p, ctypes.c_uint32]
    lib.rtlsdr_set_sample_rate.argtypes = [p, ctypes.c_uint32]
    lib.rtlsdr_set_freq_correction.argtypes = [p, ctypes.c_int]
    lib.rtlsdr_set_tuner_gain_mode.argtypes = [p, ctypes.c_int]
    lib.rtlsdr_set_tuner_gain.argtypes = [p, ctypes.c_int]
    lib.rtlsdr_reset_buffer.argtypes = [p]
    lib.rtlsdr_read_sync.argtypes = [p, ctypes.c_void_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_int)]
    lib.rtlsdr_get_center_freq.argtypes = [p]
    lib.rtlsdr_get_center_freq.restype = ctypes.c_uint32
    lib.rtlsdr_get_sample_rate.argtypes = [p]
    lib.rtlsdr_get_sample_rate.restype = ctypes.c_uint32
    lib.rtlsdr_get_tuner_gain.argtypes = [p]
    lib.rtlsdr_get_freq_correction.argtypes = [p]
    lib.rtlsdr_get_tuner_type.argtypes = [p]
    lib.rtlsdr_get_device_name.argtypes = [ctypes.c_uint32]
    lib.rtlsdr_get_device_name.restype = ctypes.c_char_p
    return lib


def comprobar(codigo, que):
    if codigo < 0:
        raise RuntimeError(f"librtlsdr: error {codigo} al {que}")


def cu8_a_complejo(crudo):
    i = (crudo[0::2].astype(np.float32) - 127.5) / 127.5
    q = (crudo[1::2].astype(np.float32) - 127.5) / 127.5
    return (i + 1j * q).astype(np.complex64)


class SDR:
    def __init__(self, fc=config.FC, fs=config.FS, ganancia=config.GANANCIA,
                 ppm=config.PPM, chunk=config.CHUNK):
        self.fc = int(fc)
        self.fs = int(fs)
        self.ganancia = ganancia
        self.ppm = ppm
        self.chunk = chunk
        self._lib = None
        self._dev = None
        self._buf = None
        self._n_leidos = ctypes.c_int(0)

    def abrir(self):
        self._lib = cargar_libreria()
        self._dev = ctypes.c_void_p()
        comprobar(self._lib.rtlsdr_open(ctypes.byref(self._dev), 0),
                  "abrir el dispositivo")
        try:
            lib, dev = self._lib, self._dev
            comprobar(lib.rtlsdr_set_sample_rate(dev, self.fs), "fijar la tasa de muestreo")
            comprobar(lib.rtlsdr_set_center_freq(dev, self.fc), "fijar la frecuencia central")
            if self.ppm != 0:
                comprobar(lib.rtlsdr_set_freq_correction(dev, self.ppm), "fijar la corrección de PPM")
            comprobar(lib.rtlsdr_set_tuner_gain_mode(dev, 1), "activar la ganancia manual")
            comprobar(lib.rtlsdr_set_tuner_gain(dev, int(round(self.ganancia * 10))), "fijar la ganancia")
            comprobar(lib.rtlsdr_reset_buffer(dev), "reiniciar el buffer")
            for _ in range(2):
                self.leer_bytes(self.chunk)
        except Exception:
            self.cerrar()
            raise
        return self

    def cerrar(self):
        if self._dev is not None:
            self._lib.rtlsdr_close(self._dev)
            self._dev = None

    def __enter__(self):
        return self.abrir()

    def __exit__(self, *_):
        self.cerrar()

    def imprimir_info(self):
        lib, dev = self._lib, self._dev
        print("Dispositivo abierto:", lib.rtlsdr_get_device_name(0).decode())
        print("  Sintonizador       :", SINTONIZADORES.get(lib.rtlsdr_get_tuner_type(dev), "?"))
        print(f"  Frecuencia central : {lib.rtlsdr_get_center_freq(dev) / 1e6:.3f} MHz")
        print(f"  Tasa de muestreo   : {lib.rtlsdr_get_sample_rate(dev) / 1e6:.3f} MS/s")
        print(f"  Ganancia           : {lib.rtlsdr_get_tuner_gain(dev) / 10:.1f} dB")
        print(f"  Corrección         : {lib.rtlsdr_get_freq_correction(dev)} ppm")

    def leer_bytes(self, n):
        if self._buf is None or len(self._buf) != n:
            self._buf = (ctypes.c_uint8 * n)()
        comprobar(self._lib.rtlsdr_read_sync(self._dev, self._buf, n,
                                             ctypes.byref(self._n_leidos)),
                  "leer muestras")
        if self._n_leidos.value == 0:
            raise RuntimeError("El dispositivo no entregó datos")
        return np.frombuffer(self._buf, dtype=np.uint8,
                             count=self._n_leidos.value).copy()

    def capturar(self, segundos):
        total = 2 * (int(self.fs * segundos) // 1024 * 1024)
        trozos, leidos = [], 0
        while leidos < total:
            t = self.leer_bytes(self.chunk)
            trozos.append(t)
            leidos += len(t)
        return np.concatenate(trozos)[:total]

    def bloques(self, n_bytes=None):
        n = n_bytes or self.chunk
        while True:
            yield cu8_a_complejo(self.leer_bytes(n))
