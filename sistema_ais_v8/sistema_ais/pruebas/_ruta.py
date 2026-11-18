import os
import sys

CARPETA_PRUEBAS = os.path.dirname(os.path.abspath(__file__))
CARPETA_FUNCIONALIDAD = os.path.join(os.path.dirname(CARPETA_PRUEBAS), "funcionalidad")
if CARPETA_FUNCIONALIDAD not in sys.path:
    sys.path.insert(0, CARPETA_FUNCIONALIDAD)
