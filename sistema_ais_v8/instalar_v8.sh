#!/bin/bash
# Actualiza ~/sistema_ais (WSL) a la versión 8 CONSERVANDO la base de datos y las capturas.
set -e
ORIGEN="$(cd "$(dirname "$0")" && pwd)/sistema_ais"
DESTINO="$HOME/sistema_ais"
ENTORNO="$HOME/ais_env"
FECHA=$(date +%Y%m%d_%H%M%S)

echo "== Actualizando Receptor AIS a la v8 =="
[ -f "$ORIGEN/funcionalidad/gui/mapa.html" ] || { echo "ERROR: no encuentro $ORIGEN"; exit 1; }
if pgrep -f "python[0-9.]* .*(gui_servidor|escuchar)\.py" >/dev/null; then
    echo "ERROR: el Receptor AIS está abierto. Ciérralo y vuelve a ejecutar el instalador."
    exit 1
fi
if [ -d "$DESTINO" ]; then
    RESP="$HOME/sistema_ais_respaldo_$FECHA"
    cp -a "$DESTINO" "$RESP"
    echo "Copia de seguridad completa (con la base de datos) en: $RESP"
else
    mkdir -p "$DESTINO"
fi

cd "$ORIGEN"
find . -type f ! -name '*.db' ! -name '*.db-*' ! -path './pruebas/capturas/*' ! -name 'sintetico.cu8' ! -name 'nube.json' | while read -r f; do
    mkdir -p "$DESTINO/$(dirname "$f")"
    cp "$f" "$DESTINO/$f"
done
echo "Código v8 copiado. Tu base de datos y tus capturas no se tocaron."

source "$ENTORNO/bin/activate"
cd "$DESTINO/funcionalidad"
python - <<'PY'
from base_datos import BaseDatos
import os
if os.path.exists("ais.db"):
    bd = BaseDatos("ais.db")   # agrega columnas nuevas y completa país/tipo/recepción
    n = bd.contar("mensajes"); b = bd.contar("barcos")
    print(f"Base de datos actualizada: {n} mensajes, {b} barcos (país, tipo de nave y recepción completados).")
    bd.cerrar()
else:
    print("No había ais.db: se creará al iniciar el receptor.")
PY
echo
echo "--- Ubicación de las sesiones anteriores ---"
python asignar_posicion.py --interactivo || echo "(se puede hacer después con: python asignar_posicion.py --interactivo)"
echo
cd "$DESTINO/pruebas"
echo "Generando captura sintética para el modo demo..."
python ais_sintetico.py > /dev/null
echo "Comprobando la instalación..."
python prueba_bits.py | tail -1
python ../funcionalidad/decodificar_archivo.py sintetico.cu8 --verdad sintetico_verdad.json | tail -1
python -c "import tornado; print('tornado', tornado.version, 'OK')"
echo
echo "--- Envío a la nube ---"
cd "$DESTINO/funcionalidad"
python nube.py --estado || true
echo "(Para activarlo cuando tengas la EC2: doble clic en Configurar_Nube.bat)"
echo "== Listo. Abre el sistema con Receptor_AIS.bat =="
