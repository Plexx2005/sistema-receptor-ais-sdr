import _ruta
from ais_msg import ensamblar, payload_a_bits, decodificar

MENSAJES = [
    ["!AIVDM,1,1,,B,1:pDs28000JaiwL6AkkJQELN00RA,0*12"],
    ["!AIVDM,1,1,,B,3815<3E000JaioB6B5g9A5HT0000,0*48"],
    ["!AIVDM,1,1,,A,1:pDs28000JaiwL6Akl825Jj08>V,0*26"],
    ["!AIVDM,2,1,0,B,5815<3@2BTmqKMPc>21@D4n0A8D4n2222222221?C8IE86QhNB0khPDH,0*76",
     "!AIVDM,2,2,0,B,888888888888880,2*27"],
]

for grupo in MENSAJES:
    payload, fill = ensamblar(grupo)
    print(decodificar(payload_a_bits(payload, fill)))
