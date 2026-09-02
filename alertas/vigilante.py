# ---------------------------------------------------------------
# vigilante.py - Avisa por Telegram cuando un recolector deja de grabar.
#
# Por que existe:
#
#   El 2026-09-02 el daemon de velas llevaba mas de dos horas parado (se
#   detuvo para bajar historico y no se relanzo) y nadie se entero: se noto
#   por casualidad al mirar otra cosa. Los JSON de niveles se seguian
#   regenerando cada minuto y su fecha de fichero parecia fresca, pero
#   describian un mercado de hace horas.
#
#   El proyecto ya sabia detectarlo -- sincronia.py marca los TF parados y
#   analyzer los descarta -- pero lo hacia en silencio y solo para si mismo.
#   Detectar y avisar son cosas distintas: esto es lo segundo.
#
# Que vigila, y por que cada umbral:
#
#   libro.py   snapshots cada 900s. Es el UNICO cuya caida provoca perdida
#              irreversible: el libro de ordenes y el open interest no
#              tienen endpoint historico en ningun exchange. Umbral corto.
#   flujo.py   ventanas de 60s. Su caida NO pierde nada: el tape se puede
#              repedir 7 dias y el propio flujo.py repone los huecos al
#              volver. Umbral mas holgado, es un aviso de comodidad.
#   velas      una vela por periodo de cada TF. Reconstruibles sin limite.
#   niveles    se delega en sincronia.es_reciente, que ya tiene el criterio
#              bueno (periodos propios del TF, no un tiempo fijo).
#
# Solo avisa en los CAMBIOS de estado (OK -> CAIDO y CAIDO -> OK), no en
# cada vuelta: un proceso caido toda la noche debe dar un mensaje, no
# cuatrocientos. El estado vive en memoria, asi que al reiniciar el
# vigilante se vuelve a avisar de lo que siga mal, que es lo deseable.
#
# Sin TELEGRAM_TOKEN / TELEGRAM_CHAT_ID en .env no manda nada, pero sigue
# imprimiendo por consola: se puede usar como comprobacion manual.
#
# Uso:
#   python alertas/vigilante.py                 # una pasada y salir
#   python alertas/vigilante.py --loop 300      # daemon, cada 5 min
#   python alertas/vigilante.py --coin ETH --mercado futuros
#   python alertas/vigilante.py --probar        # fuerza un aviso de prueba
#
# ---------------------------------------------------------------

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

# Margen sobre la cadencia nominal antes de dar algo por caido. x3 deja pasar
# un ciclo perdido por un reintento de red sin declarar una caida.
FACTOR_MARGEN = 3.0

CADENCIA_LIBRO_S = 900.0
CADENCIA_FLUJO_S = 60.0

TF_SEGUNDOS = {'1m': 60, '3m': 180, '5m': 300, '15m': 900,
               '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400}


def _ahora():
    return time.time()


def _edad_ultima_fila(ruta, columna='fecha_utc'):
    """Segundos desde la ultima fila del CSV. Lee solo la cola: estos ficheros
    llegan a decenas de MB y no hace falta cargarlos enteros."""
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


def _mas_reciente(patron):
    c = glob.glob(patron)
    return max(c, key=os.path.getmtime) if c else None


def revisar(coin, mercado):
    """[(nombre, ok, detalle)] de cada recolector."""
    r = []

    # --- libro.py: lo unico irrecuperable ---
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

    # --- flujo.py: recuperable 7 dias, aviso de comodidad ---
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

    # --- velas: una por periodo de cada TF ---
    for p in sorted(glob.glob(os.path.join(DIR_NEO, "velas", coin,
                                           "bitget_%s_*_%s.csv" % (coin, mercado)))):
        tf = os.path.basename(p)[:-4].split('_')[2]
        seg = TF_SEGUNDOS.get(tf)
        if seg is None:
            continue
        e = _edad_ultima_fila(p)
        lim = seg * FACTOR_MARGEN
        r.append(("velas %s" % tf, e is not None and e <= lim,
                  "ultima vela hace %s (limite %s)" % (_dur(e), _dur(lim))
                  if e is not None else "no se pudo leer la ultima vela"))

    # --- niveles: criterio de sincronia, que ya es el bueno ---
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
        # Distinguir los dos fallos: no es lo mismo que falte configuracion que
        # que Telegram rechace el envio (chat_id equivocado, bot bloqueado...).
        # Decir siempre "falta el .env" manda a mirar donde no es.
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
