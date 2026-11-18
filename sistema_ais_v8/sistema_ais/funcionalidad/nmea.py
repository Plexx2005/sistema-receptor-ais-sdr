MAX_PAYLOAD = 56


def bits_a_payload(bits):
    bits = list(bits)
    relleno = (-len(bits)) % 6
    bits += [0] * relleno
    chars = []
    for i in range(0, len(bits), 6):
        v = int("".join(str(b) for b in bits[i:i + 6]), 2)
        c = v + 48
        if c > 87:
            c += 8
        chars.append(chr(c))
    return "".join(chars), relleno


def checksum(cuerpo):
    x = 0
    for ch in cuerpo:
        x ^= ord(ch)
    return f"{x:02X}"


def a_nmea(bits, canal="A", id_secuencia=0):
    payload, relleno = bits_a_payload(bits)
    trozos = [payload[i:i + MAX_PAYLOAD] for i in range(0, len(payload), MAX_PAYLOAD)]
    total = len(trozos)
    sentencias = []
    for n, trozo in enumerate(trozos, start=1):
        seq = str(id_secuencia) if total > 1 else ""
        fill = relleno if n == total else 0
        cuerpo = f"AIVDM,{total},{n},{seq},{canal},{trozo},{fill}"
        sentencias.append(f"!{cuerpo}*{checksum(cuerpo)}")
    return sentencias
