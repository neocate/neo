# ---------------------------------------------------------------
# monitor_comun.py - Funciones compartidas por monitor_niveles.py (toques
# de nivel) y monitor_senales.py (señales de vela) - los dos leen el mismo
# flujo_*.csv de grabador_libro.py y el mismo historico_*_bitget.csv de
# descargar_bit.py --feed, cada uno vigilando algo distinto (ver cabecera
# de cada uno). Nada de esto es logica de trading - solo lectura en vivo
# y arranque de los procesos de los que dependen si hace falta.
#
# 2026-08-11: monitor_niveles.py hacia estas dos cosas fusionadas en un
# solo proceso (niveles + señales, añadido en el commit 7a11670 - nunca
# hubo una version separada anterior, se extrajo de cero). Se separan por
# el mismo motivo que ya justifico separar grabador_libro.py de monitor.py:
# señales es logica que se ajusta/prueba con frecuencia, niveles es
# mecanico y estable - un ajuste de señales no deberia obligar a reiniciar
# (y cortar la serie de) el vigilante de niveles.
# ---------------------------------------------------------------

import csv
import io
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas.grabador_libro import CAMPOS_CSV as CAMPOS_LIBRO
from herramientas.descargar_bit import DIR_LIBRO, _archivo as _archivo_bitget

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAMPOS_AVISOS = ["timestamp_ms", "fecha_utc", "coin", "evento", "tipo", "origen",
                  "nivel_precio", "precio_actual", "imbalance", "cvd"]


def _flt(s):
    """Parsea un campo del CSV de grabador_libro.py a float, o None si esta
    vacio (API fallo esa vuelta - ver grabador_libro.py._fila)."""
    if s in (None, ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _localizar_csv_libro(coin):
    """Busca en herramientas/libro/ el CSV de grabador_libro.py vigente que
    incluya esta moneda (nombre flujo_<MONEDA1-MONEDA2...>[_vN].csv, SIN
    fecha desde 2026-08-11 - a proposito, para que un reinicio retome el
    mismo fichero y el CVD, ver grabador_libro.py._archivo. El patron
    tambien acepta el formato viejo con fecha, flujo_<fecha>_<monedas>.csv,
    por si queda algun fichero de antes de ese cambio - ver
    grabador_libro.py._ruta_compatible). Si hay varios (el viejo con fecha y
    el nuevo sin ella, o _v2/_v3 por cabecera cambiada), toma el modificado
    mas reciente. None si no encuentra ninguno."""
    if not os.path.isdir(DIR_LIBRO):
        return None
    candidatos = []
    for nombre in os.listdir(DIR_LIBRO):
        m = re.match(r"^flujo_(?:\d+_)?(.+?)(?:_v\d+)?\.csv$", nombre)
        if not m:
            continue
        monedas = m.group(1).split("-")
        if coin.upper() in monedas:
            ruta = os.path.join(DIR_LIBRO, nombre)
            candidatos.append((os.path.getmtime(ruta), ruta))
    if not candidatos:
        return None
    candidatos.sort()
    return candidatos[-1][1]


def _tail_csv(ruta, offset, fieldnames=CAMPOS_LIBRO):
    """Lee las lineas COMPLETAS agregadas a 'ruta' desde 'offset' bytes (una
    fila a medio escribir se deja sin consumir para la proxima vuelta).
    Devuelve (filas_dict, nuevo_offset). 'fieldnames' por defecto es el
    esquema de flujo_*.csv (CAMPOS_LIBRO) - monitor_telegram.py lo llama
    con CAMPOS_AVISOS para leer senales_*.csv en vez de flujo."""
    with open(ruta, "rb") as f:
        f.seek(offset)
        data = f.read()
    if not data:
        return [], offset
    corte = data.rfind(b"\n")
    if corte == -1:
        return [], offset
    bloque, nuevo_offset = data[:corte + 1], offset + corte + 1
    texto = bloque.decode("utf-8", errors="replace")
    filas = list(csv.DictReader(io.StringIO(texto), fieldnames=fieldnames))
    return filas, nuevo_offset


def _ultima_fila_coin(ruta, coin):
    """Ultima fila COMPLETA de 'ruta' (flujo_*.csv de grabador_libro.py)
    para 'coin' - lee solo la cola del fichero (puede tener decenas de MB
    tras dias corriendo) en vez de todo. Se descarta la primera linea de la
    cola por si quedo cortada a medias por el propio seek - sobra margen
    (256KB) para que la ultima fila de CUALQUIER moneda siga estando mas
    adelante. None si no hay ninguna."""
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 262_144))
        cola = f.read()
    lineas = [l for l in cola.decode("utf-8", errors="replace").split("\n") if l.strip()]
    if len(lineas) > 1:
        lineas = lineas[1:]
    for linea in reversed(lineas):
        campos = next(csv.reader([linea]))
        if len(campos) != len(CAMPOS_LIBRO):
            continue
        fila = dict(zip(CAMPOS_LIBRO, campos))
        if fila.get("coin") == coin.upper():
            return fila
    return None


