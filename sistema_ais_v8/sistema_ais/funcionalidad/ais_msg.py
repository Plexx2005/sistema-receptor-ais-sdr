def checksum_ok(sentencia):

    cuerpo, _, esperado = sentencia.strip().partition("*")
    calculado = 0
    for ch in cuerpo[1:]:
        calculado ^= ord(ch)
    return f"{calculado:02X}" == esperado.upper()

def parse_sentencia(sentencia):
    if not checksum_ok(sentencia):
        raise ValueError("Checksum NMEA inválido: " + sentencia)
    campos = sentencia.strip().split(",")
    return {
        "total": int(campos[1]),
        "numero": int(campos[2]),
        "payload": campos[5],
        "fill": int(campos[6].split("*")[0]),
    }

def ensamblar(sentencias):
    frags = sorted((parse_sentencia(s) for s in sentencias), key=lambda f: f["numero"])
    if len(frags) != frags[0]["total"]:
        raise ValueError("Faltan fragmentos del mensaje")
    payload = "".join(f["payload"] for f in frags)
    return payload, frags[-1]["fill"]

def payload_a_bits(payload, fill=0):

    bits = ""
    for ch in payload:
        v = ord(ch) - 48
        if v > 40:
            v -= 8
        bits += format(v, "06b")
    return bits[:len(bits) - fill]

def uint(bits, inicio, n):
    return int(bits[inicio:inicio + n], 2)

def sint(bits, inicio, n):
    v = uint(bits, inicio, n)
    return v - (1 << n) if v >= (1 << (n - 1)) else v

def texto(bits, inicio, n):

    out = ""
    for k in range(inicio, inicio + n, 6):
        v = int(bits[k:k + 6], 2)
        out += chr(v + 64) if v < 32 else chr(v)
    return out.replace("@", "").strip()

def _posicion_clase_b(bits, msg):
    # Tipos 18 y 19 comparten el mismo bloque de posición (ITU-R M.1371, mensajes 18/19)
    msg["sog_kn"] = uint(bits, 46, 10) / 10
    msg["precision"] = uint(bits, 56, 1)
    msg["lon"] = sint(bits, 57, 28) / 600000
    msg["lat"] = sint(bits, 85, 27) / 600000
    msg["cog"] = uint(bits, 112, 12) / 10
    msg["rumbo"] = uint(bits, 124, 9)
    msg["segundo"] = uint(bits, 133, 6)


def decodificar(bits):
    tipo = uint(bits, 0, 6)
    msg = {"tipo": tipo, "mmsi": uint(bits, 8, 30), "repeticion": uint(bits, 6, 2)}
    if tipo in (1, 2, 3):
        if len(bits) < 168:
            raise ValueError("Mensaje de posición demasiado corto")
        msg["estado_nav"] = uint(bits, 38, 4)
        msg["sog_kn"] = uint(bits, 50, 10) / 10
        msg["lon"] = sint(bits, 61, 28) / 600000
        msg["lat"] = sint(bits, 89, 27) / 600000
        msg["cog"] = uint(bits, 116, 12) / 10
        msg["rumbo"] = uint(bits, 128, 9)
    elif tipo in (4, 11):
        # Estación base (4) o respuesta de fecha/hora UTC (11)
        if len(bits) < 168:
            raise ValueError("Mensaje de estación base demasiado corto")
        msg["fecha_utc"] = (f"{uint(bits, 38, 14):04d}-{uint(bits, 52, 4):02d}-{uint(bits, 56, 5):02d} "
                            f"{uint(bits, 61, 5):02d}:{uint(bits, 66, 6):02d}:{uint(bits, 72, 6):02d}")
        msg["lon"] = sint(bits, 79, 28) / 600000
        msg["lat"] = sint(bits, 107, 27) / 600000
    elif tipo == 5:
        if len(bits) < 424:
            raise ValueError("Mensaje tipo 5 demasiado corto")
        msg["imo"] = uint(bits, 40, 30)
        msg["indicativo"] = texto(bits, 70, 42)
        msg["nombre"] = texto(bits, 112, 120)
        msg["tipo_nave"] = uint(bits, 232, 8)
        msg["calado_m"] = uint(bits, 294, 8) / 10
        msg["destino"] = texto(bits, 302, 120)
    elif tipo == 18:
        if len(bits) < 168:
            raise ValueError("Mensaje tipo 18 demasiado corto")
        _posicion_clase_b(bits, msg)
    elif tipo == 19:
        if len(bits) < 312:
            raise ValueError("Mensaje tipo 19 demasiado corto")
        _posicion_clase_b(bits, msg)
        msg["nombre"] = texto(bits, 143, 120)
        msg["tipo_nave"] = uint(bits, 263, 8)
    elif tipo == 21:
        # Ayuda a la navegación (boyas, faros)
        if len(bits) < 272:
            raise ValueError("Mensaje tipo 21 demasiado corto")
        msg["tipo_ayuda"] = uint(bits, 38, 5)
        msg["nombre"] = texto(bits, 43, 120)
        msg["lon"] = sint(bits, 164, 28) / 600000
        msg["lat"] = sint(bits, 192, 27) / 600000
    elif tipo == 24:
        if len(bits) < 160:
            raise ValueError("Mensaje tipo 24 demasiado corto")
        parte = uint(bits, 38, 2)
        msg["parte"] = "A" if parte == 0 else "B"
        if parte == 0:
            msg["nombre"] = texto(bits, 40, 120)
        else:
            if len(bits) < 168:
                raise ValueError("Mensaje tipo 24B demasiado corto")
            msg["tipo_nave"] = uint(bits, 40, 8)
            msg["indicativo"] = texto(bits, 90, 42)
    return msg
