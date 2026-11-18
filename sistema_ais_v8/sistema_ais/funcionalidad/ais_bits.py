FLAG = [0, 1, 1, 1, 1, 1, 1, 0]
RESIDUO_CRC = 0xF0B8


def invertir_octetos(bits):
    # HDLC (y por tanto AIS, ITU-R M.1371) transmite cada octeto empezando por
    # el bit MENOS significativo. El mensaje AIS (tipo, MMSI, lat, lon...) se
    # define con el bit MÁS significativo primero, así que al recibir hay que
    # invertir el orden de los bits dentro de cada octeto (y al transmitir, igual).
    out = []
    for i in range(0, len(bits), 8):
        out.extend(bits[i:i + 8][::-1])
    return out


def extraer_mensaje(cuerpo):
    """Cuerpo sin relleno (datos + FCS) -> bits del mensaje AIS en orden MSB, o None."""
    if cuerpo is None or len(cuerpo) <= 16:
        return None
    if (len(cuerpo) - 16) % 8 != 0:
        return None
    if crc16_registro(cuerpo) != RESIDUO_CRC:
        return None
    return invertir_octetos(cuerpo[:-16])

def crc16_registro(bits):

    crc = 0xFFFF
    for b in bits:
        if (b ^ crc) & 1:
            crc = (crc >> 1) ^ 0x8408
        else:
            crc >>= 1
    return crc

def nrzi_codificar(bits, nivel=0):

    out = []
    for b in bits:
        if b == 0:
            nivel ^= 1
        out.append(nivel)
    return out

def nrzi_decodificar(niveles, previo=0):
    out = []
    for n in niveles:
        out.append(1 if n == previo else 0)
        previo = n
    return out

def stuff(bits):

    out, unos = [], 0
    for b in bits:
        out.append(b)
        if b == 1:
            unos += 1
            if unos == 5:
                out.append(0)
                unos = 0
        else:
            unos = 0
    return out

def quitar_stuffing(bits):
    out, unos = [], 0
    for b in bits:
        if unos == 5:
            if b == 0:
                unos = 0
                continue
            return None
        out.append(b)
        unos = unos + 1 if b == 1 else 0
    return out

def construir_trama(bits_mensaje):

    if len(bits_mensaje) % 8 != 0:
        raise ValueError("El mensaje AIS debe ocupar un número entero de octetos")
    datos = invertir_octetos(list(bits_mensaje))
    fcs = crc16_registro(datos) ^ 0xFFFF
    fcs_bits = [(fcs >> i) & 1 for i in range(16)]
    cuerpo = stuff(datos + fcs_bits)
    preambulo = [0, 1] * 12
    return preambulo + FLAG + cuerpo + FLAG

def buscar_flags(bits):
    pos, i = [], 0
    while i <= len(bits) - 8:
        if bits[i:i + 8] == FLAG:
            pos.append(i)
            i += 8
        else:
            i += 1
    return pos

def decodificar_trama(bits):

    flags = buscar_flags(bits)
    mensajes = []
    for a, b in zip(flags, flags[1:]):
        msg = extraer_mensaje(quitar_stuffing(bits[a + 8:b]))
        if msg is not None:
            mensajes.append(msg)
    return mensajes
