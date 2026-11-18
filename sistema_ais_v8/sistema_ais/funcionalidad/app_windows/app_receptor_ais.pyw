import argparse
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

TITULO = "Receptor AIS"
CARPETA = os.path.dirname(os.path.abspath(__file__))
REGISTRO = os.path.join(CARPETA, "servidor.log")


def aviso(texto):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, texto, TITULO, 0x10)
    except Exception:
        print(texto, file=sys.stderr)


def puerto_abierto(puerto):
    try:
        with socket.create_connection(("127.0.0.1", puerto), timeout=0.5):
            return True
    except OSError:
        return False


def ultimas_lineas(n=12):
    try:
        with open(REGISTRO, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def arrancar_servidor(args, token):
    orden = (f"cd {args.carpeta} && source {args.entorno}/bin/activate && "
             f"exec python gui_servidor.py --puerto {args.puerto} --token {token}")
    if args.demo:
        orden += " --archivo sintetico.cu8 --repetir"
    comando = ["wsl.exe"]
    if args.distro:
        comando += ["-d", args.distro]
    comando += ["--", "bash", "-lc", orden]
    registro = open(REGISTRO, "w", encoding="utf-8")
    proceso = subprocess.Popen(comando, stdout=registro, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proceso, registro


def esperar_servidor(proceso, puerto, limite_s):
    fin = time.time() + limite_s
    while time.time() < fin:
        if puerto_abierto(puerto):
            return True
        if proceso.poll() is not None:
            return False
        time.sleep(0.4)
    return False


def apagar_servidor(proceso, puerto, token):
    try:
        peticion = urllib.request.Request(f"http://127.0.0.1:{puerto}/apagar?token={token}", data=b"", method="POST")
        urllib.request.urlopen(peticion, timeout=5).read()
    except (urllib.error.URLError, OSError):
        pass
    try:
        proceso.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proceso.terminate()


class ApiVentana:
    """Funciones que la página puede llamar desde la ventana de escritorio (window.pywebview.api)."""

    def __init__(self, puerto):
        self.puerto = puerto

    def abrir_mapa(self):
        webbrowser.open(f"http://localhost:{self.puerto}/mapa")
        return True

    def abrir_url(self, ruta):
        """Abre en el navegador una ruta del propio servidor (por ejemplo, la descarga de un CSV)."""
        if not isinstance(ruta, str) or not ruta.startswith("/") or ruta.startswith("//"):
            return False
        webbrowser.open(f"http://localhost:{self.puerto}{ruta}")
        return True


def main():
    ap = argparse.ArgumentParser(description="Receptor AIS: ventana de escritorio")
    ap.add_argument("--demo", action="store_true", help="usa la captura sintética en lugar del SDR")
    ap.add_argument("--puerto", type=int, default=8000)
    ap.add_argument("--carpeta", default="~/sistema_ais/funcionalidad", help="carpeta funcionalidad dentro de WSL")
    ap.add_argument("--entorno", default="~/ais_env", help="entorno virtual de Python dentro de WSL")
    ap.add_argument("--distro", default=None, help="nombre de la distribución de WSL (por defecto, la predeterminada)")
    ap.add_argument("--espera", type=float, default=60.0, help="segundos máximos para que arranque el servidor")
    args = ap.parse_args()

    try:
        import webview
    except ImportError:
        aviso("Falta la librería pywebview.\n\nInstálala con:\n  py -3 -m pip install pywebview")
        return 1

    proceso = None
    registro = None
    token = secrets.token_urlsafe(16)
    if not puerto_abierto(args.puerto):
        proceso, registro = arrancar_servidor(args, token)
        if not esperar_servidor(proceso, args.puerto, args.espera):
            if proceso.poll() is None:
                proceso.terminate()
            registro.close()
            aviso("No se pudo iniciar el servidor en WSL.\n\nÚltimas líneas del registro:\n\n" + ultimas_lineas())
            return 1

    webview.create_window(TITULO, f"http://localhost:{args.puerto}/", width=1440, height=900, min_size=(1000, 700),
                          js_api=ApiVentana(args.puerto))
    try:
        webview.start()
    finally:
        if proceso is not None:
            apagar_servidor(proceso, args.puerto, token)
            registro.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
