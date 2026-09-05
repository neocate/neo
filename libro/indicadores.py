
import argparse
import glob
import os
from datetime import datetime, timedelta

import pandas as pd

DIR_BASE = os.path.dirname(os.path.abspath(__file__))
DIR_DATOS = os.path.join(DIR_BASE, "datos", "flujo")


def _ruta(datos, prefijo, coin, mercado, fecha):
    return os.path.join(datos, "%s_%s_%s_%s.csv" % (prefijo, coin, mercado, fecha.strftime("%Y%m%d")))


def _rutas_trades(datos, coin, mercado, fecha):
    base = _ruta(datos, "trades", coin, mercado, fecha)
    raiz, ext = os.path.splitext(base)
    return sorted(glob.glob(raiz + "*" + ext))


def _rango_fechas(desde, hasta):
    d = datetime.strptime(desde, "%Y-%m-%d")
    h = datetime.strptime(hasta, "%Y-%m-%d") if hasta else d
    dias = []
    while d <= h:
        dias.append(d)
        d += timedelta(days=1)
    return dias


def cargar_rango(datos, coin, mercado, desde, hasta):
    trades_partes, flujo_partes, faltantes = [], [], []
    for fecha in _rango_fechas(desde, hasta):
        rutas_t = _rutas_trades(datos, coin, mercado, fecha)
        rf = _ruta(datos, "flujo", coin, mercado, fecha)
        if rutas_t and os.path.exists(rf):
            for rt in rutas_t:
                trades_partes.append(pd.read_csv(rt))
            df_flujo = pd.read_csv(rf)
            df_flujo["_dia_archivo"] = fecha.date()
            flujo_partes.append(df_flujo)
        else:
            faltantes.append(fecha.strftime("%Y-%m-%d"))
    if not trades_partes:
        raise SystemExit("Sin datos para el rango pedido en %s" % datos)
    trades = pd.concat(trades_partes, ignore_index=True)
    flujo = pd.concat(flujo_partes, ignore_index=True)
    trades, n_dup = _dedup_trades(trades)
    return trades, flujo, faltantes, n_dup


def _dedup_trades(trades):
    antes = len(trades)
    claves = ["timestamp_exchange_ms", "precio", "volumen", "lado"]
    if "id" in trades.columns:
        con_id = trades["id"].notna()
        trades = pd.concat([
            trades[con_id].drop_duplicates(subset="id"),
            trades[~con_id].drop_duplicates(subset=claves),
        ], ignore_index=True)
    else:
        trades = trades.drop_duplicates(subset=claves)
    return trades, antes - len(trades)


def calcular_cvd(trades, flujo):
    buy = trades[trades["lado"] == "buy"].groupby("ventana_fin_ms")["volumen"].sum()
    sell = trades[trades["lado"] == "sell"].groupby("ventana_fin_ms")["volumen"].sum()
    por_ventana = pd.DataFrame({"vol_buy_recalc": buy, "vol_sell_recalc": sell}).fillna(0.0)
    por_ventana["delta_recalc"] = por_ventana["vol_buy_recalc"] - por_ventana["vol_sell_recalc"]

    flujo = flujo.sort_values("ventana_fin_ms")
    merge = flujo.merge(por_ventana, left_on="ventana_fin_ms", right_index=True, how="left")
    merge["cvd_recalc"] = merge.groupby("_dia_archivo")["delta_recalc"].cumsum()
    merge["diff_cvd"] = (merge["cvd"] - merge["cvd_recalc"]).abs()
    return merge


def calcular_vwap(trades):
    return (trades["precio"] * trades["volumen"]).sum() / trades["volumen"].sum()


def calcular_poc(trades, bin_size=0.5, top=5):
    bins = (trades["precio"] / bin_size).round() * bin_size
    perfil = trades.groupby(bins)["volumen"].sum().sort_values(ascending=False)
    return perfil.index[0], perfil.iloc[0], perfil.head(top)


