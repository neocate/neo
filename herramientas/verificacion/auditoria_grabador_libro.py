# Uso: python herramientas/verificacion/auditoria_grabador_libro.py [coin ...]

import csv
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COLS = ["timestamp_ms", "fecha_utc", "bid", "ask", "spread_bps", "mid", "microprecio",
        "imbalance", "imbalance_niveles", "open_interest", "funding_rate_pct",
        "long_short_ratio", "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd",
        "bids_json", "asks_json", "pid"]
IDX = {c: i for i, c in enumerate(COLS)}


def f(v):
    return float(v) if v not in ("", None) else None


def auditar(coin):
    ruta = os.path.join(RAIZ, "herramientas", "grabador_libro", f"flujo_{coin}.csv")
    print(f"\n{'=' * 100}\n{coin}\n{'=' * 100}")

    n = 0
    errores = {
        "delta_vol_no_cuadra": [], "cvd_no_cuadra_sin_reinicio": [],
        "spread_no_cuadra": [], "mid_no_cuadra": [], "microprecio_no_cuadra": [],
        "imbalance_no_cuadra": [], "imbalance_niveles_raro": [],
        "bidask_vs_json_no_cuadra": [], "libro_json_desordenado": [],
        "vol_negativo": [], "n_trades_cero_con_volumen": [],
        "oi_negativo": [], "funding_absurdo": [], "ls_ratio_raro": [],
        "gap_timestamp": [],
    }
    cvd_prev = None
    pid_prev = None
    ts_prev = None

    with open(ruta, newline="") as fh:
        r = csv.reader(fh)
        header = next(r)
        assert header == COLS, f"cabecera distinta: {header}"
        for row in r:
            n += 1
            ts = int(row[IDX["timestamp_ms"]])
            pid = row[IDX["pid"]]
            bid, ask = f(row[IDX["bid"]]), f(row[IDX["ask"]])
            spread_bps, mid, micro = f(row[IDX["spread_bps"]]), f(row[IDX["mid"]]), f(row[IDX["microprecio"]])
            imbalance = f(row[IDX["imbalance"]])
            imbalance_niveles = row[IDX["imbalance_niveles"]]
            oi = f(row[IDX["open_interest"]])
            funding = f(row[IDX["funding_rate_pct"]])
            ls_ratio = f(row[IDX["long_short_ratio"]])
            n_trades = int(row[IDX["n_trades"]]) if row[IDX["n_trades"]] != "" else 0
            vol_buy, vol_sell = f(row[IDX["vol_buy"]]), f(row[IDX["vol_sell"]])
            delta_vol, cvd = f(row[IDX["delta_vol"]]), f(row[IDX["cvd"]])
            bids_json, asks_json = row[IDX["bids_json"]], row[IDX["asks_json"]]

            if vol_buy is not None and vol_sell is not None and delta_vol is not None:
                if abs((vol_buy - vol_sell) - delta_vol) > 1e-6:
                    errores["delta_vol_no_cuadra"].append((ts, vol_buy, vol_sell, delta_vol))

            if cvd_prev is not None and cvd is not None and delta_vol is not None and pid == pid_prev:
                if abs((cvd_prev + delta_vol) - cvd) > 1e-4:
                    errores["cvd_no_cuadra_sin_reinicio"].append((ts, cvd_prev, delta_vol, cvd))
            cvd_prev, pid_prev = cvd, pid

            if (vol_buy or 0) < 0 or (vol_sell or 0) < 0:
                errores["vol_negativo"].append((ts, vol_buy, vol_sell))
            if n_trades == 0 and ((vol_buy or 0) > 0 or (vol_sell or 0) > 0):
                errores["n_trades_cero_con_volumen"].append((ts, n_trades, vol_buy, vol_sell))

            if bid is not None and ask is not None and bid <= ask:
                mid_calc = (bid + ask) / 2
                if mid is not None and abs(mid_calc - mid) > 1e-6:
                    errores["mid_no_cuadra"].append((ts, bid, ask, mid, mid_calc))
                if spread_bps is not None and mid_calc > 0:
                    spread_calc = (ask - bid) / mid_calc * 10000
                    if abs(spread_calc - spread_bps) > 1e-3:
                        errores["spread_no_cuadra"].append((ts, bid, ask, spread_bps, spread_calc))

            if imbalance_niveles not in ("10", ""):
                errores["imbalance_niveles_raro"].append((ts, imbalance_niveles))

            if bids_json and asks_json:
                try:
                    bids = json.loads(bids_json)
                    asks = json.loads(asks_json)
                except (ValueError, TypeError):
                    bids = asks = None
                if bids and asks:
                    precios_bids = [float(x[0]) for x in bids]
                    precios_asks = [float(x[0]) for x in asks]
                    if precios_bids != sorted(precios_bids, reverse=True):
                        errores["libro_json_desordenado"].append((ts, "bids", precios_bids[:5]))
                    if precios_asks != sorted(precios_asks):
                        errores["libro_json_desordenado"].append((ts, "asks", precios_asks[:5]))
                    if bid is not None and abs(precios_bids[0] - bid) > 1e-6:
                        errores["bidask_vs_json_no_cuadra"].append((ts, "bid", bid, precios_bids[0]))
                    if ask is not None and abs(precios_asks[0] - ask) > 1e-6:
                        errores["bidask_vs_json_no_cuadra"].append((ts, "ask", ask, precios_asks[0]))

                    if bid is not None and ask is not None and bid <= ask:
                        vb0, va0 = float(bids[0][1]), float(asks[0][1])
                        if (vb0 + va0) > 0:
                            micro_calc = (ask * vb0 + bid * va0) / (vb0 + va0)
                            if micro is not None and abs(micro_calc - micro) > 1e-4:
                                errores["microprecio_no_cuadra"].append((ts, micro, micro_calc))
                        vb10 = sum(float(x[1]) for x in bids[:10])
                        va10 = sum(float(x[1]) for x in asks[:10])
                        if (vb10 + va10) > 0:
                            imb_calc = (vb10 - va10) / (vb10 + va10)
                            if imbalance is not None and abs(imb_calc - imbalance) > 1e-4:
                                errores["imbalance_no_cuadra"].append((ts, imbalance, imb_calc))

            if oi is not None and oi < 0:
                errores["oi_negativo"].append((ts, oi))
            if funding is not None and abs(funding) > 5:
                errores["funding_absurdo"].append((ts, funding))
            if ls_ratio is not None and (ls_ratio <= 0 or ls_ratio > 50):
                errores["ls_ratio_raro"].append((ts, ls_ratio))

            if ts_prev is not None and (ts - ts_prev) > 60_000:
                errores["gap_timestamp"].append((ts_prev, ts, (ts - ts_prev) / 1000))
            ts_prev = ts

    print(f"{n} filas procesadas\n")
    for tipo, lista in errores.items():
        print(f"{tipo}: {len(lista)}")
        for ejemplo in lista[:3]:
            print(f"    {ejemplo}")
    return errores


if __name__ == "__main__":
    coins = [c.upper() for c in sys.argv[1:]] or ["BTC", "ETH", "ICP"]
    for coin in coins:
        auditar(coin)
