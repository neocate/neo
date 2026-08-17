# ---------------------------------------------------------------
# verificar_indicadores.py - Verificacion independiente de
# mercado/indicadores.py.rsi()/atr() contra una implementacion de
# referencia escrita a mano (formula de libro de texto, sin optimizar,
# sin reusar nada de indicadores.py) sobre datos reales.
#
# No es un test de "cambio esperado" - es una comprobacion de que la
# matematica en si (Wilder RSI/ATR) da lo que dice dar, independientemente
# de si el codigo de indicadores.py cambia con el tiempo.
#
# Uso:
#   python herramientas/verificacion/verificar_indicadores.py [coin] [tf]
# ---------------------------------------------------------------
import csv
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from mercado import indicadores as ind


def rsi_referencia(cierres, periodo=14):
    n = len(cierres)
    out = [None] * n
    if n < periodo + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = cierres[i] - cierres[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    avg_gain = sum(gains[1:periodo + 1]) / periodo
    avg_loss = sum(losses[1:periodo + 1]) / periodo
    out[periodo] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(periodo + 1, n):
        avg_gain = (avg_gain * (periodo - 1) + gains[i]) / periodo
        avg_loss = (avg_loss * (periodo - 1) + losses[i]) / periodo
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def atr_referencia(altos, bajos, cierres, periodo=14):
    n = len(cierres)
    out = [None] * n
    if n < periodo + 1:
        return out
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(altos[i] - bajos[i], abs(altos[i] - cierres[i - 1]), abs(bajos[i] - cierres[i - 1]))
    atr = sum(tr[1:periodo + 1]) / periodo
    out[periodo] = atr
    for i in range(periodo + 1, n):
        atr = (atr * (periodo - 1) + tr[i]) / periodo
        out[i] = atr
    return out


def main():
    coin = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    tf = sys.argv[2] if len(sys.argv) > 2 else "1h"
    ruta = os.path.join(RAIZ, "herramientas", "velas", coin, f"{tf}_bitget.csv")

    velas = []
    with open(ruta, newline="") as f:
        r = csv.reader(f)
        next(r)
        for i, row in enumerate(r):
            if i >= 2000:
                break
            velas.append([int(row[0]), float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])])

    cierres = [v[4] for v in velas]
    altos = [v[2] for v in velas]
    bajos = [v[3] for v in velas]

    rsi_ref = rsi_referencia(cierres)
    atr_ref = atr_referencia(altos, bajos, cierres)
    rsi_ind = ind.rsi(cierres)
    atr_ind = ind.atr(altos, bajos, cierres)

    diffs_rsi = [abs(a - b) for a, b in zip(rsi_ref, rsi_ind) if a is not None and b is not None]
    diffs_atr = [abs(a - b) for a, b in zip(atr_ref, atr_ind) if a is not None and b is not None]

    print(f"{coin} {tf}: RSI {len(diffs_rsi)} puntos, diferencia maxima: {max(diffs_rsi):.10f}")
    print(f"{coin} {tf}: ATR {len(diffs_atr)} puntos, diferencia maxima: {max(diffs_atr):.10f}")

    assert max(diffs_rsi) < 1e-6, "RSI DIFIERE de la referencia independiente"
    assert max(diffs_atr) < 1e-6, "ATR DIFIERE de la referencia independiente"
    print("OK: indicadores.py.rsi()/atr() coinciden con una implementacion independiente.")


if __name__ == "__main__":
    main()
