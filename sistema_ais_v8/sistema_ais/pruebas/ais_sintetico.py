import _ruta
import argparse
import json
import os

import numpy as np

import config
from ais_msg import ensamblar, payload_a_bits
from ais_bits import construir_trama, nrzi_codificar
from nmea import a_nmea

BAUDIOS = 9600
BT = 0.4
DESVIO_HZ = 2400
OFFSET_CANAL = {"A": -25_000, "B": +25_000}
ANCHO_RUIDO = 15_000

S1 = "!AIVDM,1,1,,B,1:pDs28000JaiwL6AkkJQELN00RA,0*12"
S2 = "!AIVDM,1,1,,B,3815<3E000JaioB6B5g9A5HT0000,0*48"
S3 = "!AIVDM,1,1,,A,1:pDs28000JaiwL6Akl825Jj08>V,0*26"
S4 = ["!AIVDM,2,1,0,B,5815<3@2BTmqKMPc>21@D4n0A8D4n2222222221?C8IE86QhNB0khPDH,0*76",
      "!AIVDM,2,2,0,B,888888888888880,2*27"]
# Clase B: tipo 18 (posición) y tipo 24 partes A y B (nombre / indicativo)
S5 = "!AIVDM,1,1,,B,B6CdCm0t3`tba35f@V9faHi7kP06,0*5B"
S6 = "!AIVDM,1,1,,B,H42O55i18tMET00000000000000,2*6E"
S7 = "!AIVDM,1,1,,B,H42O55lti4hhhilD3nink000?050,0*43"


def bits_de_sentencias(sentencias):
    payload, fill = ensamblar(sentencias)
    return [int(c) for c in payload_a_bits(payload, fill)]


def niveles_de_mensaje(bits_msg):
    return nrzi_codificar(construir_trama(bits_msg))


def modular_gmsk(niveles, fs):
    sps = int(round(fs / BAUDIOS))
    a = 2.0 * np.asarray(niveles, dtype=float) - 1.0
    x = np.repeat(a, sps)
    x = np.concatenate([np.full(2 * sps, a[0]), x, np.full(2 * sps, a[-1])])
    n = np.arange(-2 * sps, 2 * sps + 1)
    sigma = np.sqrt(np.log(2)) / (2 * np.pi * BT) * sps
    g = np.exp(-n ** 2 / (2 * sigma ** 2))
    g /= g.sum()
    f = np.convolve(x, g, mode="same")[2 * sps:-2 * sps]
    fase = 2 * np.pi * DESVIO_HZ * np.cumsum(f) / fs
    return np.exp(1j * fase)


def ruido_complejo(n, rng):
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)


def generar_captura(eventos, duracion_s, fs=config.FS, snr_db=15.0, semilla=0,
                    dc=0.0):
    rng = np.random.default_rng(semilla)
    n_total = int(duracion_s * fs)
    ruido = ruido_complejo(n_total, rng)
    amplitud = np.sqrt(10 ** (snr_db / 10) * ANCHO_RUIDO / fs)
    senal = np.zeros(n_total, dtype=np.complex128)
    verdad = []
    for ev in eventos:
        bits = bits_de_sentencias(ev["sentencias"])
        rafaga = modular_gmsk(niveles_de_mensaje(bits), fs)
        i0 = int(ev["t"] * fs)
        i1 = min(i0 + len(rafaga), n_total)
        k = np.arange(i1 - i0)
        desvio = ev.get("desvio_hz", 0.0)
        f = OFFSET_CANAL[ev["canal"]] + desvio
        fase0 = rng.uniform(0, 2 * np.pi)
        senal[i0:i1] += amplitud * rafaga[:i1 - i0] * np.exp(
            1j * (2 * np.pi * f * k / fs + fase0))
        verdad.append({"t": ev["t"], "canal": ev["canal"], "desvio_hz": desvio,
                       "sentencias": a_nmea(bits, ev["canal"], 0),
                       "bits": "".join(str(b) for b in bits)})
    iq = (ruido + senal) * 0.1 + dc
    return iq.astype(np.complex64), verdad


def a_cu8(iq):
    i = np.clip(np.round(iq.real * 127.5 + 127.5), 0, 255).astype(np.uint8)
    q = np.clip(np.round(iq.imag * 127.5 + 127.5), 0, 255).astype(np.uint8)
    crudo = np.empty(2 * len(iq), dtype=np.uint8)
    crudo[0::2] = i
    crudo[1::2] = q
    return crudo


def escenario_base():
    return [
        {"t": 0.30, "canal": "B", "sentencias": [S1]},
        {"t": 0.80, "canal": "A", "sentencias": [S2]},
        {"t": 1.30, "canal": "A", "sentencias": [S3], "desvio_hz": 1200},
        {"t": 1.80, "canal": "B", "sentencias": S4},
        {"t": 2.60, "canal": "B", "sentencias": [S2], "desvio_hz": -900},
        {"t": 3.00, "canal": "A", "sentencias": [S1]},
        {"t": 0.55, "canal": "A", "sentencias": [S5]},
        {"t": 1.05, "canal": "B", "sentencias": [S6], "desvio_hz": 600},
        {"t": 2.20, "canal": "A", "sentencias": [S7]},
        {"t": 3.30, "canal": "B", "sentencias": [S5], "desvio_hz": -1500},
    ]


def main():
    ap = argparse.ArgumentParser(description="Genera una captura AIS sintética (.cu8)")
    ap.add_argument("--snr", type=float, default=15.0, help="SNR de las ráfagas en 15 kHz (dB)")
    ap.add_argument("--duracion", type=float, default=3.6, help="duración (s)")
    ap.add_argument("--salida", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "sintetico.cu8"))
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--dc", type=float, default=0.02, help="desvío de DC añadido")
    args = ap.parse_args()

    iq, verdad = generar_captura(escenario_base(), args.duracion, snr_db=args.snr,
                                 semilla=args.semilla, dc=args.dc * (1 + 1j))
    crudo = a_cu8(iq)
    crudo.tofile(args.salida)
    with open(args.salida.replace(".cu8", "_verdad.json"), "w") as f:
        json.dump(verdad, f, indent=1)
    print(f"Muestras I/Q   : {len(iq)}  ({args.duracion} s a {config.FS / 1e6:.3f} MS/s)")
    print(f"Archivo        : {args.salida}  ({crudo.nbytes} bytes)")
    print(f"Ráfagas        : {len(verdad)}")
    for v in verdad:
        print(f"  t={v['t']:.2f} s  canal {v['canal']}  desvío {v['desvio_hz']:+.0f} Hz  {v['sentencias'][0][:40]}...")
    print("Verdad guardada en", args.salida.replace(".cu8", "_verdad.json"))


if __name__ == "__main__":
    main()
