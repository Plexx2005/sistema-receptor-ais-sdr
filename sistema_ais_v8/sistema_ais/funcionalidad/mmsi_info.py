"""Información derivada del MMSI y del código de tipo de nave (ITU-R M.585 y M.1371)."""
from mid_paises import MID

TIPOS_NAVE = {
    20: "Nave de efecto suelo (WIG)", 30: "Pesquero", 31: "Remolcador (remolcando)",
    32: "Remolcador (remolque largo >200 m o ancho >25 m)", 33: "Dragado u operaciones submarinas",
    34: "Operaciones de buceo", 35: "Operaciones militares", 36: "Velero", 37: "Embarcación de recreo",
    40: "Nave de alta velocidad", 50: "Práctico", 51: "Búsqueda y rescate", 52: "Remolcador",
    53: "Lancha de servicio portuario", 54: "Equipo anticontaminación", 55: "Autoridad / policía marítima",
    56: "Embarcación local", 57: "Embarcación local", 58: "Transporte sanitario", 59: "Nave no combatiente",
    60: "Pasajeros", 70: "Carga", 80: "Tanquero", 90: "Otro tipo",
}
CARGA_PELIGROSA = {1: "carga peligrosa cat. A", 2: "carga peligrosa cat. B", 3: "carga peligrosa cat. C",
                   4: "carga peligrosa cat. D"}


def tipo_nave_texto(codigo):
    """Código de tipo de nave (mensajes 5, 19 y 24B) -> descripción en español, o None."""
    if codigo is None:
        return None
    try:
        codigo = int(codigo)
    except (TypeError, ValueError):
        return None
    if codigo <= 0 or codigo > 99:
        return None
    if codigo in TIPOS_NAVE:
        return TIPOS_NAVE[codigo]
    decena, unidad = divmod(codigo, 10)
    base = {2: 20, 4: 40, 6: 60, 7: 70, 8: 80, 9: 90}.get(decena)
    if base is None:
        return f"Reservado ({codigo})"
    texto = TIPOS_NAVE[base]
    if unidad in CARGA_PELIGROSA:
        return f"{texto}, {CARGA_PELIGROSA[unidad]}"
    if unidad == 9 or unidad == 0:
        return texto
    return f"{texto} ({codigo})"


def _relleno(cola):
    return len(set(cola)) == 1 and cola[0] in "09"


def clasificar(mmsi):
    """MMSI -> (categoría, MID o None, válido)."""
    s = f"{int(mmsi):09d}"
    if len(s) != 9:
        return "MMSI no válido", None, False
    if s.startswith("970"):
        return "Radiobaliza AIS-SART", None, True
    if s.startswith("972"):
        return "Radiobaliza hombre al agua", None, True
    if s.startswith("974"):
        return "Radiobaliza EPIRB-AIS", None, True
    if s.startswith("111"):
        mid, cola, cat = s[3:6], s[6:], "Aeronave de búsqueda y rescate"
    elif s.startswith("99"):
        mid, cola, cat = s[2:5], s[5:], "Ayuda a la navegación"
    elif s.startswith("98"):
        mid, cola, cat = s[2:5], s[5:], "Embarcación auxiliar"
    elif s.startswith("00"):
        mid, cola, cat = s[2:5], s[5:], "Estación costera"
    elif s.startswith("0"):
        mid, cola, cat = s[1:4], s[4:], "Grupo de barcos"
    elif s.startswith("8"):
        mid, cola, cat = s[1:4], s[4:], "Radio VHF portátil"
    elif s[0] in "234567":
        mid, cola, cat = s[:3], s[3:], "Barco"
    else:
        return "MMSI no válido", None, False
    mid = int(mid)
    valido = mid in MID and not _relleno(cola)
    return cat, mid, valido


def info(mmsi, tipo_nave=None):
    """Resumen para mostrar y guardar: país, categoría, validez y tipo de nave en texto."""
    categoria, mid, valido = clasificar(mmsi)
    if mid in MID:
        iso, pais = MID[mid]
    else:
        iso, pais = None, ("Desconocido" if mid is None else f"MID {mid:03d} no asignado")
    if not valido and categoria != "MMSI no válido":
        categoria += " (MMSI no válido)"
    return {"pais": pais, "pais_iso": iso, "categoria": categoria, "mmsi_valido": valido,
            "tipo_nave_desc": tipo_nave_texto(tipo_nave)}
