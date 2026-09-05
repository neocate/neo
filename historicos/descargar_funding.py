
import csv
import glob
import os
import sys
from datetime import datetime, timedelta, timezone

import ccxt

DIR_HISTORICOS = os.path.dirname(os.path.abspath(__file__))
CAMPOS = ["timestamp", "fecha_utc", "coin", "funding_rate", "funding_pct"]
LIMITE = 1000
MS_8H = 8 * 3600 * 1000


def _simbolo(coin):
    coin = coin.strip()
    if '/' in coin:
        return coin
    return "%s/USDT:USDT" % coin.upper()


def _moneda(coin):
    return coin.split('/')[0].upper() if '/' in coin else coin.upper()


def _fecha_str(f):
    return f.strftime("%d-%m-%y")


def _existente(moneda):
    patron = os.path.join(DIR_HISTORICOS, "*_%s_funding_binance.csv" % moneda)
    c = glob.glob(patron)
    return max(c, key=os.path.getmtime) if c else None


def _ultimo_ts(ruta):
    ultimo = None
    try:
        with open(ruta, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    ultimo = int(r["timestamp"])
                except (KeyError, ValueError):
                    continue
    except OSError:
        return None
    return ultimo


def _descargar(cliente, simbolo, desde_ms, hasta_ms):
    filas = []
    cursor = desde_ms
    while cursor < hasta_ms:
        lote = cliente.fetch_funding_rate_history(simbolo, since=cursor, limit=LIMITE)
        if not lote:
            break
        nuevos = [x for x in lote if x['timestamp'] >= cursor]
        if not nuevos:
            break
        filas.extend(nuevos)
        avance = max(x['timestamp'] for x in nuevos)
        if avance <= cursor:
            break
        cursor = avance + 1
        if len(lote) < LIMITE:
            break
    return filas


def actualizar(coin):
    cliente = ccxt.binanceusdm({'enableRateLimit': True})
    cliente.load_markets()
    simbolo = _simbolo(coin)
    moneda = _moneda(coin)

    ahora = cliente.milliseconds()
    hasta = (ahora // MS_8H) * MS_8H

    previo = _existente(moneda)
    if previo:
        ult = _ultimo_ts(previo)
        desde = (ult + 1) if ult else 1
        print("Historico previo de %s: hasta %s. Pido lo que falta."
              % (moneda, datetime.fromtimestamp(ult / 1000, timezone.utc).strftime('%Y-%m-%d')
                 if ult else '?'))
    else:
        desde = 1
        print("Sin historico previo de %s en %s - descarga completa."
              % (moneda, DIR_HISTORICOS))

    filas = _descargar(cliente, simbolo, desde, hasta)
    if not filas:
        print("  [OK] nada nuevo para %s" % moneda)
        return

    nueva = os.path.join(DIR_HISTORICOS, "%s_%s_funding_binance.csv"
                         % (_fecha_str(datetime.now(timezone.utc)), moneda))
    modo = 'a' if previo else 'w'
    if previo and previo != nueva:
        os.replace(previo, nueva)

    with open(nueva, modo, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if modo == 'w':
            w.writeheader()
        for x in filas:
            t = x['timestamp']
            r = x['fundingRate']
            if r is None:
                continue
            w.writerow({
                "timestamp": t,
                "fecha_utc": datetime.fromtimestamp(t / 1000, timezone.utc)
                                     .strftime("%Y-%m-%d %H:%M:%S"),
                "coin": moneda,
                "funding_rate": r,
                "funding_pct": round(float(r) * 100, 6),
            })
    ts = [x['timestamp'] for x in filas]
    print("  [OK] %d registros -> %s" % (len(filas), os.path.basename(nueva)))
    print("       %s -> %s"
          % (datetime.fromtimestamp(min(ts) / 1000, timezone.utc).strftime('%Y-%m-%d'),
             datetime.fromtimestamp(max(ts) / 1000, timezone.utc).strftime('%Y-%m-%d')))


def main():
    if len(sys.argv) < 2 or "-h" in sys.argv or "--help" in sys.argv:
        print("uso: python descargar_funding.py <coin> [coin2 ...]")
        print("  ejemplo: python historicos/descargar_funding.py eth btc icp sol")
        sys.exit(0 if "-h" in sys.argv or "--help" in sys.argv else 1)
    for coin in sys.argv[1:]:
        actualizar(coin)


if __name__ == "__main__":
    main()
