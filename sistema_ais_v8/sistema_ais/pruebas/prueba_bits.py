import _ruta
from ais_msg import payload_a_bits, decodificar
from ais_bits import (construir_trama, nrzi_codificar, nrzi_decodificar,
                      decodificar_trama)

PAYLOAD = "1:pDs28000JaiwL6AkkJQELN00RA"
bits_msg = [int(c) for c in payload_a_bits(PAYLOAD)]
print("Bits del mensaje:", len(bits_msg))


trama = construir_trama(bits_msg)
niveles = nrzi_codificar(trama)
print("Bits transmitidos (preámbulo, banderas, CRC y relleno):", len(niveles))


bits_rx = nrzi_decodificar(niveles)
mensajes = decodificar_trama(bits_rx)
print("Tramas válidas encontradas:", len(mensajes))
recuperado = "".join(str(b) for b in mensajes[0])
original = "".join(str(b) for b in bits_msg)
print("¿Mensaje recuperado idéntico al original?", recuperado == original)
print(decodificar(recuperado))


bits_danados = bits_rx.copy()
bits_danados[60] ^= 1
print("Tramas válidas con un bit dañado:", len(decodificar_trama(bits_danados)))


# Orden en el aire (ITU-R M.1371 / HDLC): cada octeto sale con el bit menos
# significativo primero. El primer octeto de un tipo 1 es 000001|00 = 0x04,
# así que tras la bandera deben salir los bits 0,0,1,0,0,0,0,0.
primer_octeto = trama[24 + 8:24 + 16]
print("Primer octeto en el aire:", primer_octeto, "->",
      "orden LSB primero correcto" if primer_octeto == [0, 0, 1, 0, 0, 0, 0, 0] else "ORDEN INCORRECTO")
