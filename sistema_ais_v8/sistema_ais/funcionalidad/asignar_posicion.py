"""Asigna la posición del receptor a sesiones ya grabadas que no la tienen.

Sirve para completar el historial: las sesiones grabadas antes de la v5 no
guardaban dónde estaba la antena. Calcula también la distancia de cada mensaje.

Ejemplos:
  python asignar_posicion.py --interactivo                            (pregunta y propone la última posición)
  python asignar_posicion.py --lat 10.98 --lon -74.80                 (todas las sesiones del SDR sin posición)
  python asignar_posicion.py --lat 10.98 --lon -74.80 --sesiones 100-107
  python asignar_posicion.py --lat 10.98 --lon -74.80 --sesiones 32,35 --forzar
"""
import argparse
import json

import rutas
from base_datos import BaseDatos, TIPOS_CON_POSICION, _pos_valida, distancia_km


def lista_sesiones(texto):
    ids = set()
    for parte in texto.split(","):
        parte = parte.strip()
        if "-" in parte:
            a, b = parte.split("-")
            ids.update(range(int(a), int(b) + 1))
        elif parte:
            ids.add(int(parte))
    return ids


def main():
    ap = argparse.ArgumentParser(description="Asigna la posición del receptor a sesiones antiguas")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--interactivo", action="store_true", help="muestra qué se haría y pide confirmación")
    ap.add_argument("--incluir-archivos", action="store_true",
                    help="incluye sesiones de archivos .cu8 (demo/sintéticas); por defecto solo sesiones del SDR")
    ap.add_argument("--sesiones", help="ids, rangos o ambos: 1-50,60 (por defecto, todas las que no tienen posición)")
    ap.add_argument("--forzar", action="store_true", help="reemplaza también la posición de sesiones que ya la tienen")
    ap.add_argument("--db", default=rutas.DB_DEFECTO)
    args = ap.parse_args()
    if not args.interactivo and (args.lat is None or args.lon is None):
        raise SystemExit("Indica --lat y --lon, o usa --interactivo")

    with BaseDatos(args.db) as bd:
        sesiones = bd.sesiones()
        if args.sesiones:
            pedidas = lista_sesiones(args.sesiones)
            sesiones = [s for s in sesiones if s["id"] in pedidas]
        if not args.incluir_archivos:
            sesiones = [s for s in sesiones if s["fuente"] != "archivo"]
        if not args.forzar:
            sesiones = [s for s in sesiones if s["rx_lat"] is None]
        if not sesiones:
            print("No hay sesiones sin ubicación que actualizar.")
            return
        if args.interactivo:
            con_msgs = sum(1 for s in sesiones if s["n_mensajes"])
            msgs = sum(s["n_mensajes"] for s in sesiones)
            print(f"Hay {len(sesiones)} sesiones del receptor sin ubicación ({con_msgs} con mensajes, {msgs} mensajes en total).")
            if args.lat is None:
                ultima = bd.ultima_posicion_receptor()
                if ultima:
                    args.lat, args.lon = ultima["lat"], ultima["lon"]
            if args.lat is not None:
                print(f"Posición propuesta (la última registrada): {args.lat:.5f}, {args.lon:.5f}")
                pregunta = "Enter = usar esa posición · escribe 'lat,lon' para otra · 'n' para omitir: "
            else:
                pregunta = "Escribe la posición de la antena como 'lat,lon' (o 'n' para omitir): "
            try:
                r = input(pregunta).strip()
            except EOFError:
                r = "n"
            if r.lower() in ("n", "no"):
                print("No se modificó nada.")
                return
            if r:
                try:
                    args.lat, args.lon = (float(v) for v in r.replace(" ", "").split(","))
                except ValueError:
                    raise SystemExit("Formato no válido; usa por ejemplo 10.98642,-74.78918")
            if args.lat is None:
                print("No se modificó nada.")
                return
        if not _pos_valida(args.lat, args.lon):
            raise SystemExit("Coordenadas fuera de rango")
        afectados, n_msg = set(), 0
        with bd._bloqueo:
            for s in sesiones:
                if args.forzar:
                    bd.con.execute("UPDATE sesiones SET rx_lat = NULL, rx_lon = NULL WHERE id = ?", (s["id"],))
                bd.registrar_posicion(args.lat, args.lon, None, "asignada después de la sesión", ts=s["inicio"], sesion_id=s["id"])
                filas = bd._filas("SELECT id, tipo, mmsi, campos FROM mensajes WHERE sesion_id = ?" +
                                  ("" if args.forzar else " AND rx_lat IS NULL"), (s["id"],))
                for f in filas:
                    c = json.loads(f["campos"]) if f["campos"] else {}
                    dist = None
                    if f["tipo"] in TIPOS_CON_POSICION and _pos_valida(c.get("lat"), c.get("lon")):
                        dist = round(distancia_km(args.lat, args.lon, c["lat"], c["lon"]), 3)
                    bd.con.execute("UPDATE mensajes SET rx_lat = ?, rx_lon = ?, dist_km = ? WHERE id = ?",
                                   (args.lat, args.lon, dist, f["id"]))
                    afectados.add(f["mmsi"])
                n_msg += len(filas)
            for mmsi in afectados:
                bd._reconstruir_barco(mmsi)
            bd.con.commit()
        print(f"Posición {args.lat:.5f}, {args.lon:.5f} asignada a {len(sesiones)} sesiones y {n_msg} mensajes.")


if __name__ == "__main__":
    main()
