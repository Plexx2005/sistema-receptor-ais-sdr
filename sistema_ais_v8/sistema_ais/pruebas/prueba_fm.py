import _ruta
import sys

from activar_sdr import main

sys.argv = [sys.argv[0], "--frecuencia", "98", "--ganancia", "19.7"] + sys.argv[1:]
main()
