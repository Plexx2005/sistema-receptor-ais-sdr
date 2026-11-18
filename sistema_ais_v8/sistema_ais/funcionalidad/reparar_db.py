"""Repara una base de datos grabada con la versión v3 (bits de cada octeto invertidos).

La v3 guardaba los mensajes con el orden de bits de cada octeto al revés, por lo
que los tipo 18 aparecían como "tipo 4" y los tipo 24 como "tipo 1". Este script:
  1. hace una copia de seguridad del archivo (.antes_de_reparar.db),
  2. vuelve a invertir los bits de cada mensaje guardado y lo decodifica bien,
  3. borra los que, ya corregidos, no son mensajes creíbles,
  4. reconstruye la tabla de barcos.
Los tipos 1 y 5 reales que la v3 descartó no se pueden recuperar: nunca se guardaron.

Uso:  python reparar_db.py ais.db
"""
import argparse
import json
import os
import shutil
import sqlite3

import rutas
from ais_bits import invertir_octetos
from ais_msg import ensamblar, payload_a_bits
from base_datos import BaseDatos
from nmea import a_nmea
from receptor import validar_mensaje

MARCA = "reparada_v4"


def corregir_fila(fila):
    sentencias = fila["nmea"].split("\n")
    payload, fill = ensamblar(sentencias)
    bits_v3 = [int(c) for c in payload_a_bits(payload, fill)]
    if len(bits_v3) % 8 != 0:
        return None
    bits = "".join(str(b) for b in invertir_octetos(bits_v3))
    campos = validar_mensaje(bits)
    if campos is None:
        return None
    nmea = a_nmea([int(b) for b in bits], fila["canal"])
    extra = {k: v for k, v in campos.items() if k not in ("tipo", "mmsi")}
    return campos["tipo"], campos["mmsi"], "\n".join(nmea), extra


def main():
    ap = argparse.ArgumentParser(description="Corrige una base de datos AIS grabada con la v3")
    ap.add_argument("db", nargs="?", default=rutas.DB_DEFECTO)
    ap.add_argument("--forzar", action="store_true", help="repara aunque ya figure como reparada")
    args = ap.parse_args()
    if not os.path.exists(args.db):
        raise SystemExit(f"No existe {args.db}")

    con = sqlite3.connect(args.db)
    con.execute("CREATE TABLE IF NOT EXISTS info (clave TEXT PRIMARY KEY, valor TEXT)")
    if con.execute("SELECT 1 FROM info WHERE clave = ?", (MARCA,)).fetchone() and not args.forzar:
        raise SystemExit("Esta base de datos ya fue reparada (usa --forzar para repetir).")
    con.close()

    copia = os.path.splitext(args.db)[0] + ".antes_de_reparar.db"
    shutil.copy2(args.db, copia)
    print("Copia de seguridad:", copia)

    bd = BaseDatos(args.db)
    filas = bd._filas("SELECT id, canal, nmea, tipo, mmsi FROM mensajes ORDER BY id")
    corregidos, borrados, antes, despues = 0, 0, {}, {}
    with bd._bloqueo:
        for f in filas:
            antes[f["tipo"]] = antes.get(f["tipo"], 0) + 1
            try:
                r = corregir_fila(f)
            except (ValueError, IndexError):
                r = None
            if r is None:
                bd.con.execute("DELETE FROM mensajes WHERE id = ?", (f["id"],))
                borrados += 1
                continue
            tipo, mmsi, nmea, extra = r
            bd.con.execute("UPDATE mensajes SET tipo = ?, mmsi = ?, nmea = ?, campos = ? WHERE id = ?",
                           (tipo, mmsi, nmea, json.dumps(extra, ensure_ascii=False), f["id"]))
            despues[tipo] = despues.get(tipo, 0) + 1
            corregidos += 1

        bd.con.execute("DELETE FROM barcos")
        mensajes = bd._filas("SELECT ts, tipo, mmsi, canal, campos FROM mensajes ORDER BY ts, id")
        for m in mensajes:
            if m["tipo"] in (1, 2, 3, 5, 18, 19, 24):
                ev = {"tipo": m["tipo"], "mmsi": m["mmsi"], "canal": m["canal"]}
                ev.update(json.loads(m["campos"]) if m["campos"] else {})
                bd._actualizar_barco(ev, m["ts"])
        bd.con.execute("CREATE TABLE IF NOT EXISTS info (clave TEXT PRIMARY KEY, valor TEXT)")
        bd.con.execute("INSERT OR REPLACE INTO info VALUES (?, '1')", (MARCA,))
        bd.con.commit()

    print(f"Mensajes revisados : {len(filas)}")
    print(f"Corregidos         : {corregidos}")
    print(f"Borrados (basura)  : {borrados}")
    print("Por tipo antes     :", dict(sorted(antes.items())))
    print("Por tipo después   :", dict(sorted(despues.items())))
    print("--- Barcos ---")
    for b in bd.barcos():
        pos = "-" if b["lat"] is None else f"{b['lat']:.5f}, {b['lon']:.5f}"
        print(f"  MMSI {b['mmsi']}  {b['nombre'] or '(sin nombre)'}  indicativo {b['indicativo'] or '-'}  "
              f"tipo nave {b['tipo_nave'] or '-'}  posición {pos}  mensajes {b['n_mensajes']}")
    bd.cerrar()


if __name__ == "__main__":
    main()