def calcular_footprint(trades, flujo, bin_size=0.1, ventana_fin_ms=None):
    if ventana_fin_ms is None:
        fila = flujo.loc[flujo["n_trades"].idxmax()]
        ventana_fin_ms = int(fila["ventana_fin_ms"])
    else:
        fila = flujo.loc[flujo["ventana_fin_ms"] == ventana_fin_ms].iloc[0]

    sub = trades[trades["ventana_fin_ms"] == ventana_fin_ms].copy()
    if sub.empty:
        raise SystemExit("Sin trades para la ventana_fin_ms=%s" % ventana_fin_ms)
    sub["bin_precio"] = (sub["precio"] / bin_size).round() * bin_size
    tabla = sub.pivot_table(index="bin_precio", columns="lado", values="volumen", aggfunc="sum", fill_value=0.0)
    for lado in ("buy", "sell"):
        if lado not in tabla.columns:
            tabla[lado] = 0.0
    tabla["delta"] = tabla["buy"] - tabla["sell"]
    return fila, tabla.sort_index(ascending=False)


def main():
    p = argparse.ArgumentParser(description="CVD, VWAP, POC y footprint sobre los CSV de datos/flujo/.")
    p.add_argument("coin", nargs="?", default="eth")
    p.add_argument("--mercado", default="futuros")
    p.add_argument("--desde", required=True, help="YYYY-MM-DD")
    p.add_argument("--hasta", default=None, help="YYYY-MM-DD (default: igual a --desde)")
    p.add_argument("--datos", default=DIR_DATOS, help="carpeta con trades_*.csv y flujo_*.csv")
    p.add_argument("--bin-poc", type=float, default=0.5, help="tamano de bin en $ para el perfil de volumen (default: 0.5)")
    p.add_argument("--bin-footprint", type=float, default=0.1, help="tamano de bin en $ para el footprint (default: 0.1)")
    p.add_argument("--footprint", default=None,
                    help="ventana para el footprint, 'YYYY-MM-DD HH:MM' (default: la de mas trades del rango)")
    args = p.parse_args()

    coin = args.coin.upper()
    trades, flujo, faltantes, n_dup = cargar_rango(args.datos, coin, args.mercado, args.desde, args.hasta)

    print("=== %s/%s | %s a %s ===" % (coin, args.mercado, args.desde, args.hasta or args.desde))
    print("trades (tras deduplicar):", len(trades), "| duplicados descartados:", n_dup, "| ventanas de 1 min:", len(flujo))
    if faltantes:
        print("dias sin fichero (huecos de recoleccion):", ", ".join(faltantes))
    print()

    merge = calcular_cvd(trades, flujo)
    print("--- CVD (recalculado sobre trades deduplicados) ---")
    print("CVD final:", round(merge["cvd_recalc"].iloc[-1], 3))
    print("diferencia media vs. cvd guardado en flujo_*.csv:", round(merge["diff_cvd"].mean(), 3),
          "| maxima:", round(merge["diff_cvd"].max(), 3))
    if n_dup:
        print("aviso: %d trades duplicados descartados en el rango -> el cvd/vol_buy/vol_sell guardado en "
              "flujo_*.csv para esos dias esta calculado sobre los duplicados, por eso no coincide" % n_dup)
    print()

    vwap_sesion = calcular_vwap(trades)
    print("--- VWAP de la sesion ---")
    print(round(vwap_sesion, 4))
    print()

    poc_precio, poc_vol, top = calcular_poc(trades, bin_size=args.bin_poc)
    print("--- POC (bins de $%.2f) ---" % args.bin_poc)
    print("nivel:", poc_precio, "| volumen:", round(poc_vol, 2))
    print(top.round(2).to_string())
    print()

    ventana_ms = None
    if args.footprint:
        ventana_ms = int(pd.Timestamp(args.footprint, tz="UTC").timestamp() * 1000) + 60000
    fila, tabla = calcular_footprint(trades, flujo, bin_size=args.bin_footprint, ventana_fin_ms=ventana_ms)
    print("--- Footprint (bins de $%.2f) | ventana %s, %s trades ---" % (args.bin_footprint, fila["fecha_utc"], fila["n_trades"]))
    print(tabla.round(2).to_string())


if __name__ == "__main__":
    main()
