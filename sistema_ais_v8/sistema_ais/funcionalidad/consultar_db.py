import argparse
import os
import time

import rutas
from base_datos import BaseDatos, TABLAS


def fecha(ts):
    if ts is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def numero(v, formato):
    return "-" if v is None else format(v, formato)


def mostrar_sesiones(bd):
    print("--- Sesiones ---")
    filas = bd.sesiones()
    if not filas:
        print("(sin sesiones)")
        return
    print(f"{'id':>3}  {'inicio':19}  {'duración':>9}  {'fuente':8}  {'mensajes':>8}  {'ganancia':>8}  {'receptor':>21}  detalle")
    for s in filas:
        dur = "-" if s["fin"] is None else f"{s['fin'] - s['inicio']:.0f} s"
        rx = "-" if s.get("rx_lat") is None else f"{s['rx_lat']:.5f},{s['rx_lon']:.5f}"
        print(f"{s['id']:>3}  {fecha(s['inicio'])}  {dur:>9}  {s['fuente']:8}  {s['n_mensajes']:>8}  "
              f"{numero(s['ganancia'], '.1f'):>8}  {rx:>21}  {s['detalle'] or ''}")


def mostrar_barcos(bd):
    print("--- Barcos ---")
    filas = bd.barcos()
    if not filas:
        print("(sin barcos)")
        return
    print(f"{'MMSI':>9}  {'nombre':20}  {'país':16}  {'tipo':18}  {'lat':>9}  {'lon':>10}  {'vel kn':>6}  "
          f"{'dist km':>7}  {'recepción':13}  {'msgs':>5}  última vez")
    for b in filas:
        rec = {1: "directa", 0: "retransmitida"}.get(b.get("directo"), "-")
        print(f"{b['mmsi']:>9}  {(b['nombre'] or '-')[:20]:20}  {(b.get('pais') or '-')[:16]:16}  "
              f"{(b.get('tipo_nave_desc') or b.get('categoria') or '-')[:18]:18}  "
              f"{numero(b['lat'], '.5f'):>9}  {numero(b['lon'], '.5f'):>10}  "
              f"{numero(b['sog_kn'], '.1f'):>6}  {numero(b.get('dist_km'), '.2f'):>7}  {rec:13}  "
              f"{b['n_mensajes']:>5}  {fecha(b['ultima_vez'])}")


def mostrar_mensajes(bd, n, sesion):
    print(f"--- Últimos {n} mensajes ---")
    filas = bd.ultimos_mensajes(n, sesion)
    if not filas:
        print("(sin mensajes)")
        return
    for m in reversed(filas):
        print(f"[{fecha(m['ts'])}] sesión {m['sesion_id']}  canal {m['canal']}  SNR {m['snr_db']:4.1f} dB  "
              f"tipo {m['tipo']}  MMSI {m['mmsi']}")
        for linea in m["nmea"].split("\n"):
            print("    ", linea)


def mostrar_resumen(bd, sesion):
    print("--- Resumen de mensajes ---")
    where = "" if sesion is None else " WHERE sesion_id = ?"
    params = () if sesion is None else (sesion,)
    total = bd._filas("SELECT COUNT(*) AS n FROM mensajes" + where, params)[0]["n"]
    print("Mensajes totales:", total)
    por_canal = bd._filas("SELECT canal, COUNT(*) AS n FROM mensajes" + where + " GROUP BY canal ORDER BY canal", params)
    print("Por canal       :", {r["canal"]: r["n"] for r in por_canal})
    por_tipo = bd._filas("SELECT tipo, COUNT(*) AS n FROM mensajes" + where + " GROUP BY tipo ORDER BY tipo", params)
    print("Por tipo        :", {r["tipo"]: r["n"] for r in por_tipo})
    snr = bd._filas("SELECT AVG(snr_db) AS medio, MIN(snr_db) AS minimo, MAX(snr_db) AS maximo FROM mensajes" + where, params)[0]
    if snr["medio"] is not None:
        print(f"SNR (dB)        : medio {snr['medio']:.1f}, mínimo {snr['minimo']:.1f}, máximo {snr['maximo']:.1f}")


def exportar(bd, carpeta):
    os.makedirs(carpeta, exist_ok=True)
    print("--- Exportación a CSV ---")
    for tabla in TABLAS:
        ruta = os.path.join(carpeta, f"{tabla}.csv")
        n = bd.exportar_csv(ruta, tabla)
        print(f"{ruta}: {n} filas")


def main():
    ap = argparse.ArgumentParser(description="Consulta la base de datos del receptor AIS")
    ap.add_argument("db", nargs="?", default=rutas.DB_DEFECTO, help="archivo SQLite (por defecto funcionalidad/ais.db)")
    ap.add_argument("--n", type=int, default=10, help="cuántos mensajes recientes mostrar")
    ap.add_argument("--sesion", type=int, help="limita mensajes y resumen a una sesión")
    ap.add_argument("--csv", metavar="CARPETA", help="exporta todas las tablas a CSV en esa carpeta")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"No existe {args.db}. Se crea al ejecutar escuchar.py o decodificar_archivo.py con --db {args.db}")
        return
    with BaseDatos(args.db) as bd:
        mostrar_sesiones(bd)
        print()
        mostrar_barcos(bd)
        print()
        mostrar_resumen(bd, args.sesion)
        print()
        mostrar_mensajes(bd, args.n, args.sesion)
        if args.csv:
            print()
            exportar(bd, args.csv)


if __name__ == "__main__":
    main()
