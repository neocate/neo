import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_ema import (
    ATR_PERIOD,
    CAPITAL_TOTAL,
    FEE_TAKER,
    FUNDING_RATE_8H,
    LEVERAGE,
    RIESGO_PCT,
    SL_ATR_MULT,
    SLIPPAGE_BPS,
    TF_MINUTES,
    TP_ATR_MULT,
    aplicar_slippage,
    calcular_atr,
    cerrar_posicion,
    fecha_fin_por_defecto,
    filtrar_periodo,
)

DIR_FLUJO = Path(__file__).resolve().parent.parent / "libro" / "datos" / "flujo"

CVD_EMA1 = 9
CVD_EMA2 = 26


def cargar_flujo(data_dir: Path, coin: str) -> pd.DataFrame:
    archivos = sorted(data_dir.glob(f"flujo_{coin}_futuros_*.csv"))
    if not archivos:
        raise FileNotFoundError(f"No se encontraron flujo_{coin}_futuros_*.csv en {data_dir}")
    df = pd.concat([pd.read_csv(a) for a in archivos], ignore_index=True)
    df["fecha_utc"] = pd.to_datetime(df["fecha_utc"], utc=True, errors="coerce")
    if df["fecha_utc"].isna().any():
        raise ValueError("Fechas inválidas en archivos de flujo")
    df = df.sort_values("fecha_utc").drop_duplicates(subset="fecha_utc").reset_index(drop=True)
    df = df.rename(columns={"precio_apertura": "open", "precio_max": "high", "precio_min": "low", "precio_cierre": "close"})
    # El "cvd" de cada archivo reinicia a medianoche; se recalcula continuo
    # sobre todo el histórico concatenado para no perder la tendencia entre días.
    df["cvd"] = df["delta_vol"].cumsum()
    return df[["fecha_utc", "open", "high", "low", "close", "vol_buy", "vol_sell", "delta_vol", "cvd", "n_trades"]]


