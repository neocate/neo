# ---------------------------------------------------------------
# buscar_libro_atascado.py - Busca rachas donde bid+ask de
# herramientas/grabador_libro/flujo_<COIN>.csv se quedan EXACTAMENTE
# congelados mucho tiempo con trades reales de por medio, y las cruza
# contra las velas de 1m (fuente independiente, REST) para distinguir
# mercado genuinamente plano de un libro realmente atascado.
#
# OJO (2026-08-17, ver anotaciones.md): el primer intento de esta
# comprobacion comparaba el RANGO TOTAL de precio real durante toda la
# racha contra el spread registrado, y eso dio muchos falsos positivos -
# una racha larga de mercado plano seguida de un movimiento real al final
# hace que el rango total parezca grande aunque el libro haya reaccionado
# bien, en la misma vela, al movimiento real. La comprobacion fiable es
# grano a grano (15s) contra n_trades/vol_buy/vol_sell de la propia
# flujo_<COIN>.csv, no contra el rango agregado de toda la racha - este
# script solo hace la deteccion de rachas; para el veredicto final hay que
# mirar las filas exactas del final de cada racha a mano (ver ejemplo en
# anotaciones.md 2026-08-17).
#
# Uso: python herramientas/verificacion/buscar_libro_atascado.py [coin ...]
# ---------------------------------------------------------------
import csv
import os
import sys
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UMBRAL_SEG = 180


def cargar_velas_1m(coin):
    ruta = os.path.join(RAIZ, "herramientas", "velas", coin, "1m_bitget.csv")
    velas = {}
    with open(ruta, newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            velas[int(row[0])] = (float(row[3]), float(row[4]))  # high, low
    return velas


def rango_real(velas_1m, ts_ini, ts_fin):
    m = ts_ini - (ts_ini % 60000)
    altos, bajos = [], []
    while m <= ts_fin:
        if m in velas_1m:
            h, l = velas_1m[m]
            altos.append(h)
            bajos.append(l)
        m += 60000
    if not altos:
        return None
    return max(altos) - min(bajos)


def analizar(coin):
    ruta = os.path.join(RAIZ, "herramientas", "grabador_libro", f"flujo_{coin}.csv")
    velas_1m = cargar_velas_1m(coin)
    filas = []
    with open(ruta, newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            n_trades = int(row[12]) if row[12] else 0
            filas.append((int(row[0]), row[2], row[3], n_trades))

    print(f"\n=== {coin} ===")
    racha_inicio = None
    bid_racha = ask_racha = None
    trades_racha = 0
    rachas = []

    def cerrar(fin_ts):
        if racha_inicio is None:
            return
        dur = (fin_ts - racha_inicio) / 1000
        if dur >= UMBRAL_SEG and trades_racha > 0:
            rachas.append((racha_inicio, fin_ts, dur, trades_racha, bid_racha, ask_racha))

    for i, (ts, bid, ask, n_trades) in enumerate(filas):
        cruzado = bid != "" and ask != "" and float(bid) > float(ask)
        vacio = bid == "" or ask == ""
        if vacio or cruzado:
            cerrar(filas[i - 1][0] if i > 0 else ts)
            racha_inicio = None
            continue
        if bid == bid_racha and ask == ask_racha:
            trades_racha += n_trades
            continue
        cerrar(filas[i - 1][0] if i > 0 else ts)
        racha_inicio = ts
        bid_racha, ask_racha = bid, ask
        trades_racha = n_trades
    cerrar(filas[-1][0])

    print(f"rachas no-cruzadas >= {UMBRAL_SEG}s con trades reales: {len(rachas)} "
          f"(revisar a mano grano a grano las mas largas, ver cabecera del fichero)")
    for inicio, fin, dur, trades, bid_r, ask_r in sorted(rachas, key=lambda x: -x[2])[:10]:
        rango = rango_real(velas_1m, inicio, fin)
        print(f"  {datetime.fromtimestamp(inicio / 1000, timezone.utc):%m-%d %H:%M} -> "
              f"{datetime.fromtimestamp(fin / 1000, timezone.utc):%H:%M} ({dur:.0f}s, {trades} trades) "
              f"libro={bid_r}/{ask_r}  rango_real_velas_1m={rango}")


if __name__ == "__main__":
    coins = [c.upper() for c in sys.argv[1:]] or ["BTC", "ETH", "ICP"]
    for coin in coins:
        analizar(coin)