# grabador_libro.py/descargar_bit.py --feed SIEMPRE corren en el NAS por
# SSH (ver sus cabeceras) - nunca se lanzan desde Windows. 'ps' no existe en
# Windows, asi que ahi _proceso_corriendo() no tiene forma fiable de
# comprobar nada - el auto-arranque se desactiva del todo en vez de asumir
# "no esta corriendo" y lanzar una copia local rota (sin ccxt/dotenv en el
# Python de Windows, fallaria). Estos monitores SI pueden correr en Windows
# (son solo lectores, ver anotaciones.md), simplemente ahi no intentan
# arrancar nada - si de verdad no hay nada corriendo en el NAS, se quedan
# esperando en _esperar_historico/_localizar_csv_libro como antes.
_PUEDE_AUTO_ARRANCAR = os.name == "posix"


def _proceso_corriendo(fragmentos):
    """True si hay un proceso vivo cuya linea de comando (ps w) contiene
    TODOS los 'fragmentos' (lista de substrings) - p.ej. ["descargar_bit.py",
    "--feed"] para distinguir el modo daemon del uso puntual. DSM/busybox no
    tiene pgrep (ver anotaciones.md), se parsea 'ps w' a mano. Si 'ps'
    fallara por lo que sea, asume que NO esta corriendo (mejor arrancarlo de
    mas que quedarse esperando para siempre por un falso negativo)."""
    try:
        salida = subprocess.run(["ps", "w"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return any(all(frag in linea for frag in fragmentos) for linea in salida.splitlines())


def _ruta_script(nombre):
    return os.path.join(RAIZ_PROYECTO, "herramientas", nombre)


def _lanzar_detached(cmd, ruta_log):
    """Equivalente a 'nohup <cmd> > ruta_log 2>&1 &': el hijo sobrevive
    aunque este monitor se cierre (start_new_session=True, mismo patron que
    usa nohup para desligarse de la sesion)."""
    with open(ruta_log, "a") as f:
        subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          start_new_session=True, cwd=RAIZ_PROYECTO)


def _asegurar_grabador_libro(coins=("BTC", "ETH")):
    """grabador_libro.py es el unico dueño del libro/OI/funding/CVD en vivo
    (dato irreconstruible, ver su cabecera) - si no hay ninguno corriendo,
    lo arranca con el mismo set de monedas por defecto que el propio script
    (BTC,ETH), no solo la moneda de este monitor, para no fragmentar la
    cobertura si luego otro monitor vigila una moneda distinta. Idempotente:
    si ya hay uno vivo (para estas u otras monedas), no lanza un segundo.
    En Windows no hace nada (ver _PUEDE_AUTO_ARRANCAR)."""
    if not _PUEDE_AUTO_ARRANCAR or _proceso_corriendo(["grabador_libro.py"]):
        return
    print(f"grabador_libro.py no esta corriendo - arrancandolo ({','.join(coins)})...")
    _lanzar_detached([sys.executable, _ruta_script("grabador_libro.py"), ",".join(coins)],
                      os.path.join(RAIZ_PROYECTO, "libro.log"))


def _asegurar_feed_velas(coins=("BTC", "ETH")):
    """Igual que _asegurar_grabador_libro pero para descargar_bit.py --feed
    (velas). Busca '--feed' en la linea de comando ademas del nombre del
    script, para no confundir el modo daemon con un uso puntual manual. En
    Windows no hace nada (ver _PUEDE_AUTO_ARRANCAR)."""
    if not _PUEDE_AUTO_ARRANCAR or _proceso_corriendo(["descargar_bit.py", "--feed"]):
        return
    print(f"descargar_bit.py --feed no esta corriendo - arrancandolo ({','.join(coins)})...")
    _lanzar_detached([sys.executable, _ruta_script("descargar_bit.py"), "--feed", ",".join(coins)],
                      os.path.join(RAIZ_PROYECTO, "velas.log"))


def _esperar_historico(coin, tfs, cada):
    """Asegura el feed de velas (arrancandolo si hace falta) y luego espera
    a que existan los historico_<COIN>_<TF>_bitget.csv de 'tfs' - este
    monitor no descarga nada el mismo, solo lee (ver descargar_bit.py)."""
    _asegurar_feed_velas()
    for tf in tfs:
        ruta = _archivo_bitget(coin, tf)
        while not os.path.exists(ruta):
            print(f"Esperando a que 'descargar_bit.py --feed' genere el historico de "
                  f"{coin.upper()} {tf} ({ruta})...")
            time.sleep(cada)
