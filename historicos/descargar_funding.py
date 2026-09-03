# ---------------------------------------------------------------
# descargar_funding.py - Baja/actualiza el historico de funding de Binance
#
# El funding es el pago periodico entre largos y cortos de un perpetuo: cada
# 8 horas, quien esta del lado "caro" paga al otro. Es un COSTE REAL que
# ningun backtest de posiciones apalancadas puede ignorar -- y que los
# nuestros ignoraban hasta ahora.
#
# Medido en ETH: +0,0021% de media cada 8 h, positivo el 72% de las veces.
# Mantener un LARGO 60 dias cuesta ~0,37% del nocional en 180 pagos. Sobre
# capital apalancado 3-5x eso es un 1-2%, que sobre retornos del 5-7% por
# ventana no es decisivo pero tampoco despreciable.
#
# Y es ASIMETRICO: si el largo paga, el corto COBRA. Una estrategia que se
# pone larga en tendencia alcista y corta en bajista puede estar pagando en
# los dos lados o cobrando en los dos, segun el signo dominante de cada
# regimen. Sin el dato no hay forma de saberlo.
#
# Se baja de Binance, igual que las velas de descargar_bin.py, y por el mismo
# motivo: tiene historia desde el origen de cada perpetuo. Bitget solo sirve
# ~90 dias.
#
#   ETH desde 2019-11-27   BTC desde 2019-09-10
#   SOL desde 2020-09-13   ICP desde 2021-05-11
#
# Cache PERMANENTE en <raiz>/historicos/, misma convencion que las velas:
#     DD-MM-AA_<COIN>_funding_binance.csv
# Si no existe se baja todo; si existe se lee la ultima marca del propio
# contenido y solo se pide lo que falta, se ANADE y se renombra al nuevo dia
# de corte.
#
# CSV:
#     timestamp,fecha_utc,coin,funding_rate,funding_pct
#   funding_rate es la fraccion tal cual la da el exchange (0.0001)
#   funding_pct es la misma cifra en por ciento (0.01), por comodidad
#
# Uso:
#   python historicos/descargar_funding.py <coin> [coin2 ...]
#   python historicos/descargar_funding.py eth btc icp sol
# ---------------------------------------------------------------

import csv
import glob
import os
import sys
from datetime import datetime, timedelta, timezone

import ccxt

DIR_HISTORICOS = os.path.dirname(os.path.abspath(__file__))
CAMPOS = ["timestamp", "fecha_utc", "coin", "funding_rate", "funding_pct"]
LIMITE = 1000            # tope de registros por peticion
MS_8H = 8 * 3600 * 1000


def _simbolo(coin):
    """'eth' -> 'ETH/USDT:USDT' (perpetuo USDT-M de Binance)."""
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
    """Ultima marca guardada, leida del contenido y no del nombre."""
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
    """Pagina hacia delante hasta alcanzar el presente."""
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
            break                      # el cursor no progresa: se corta
        cursor = avance + 1
        if len(lote) < LIMITE:
            break
    return filas


def actualizar(coin):
    cliente = ccxt.binanceusdm({'enableRateLimit': True})
    cliente.load_markets()
    simbolo = _simbolo(coin)
    moneda = _moneda(coin)

    # se corta en el ultimo periodo de 8 h COMPLETO, para no guardar uno a medias
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
    if len(sys.argv) < 2:
        print(__doc__ or "uso: python descargar_funding.py <coin> [coin2 ...]")
        print("  ejemplo: python historicos/descargar_funding.py eth btc icp sol")
        sys.exit(1)
    for coin in sys.argv[1:]:
        actualizar(coin)


if __name__ == "__main__":
    main()
