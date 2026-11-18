import os

CARPETA_FUNCIONALIDAD = os.path.dirname(os.path.abspath(__file__))
CARPETA_PROYECTO = os.path.dirname(CARPETA_FUNCIONALIDAD)
CARPETA_PRUEBAS = os.path.join(CARPETA_PROYECTO, "pruebas")
CARPETA_CAPTURAS = os.path.join(CARPETA_PRUEBAS, "capturas")
DB_DEFECTO = os.path.join(CARPETA_FUNCIONALIDAD, "ais.db")


def resolver(archivo):
    if not archivo or os.path.exists(archivo):
        return archivo
    for carpeta in (CARPETA_PRUEBAS, CARPETA_CAPTURAS):
        candidato = os.path.join(carpeta, archivo)
        if os.path.exists(candidato):
            return candidato
    return archivo