def reconstruir_flujo(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "1m":
        return df.copy()
    if timeframe not in TF_MINUTES:
        raise ValueError(f"Timeframe no soportado: {timeframe}")
    rule = {"3m": "3min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}[timeframe]
    base = df.set_index("fecha_utc")
    salida = base.resample(rule, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "vol_buy": "sum", "vol_sell": "sum", "delta_vol": "sum", "n_trades": "sum",
    }).dropna(subset=["open", "high", "low", "close"]).reset_index()
    salida["cvd"] = salida["delta_vol"].cumsum()
    return salida


def backtest(tf_senales="1m", cvd_ema1=CVD_EMA1, cvd_ema2=CVD_EMA2, data_dir=DIR_FLUJO, coin="ETH", inicio=None, fin=None, riesgo_pct=RIESGO_PCT, leverage=LEVERAGE, fee=FEE_TAKER, slippage_bps=SLIPPAGE_BPS, atr_period=ATR_PERIOD, sl_atr_mult=SL_ATR_MULT, tp_atr_mult=TP_ATR_MULT, funding_rate_8h=FUNDING_RATE_8H):
    if tf_senales not in TF_MINUTES:
        raise ValueError(f"Timeframes válidos: {', '.join(TF_MINUTES)}")
    if cvd_ema1 <= 0 or cvd_ema2 <= cvd_ema1 or atr_period <= 0:
        raise ValueError("Debe cumplirse cvd_ema1 > 0, cvd_ema2 > cvd_ema1 y atr_period > 0")
    if fin is None:
        fin = fecha_fin_por_defecto()

    print(f"[*] Cargando flujo_{coin}_futuros_*.csv de {data_dir}...")
    df_1m = filtrar_periodo(cargar_flujo(data_dir, coin), inicio, fin)
    print(f"[*] Velas 1m cargadas: {len(df_1m)}")
    if df_1m.empty:
        raise ValueError("No hay datos de flujo en el periodo indicado")
    df_senales = reconstruir_flujo(df_1m, tf_senales)
    print(f"[*] Velas {tf_senales}: {len(df_senales)}")
    df_ejecucion = df_1m  # la ejecución siempre corre a 1m: resolución nativa del flujo

    df_senales[f"cvd_ema{cvd_ema1}"] = df_senales["cvd"].ewm(span=cvd_ema1, adjust=False).mean()
    df_senales[f"cvd_ema{cvd_ema2}"] = df_senales["cvd"].ewm(span=cvd_ema2, adjust=False).mean()
    df_senales["atr"] = calcular_atr(df_senales, atr_period)

    estado = {"capital_disponible": CAPITAL_TOTAL, "posiciones": [], "trades_cerrados": [], "entradas": 0, "salidas": 0, "ultima_entrada_fecha": None}
    pendientes = []
    idx_senal = 0
    n_senales = len(df_senales)
    duracion_senal = pd.Timedelta(minutes=TF_MINUTES[tf_senales])
    senal_fecha = df_senales["fecha_utc"].to_numpy()
    senal_confirmada = (df_senales["fecha_utc"] + duracion_senal).to_numpy()
    senal_ema1 = df_senales[f"cvd_ema{cvd_ema1}"].to_numpy()
    senal_ema2 = df_senales[f"cvd_ema{cvd_ema2}"].to_numpy()
    senal_atr = df_senales["atr"].to_numpy()
    print(f"=== BACKTEST FLUJO (CVD): {coin} {tf_senales} -> 1m ===")
    print(f"Fuente: {data_dir}")
    print(f"Corte: {fin} (exclusivo)")
    print(f"CVD EMA {cvd_ema1}/{cvd_ema2} | ATR: periodo {atr_period}, SL {sl_atr_mult}x, TP {tp_atr_mult}x")

    for vela in df_ejecucion.itertuples():
        fecha = vela.fecha_utc
        while idx_senal < n_senales and senal_confirmada[idx_senal] <= fecha:
            if idx_senal > 0:
                antes = senal_ema1[idx_senal - 1] > senal_ema2[idx_senal - 1]
                ahora = senal_ema1[idx_senal] > senal_ema2[idx_senal]
                if antes != ahora and not np.isnan(senal_atr[idx_senal]):
                    pendientes.append({"fecha_senal": senal_fecha[idx_senal], "tipo": "LONG" if ahora else "SHORT", "atr": float(senal_atr[idx_senal])})
            idx_senal += 1

        cierres = []
        for i, pos in enumerate(estado["posiciones"]):
            long = pos["tipo"] == "LONG"
            hit_sl = vela.low <= pos["sl_precio"] if long else vela.high >= pos["sl_precio"]
            hit_tp = vela.high >= pos["tp_precio"] if long else vela.low <= pos["tp_precio"]
            if hit_sl:
                cierres.append((i, pos["sl_precio"], "SL"))
            elif hit_tp:
                cierres.append((i, pos["tp_precio"], "TP"))
        for i, nivel, razon in reversed(cierres):
            pos = estado["posiciones"][i]
            salida = aplicar_slippage(nivel, pos["tipo"], False, slippage_bps)
            cerrar_posicion(estado, pos, salida, fecha, razon, fee, funding_rate_8h)
            del estado["posiciones"][i]

        ejecutables = [p for p in pendientes if p["fecha_senal"] <= fecha]
        pendientes = [p for p in pendientes if p["fecha_senal"] > fecha]
        for orden in ejecutables:
            tipo = orden["tipo"]
            for i in reversed([i for i, pos in enumerate(estado["posiciones"]) if pos["tipo"] != tipo]):
                pos = estado["posiciones"][i]
                salida = aplicar_slippage(vela.open, pos["tipo"], False, slippage_bps)
                cerrar_posicion(estado, pos, salida, fecha, "FLIP", fee, funding_rate_8h)
                del estado["posiciones"][i]
            if estado["posiciones"] or estado["capital_disponible"] <= 0:
                continue
            margen_trade = estado["capital_disponible"] * riesgo_pct
            nominal_trade = margen_trade * leverage
            entrada = aplicar_slippage(vela.open, tipo, True, slippage_bps)
            distancia_sl = orden["atr"] * sl_atr_mult
            distancia_tp = orden["atr"] * tp_atr_mult
            sl = entrada - distancia_sl if tipo == "LONG" else entrada + distancia_sl
            tp = entrada + distancia_tp if tipo == "LONG" else entrada - distancia_tp
            fee_entrada = nominal_trade * fee
            estado["capital_disponible"] -= margen_trade + fee_entrada
            estado["posiciones"].append({"fecha": fecha, "entrada": entrada, "tipo": tipo, "sl_precio": sl, "tp_precio": tp, "nominal": nominal_trade, "margen": margen_trade, "fee_entrada": fee_entrada})
            estado["entradas"] += 1
            estado["ultima_entrada_fecha"] = fecha

    ultima = df_ejecucion.iloc[-1]
    fin_periodo = ultima["fecha_utc"]
    for pos in list(estado["posiciones"]):
        salida = aplicar_slippage(float(ultima["close"]), pos["tipo"], False, slippage_bps)
        cerrar_posicion(estado, pos, salida, fin_periodo, "FIN_TEST", fee, funding_rate_8h)
    estado["posiciones"] = []

    trades = estado["trades_cerrados"]
    resultados = [x["ganancia_neta"] for x in trades]
    wins = [x for x in resultados if x > 0]
    losses = [x for x in resultados if x < 0]
    pf = sum(wins) / abs(sum(losses)) if losses else np.inf
    print("\n=== RESULTADOS ===")
    print(f"Entradas: {estado['entradas']} | Salidas: {estado['salidas']} | Trades: {len(trades)}")
    if estado["capital_disponible"] <= CAPITAL_TOTAL * 0.01:
        print(f"AVISO: capital por debajo del 1% del inicial (${estado['capital_disponible']:.4f}) — la cuenta sigue operando pero con tamaños marginales")
    print(f"Equity final: ${estado['capital_disponible']:.4f} | P&L neto: ${estado['capital_disponible'] - CAPITAL_TOTAL:.4f}")
    print(f"Win rate: {len(wins) / len(resultados) * 100:.2f}%" if resultados else "Win rate: n/a")
    print(f"Expectativa/trade: ${np.mean(resultados):.4f}" if resultados else "Expectativa/trade: n/a")
    print(f"Profit factor: {pf:.4f}")
    print(f"Costes totales: ${sum(x['comisiones'] + x['funding'] for x in trades):.4f}")
    return estado


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest sobre flujo de órdenes: cruce de EMA sobre CVD, con SL/TP por ATR")
    parser.add_argument("-tf", "--timeframe", default="1m", choices=list(TF_MINUTES))
    parser.add_argument("-cvd1", "--cvd-ema1", type=int, default=CVD_EMA1)
    parser.add_argument("-cvd2", "--cvd-ema2", type=int, default=CVD_EMA2)
    parser.add_argument("--data-dir", default=str(DIR_FLUJO))
    parser.add_argument("--coin", default="ETH")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--fee", type=float, default=FEE_TAKER)
    parser.add_argument("--slippage-bps", type=float, default=SLIPPAGE_BPS)
    parser.add_argument("--atr-period", type=int, default=ATR_PERIOD)
    parser.add_argument("--sl-atr", type=float, default=SL_ATR_MULT)
    parser.add_argument("--tp-atr", type=float, default=TP_ATR_MULT)
    parser.add_argument("--funding-8h", type=float, default=FUNDING_RATE_8H)
    parser.add_argument("--riesgo-pct", type=float, default=RIESGO_PCT)
    args = parser.parse_args()
    backtest(tf_senales=args.timeframe, cvd_ema1=args.cvd_ema1, cvd_ema2=args.cvd_ema2, data_dir=Path(args.data_dir), coin=args.coin, inicio=args.start, fin=args.end, fee=args.fee, slippage_bps=args.slippage_bps, atr_period=args.atr_period, sl_atr_mult=args.sl_atr, tp_atr_mult=args.tp_atr, funding_rate_8h=args.funding_8h, riesgo_pct=args.riesgo_pct)
