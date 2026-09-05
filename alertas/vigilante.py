
import argparse
import csv
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

DIR_ALERTAS = os.path.dirname(os.path.abspath(__file__))
DIR_NEO = os.path.dirname(DIR_ALERTAS)
sys.path.insert(0, DIR_ALERTAS)
sys.path.insert(0, os.path.join(DIR_NEO, "niveles"))

import avisos

try:
    import sincronia
    SINCRONIA = True
except ImportError:
    SINCRONIA = False

FACTOR_MARGEN = 3.0


CADENCIA_LIBRO_S = 900.0
CADENCIA_FLUJO_S = 60.0

TF_SEGUNDOS = {'1m': 60, '3m': 180, '5m': 300, '15m': 900,
               '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400}


def _ahora():
    return time.time()


def _edad_ultima_fila(ruta, columna='fecha_utc'):
    try:
        tam = os.path.getsize(ruta)
        with open(ruta, 'rb') as f:
            f.seek(max(0, tam - 65536))
            cola = f.read().decode('utf-8', 'replace').splitlines()
        with open(ruta, encoding='utf-8') as f:
            cab = f.readline().strip().split(',')
        if columna not in cab or len(cola) < 2:
            return None
        i = cab.index(columna)
        for linea in reversed(cola):
            campos = next(csv.reader([linea]), [])
            if len(campos) <= i or not campos[i]:
                continue
            try:
                t = datetime.strptime(campos[i], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
            return _ahora() - t.replace(tzinfo=timezone.utc).timestamp()
    except (OSError, ValueError, StopIteration):
        return None
    return None


def _ultimo_ts_ms(ruta):
    try:
        tam = os.path.getsize(ruta)
        with open(ruta, 'rb') as f:
            f.seek(max(0, tam - 8192))
            cola = f.read().decode('utf-8', 'replace').splitlines()
        for linea in reversed(cola):
            campos = linea.split(',')
            if campos and campos[0].strip().isdigit():
                return int(campos[0])
    except (OSError, ValueError):
        return None
    return None


def _mas_reciente(patron):
    c = glob.glob(patron)
    return max(c, key=os.path.getmtime) if c else None


def revisar(coin, mercado):
    r = []

    p = _mas_reciente(os.path.join(DIR_NEO, "libro", "datos",
                                   "libro_%s_%s_*.csv" % (coin, mercado)))
    if p is None:
        r.append(("libro", False, "no hay ningun CSV de libro"))
    else:
        e = _edad_ultima_fila(p)
        lim = CADENCIA_LIBRO_S * FACTOR_MARGEN
        r.append(("libro", e is not None and e <= lim,
                  "sin grabar desde hace %s (limite %s)" % (_dur(e), _dur(lim))
                  if e is not None else "no se pudo leer la ultima fila"))

    p = _mas_reciente(os.path.join(DIR_NEO, "libro", "datos", "flujo",
                                   "flujo_%s_%s_*.csv" % (coin, mercado)))
    if p is None:
        r.append(("flujo", False, "no hay ningun CSV de flujo"))
    else:
        e = _edad_ultima_fila(p)
        lim = CADENCIA_FLUJO_S * FACTOR_MARGEN
        r.append(("flujo", e is not None and e <= lim,
                  "sin grabar desde hace %s (limite %s)" % (_dur(e), _dur(lim))
                  if e is not None else "no se pudo leer la ultima fila"))

    for p in sorted(glob.glob(os.path.join(DIR_NEO, "velas", coin,
                                           "bitget_%s_*_%s.csv" % (coin, mercado)))):
        tf = os.path.basename(p)[:-4].split('_')[2]
        seg = TF_SEGUNDOS.get(tf)
        if seg is None:
            continue
        ts = _ultimo_ts_ms(p)
        e = None if ts is None else _ahora() - ts / 1000.0
        if ts is None:
            ok, det = False, "no se pudo leer la ultima vela"
        elif SINCRONIA:
            ok = sincronia.es_reciente(ts, tf)
            det = "ultima vela hace %s (criterio sincronia)" % _dur(e)
        else:
            lim = seg * FACTOR_MARGEN
            ok = e <= lim
            det = "ultima vela hace %s (limite %s)" % (_dur(e), _dur(lim))
        r.append(("velas %s" % tf, ok, det))

    if SINCRONIA:
        for p in sorted(glob.glob(os.path.join(DIR_NEO, "niveles", "json",
                                               "nivel_%s_*_%s_*.json" % (coin, mercado)))):
            try:
                with open(p, encoding='utf-8') as f:
                    d = json.load(f)
                tf, ts = d.get('tf'), d.get('ts_ultima_vela')
                if not tf or ts is None:
                    continue
                ok = sincronia.es_reciente(ts, tf)
                r.append(("niveles %s" % tf, ok,
                          "ultima vela %s" % d.get('fecha_ultima_vela', '?')))
            except (OSError, ValueError):
                continue
    return r


def _dur(s):
    if s is None:
        return "?"
    s = int(s)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh %dm" % (s // 3600, (s % 3600) // 60)


def _notificar(cambios, coin, mercado):
    caidos = [c for c in cambios if not c[1]]
    vueltos = [c for c in cambios if c[1]]
    lineas = []
    if caidos:
        lineas.append("PARADO  %s %s" % (coin, mercado))
        lineas += ["  - %s: %s" % (n, d) for n, _, d in caidos]
    if vueltos:
        lineas.append("Recuperado  %s %s" % (coin, mercado))
        lineas += ["  - %s" % n for n, _, _ in vueltos]
    texto = "\n".join(lineas)
    print(texto)
    avisos.enviar(texto)


def main():
    p = argparse.ArgumentParser(
        description="Avisa por Telegram cuando un recolector deja de grabar.")
    p.add_argument("--coin", default="ETH")
    p.add_argument("--mercado", default="futuros")
    p.add_argument("--loop", type=float, default=None,
                   help="segundos entre revisiones (sin esto, una pasada y salir)")
    p.add_argument("--probar", action="store_true",
                   help="manda un mensaje de prueba y sale")
    a = p.parse_args()

    if a.probar:
        if not avisos.configurado():
            print("NO enviado: faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env")
        elif avisos.enviar("vigilante.py: prueba de aviso"):
            print("enviado")
        else:
            print("NO enviado: el .env esta completo pero Telegram rechazo el envio.")
            print("  Causa habitual: el chat_id no es el tuyo, o no has pulsado")
            print("  START en el bot. Escribele y mira:")
            print("  https://api.telegram.org/bot<TOKEN>/getUpdates")
        return

    if not avisos.configurado():
        print("(Telegram no configurado: solo se imprime por consola)")

    estado = {}
    while True:
        actual = revisar(a.coin.upper(), a.mercado)
        cambios = [(n, ok, d) for n, ok, d in actual
                   if n in estado and estado[n] != ok]
        nuevos_malos = [(n, ok, d) for n, ok, d in actual
                        if n not in estado and not ok]
        if cambios or nuevos_malos:
            _notificar(cambios + nuevos_malos, a.coin.upper(), a.mercado)
        elif a.loop is None:
            print("%s %s: %d/%d recolectores al dia"
                  % (a.coin.upper(), a.mercado,
                     sum(1 for _, ok, _ in actual if ok), len(actual)))
            for n, ok, d in actual:
                print("  %-14s %-8s %s" % (n, "OK" if ok else "PARADO", d))
        estado = dict((n, ok) for n, ok, _ in actual)
        if a.loop is None:
            return
        time.sleep(a.loop)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrumpido")
        sys.exit(130)
