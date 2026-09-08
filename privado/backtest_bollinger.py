import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_ema import (
    ATR_PERIOD,
    CAPITAL_TOTAL,
    DIR_HISTORICOS,
    FEE_TAKER,
    FUNDING_RATE_8H,
    LEVERAGE,
    RIESGO_PCT,
    SL_ATR_MULT,
    SLIPPAGE_BPS,
    TF_MINUTES,
    TP_ATR_MULT,
    aplicar_slippage,
    buscar_historico,
    calcular_atr,
    cargar_velas,
    cerrar_posicion,
    fecha_fin_por_defecto,
    filtrar_periodo,
    reconstruir_desde_1m,
)

BB_PERIOD = 20
BB_STD = 2.0


def calcular_bollinger(df, periodo, num_std):
    media = df["close"].rolling(periodo, min_periods=periodo).mean()
    desvio = df["close"].rolling(periodo, min_periods=periodo).std()
    return media + num_std * desvio, media - num_std * desvio


def backtest(tf_senales="15m", tf_ejecucion="1m", bb_period=BB_PERIOD, bb_std=BB_STD, data_dir=DIR_HISTORICOS, coin="ETH", inicio=None, fin=None, riesgo_pct=RIESGO_PCT, leverage=LEVERAGE, fee=FEE_TAKER, slippage_bps=SLIPPAGE_BPS, atr_period=ATR_PERIOD, sl_atr_mult=SL_ATR_MULT, tp_atr_mult=TP_ATR_MULT, funding_rate_8h=FUNDING_RATE_8H):
    if tf_senales not in TF_MINUTES or tf_ejecucion not in TF_MINUTES:
        raise ValueError(f"Timeframes válidos: {', '.join(TF_MINUTES)}")
    if bb_period <= 1 or bb_std <= 0 or atr_period <= 0:
        raise ValueError("Debe cumplirse bb_period > 1, bb_std > 0 y atr_period > 0")
    if fin is None:
        fin = fecha_fin_por_defecto()

    archivo_1m = buscar_historico(data_dir, coin, "1m")
    print(f"[*] Cargando {archivo_1m.name}...")
    df_1m = filtrar_periodo(cargar_velas(archivo_1m), inicio, fin)
    print(f"[*] Velas 1m cargadas: {len(df_1m)}")
    if df_1m.empty:
        raise ValueError("No hay velas 1m en el periodo indicado")
    print(f"[*] Reconstruyendo a {tf_senales}...")
    df_senales = reconstruir_desde_1m(df_1m, tf_senales)
    print(f"[*] Velas {tf_senales}: {len(df_senales)}")
    print(f"[*] Reconstruyendo a {tf_ejecucion}...")
    df_ejecucion = reconstruir_desde_1m(df_1m, tf_ejecucion)
    print(f"[*] Velas {tf_ejecucion}: {len(df_ejecucion)}")
    print(f"[*] Calculando Bollinger y ATR...")

    banda_sup, banda_inf = calcular_bollinger(df_senales, bb_period, bb_std)
    df_senales["bb_sup"] = banda_sup
    df_senales["bb_inf"] = banda_inf
    df_senales["atr"] = calcular_atr(df_senales, atr_period)

    estado = {"capital_disponible": CAPITAL_TOTAL, "posiciones": [], "trades_cerrados": [], "entradas": 0, "salidas": 0, "ultima_entrada_fecha": None}
    pendientes = []
    idx_senal = 0
    n_senales = len(df_senales)
    duracion_senal = pd.Timedelta(minutes=TF_MINUTES[tf_senales])
    senal_fecha = df_senales["fecha_utc"].to_numpy()
    senal_confirmada = (df_senales["fecha_utc"] + duracion_senal).to_numpy()
    senal_close = df_senales["close"].to_numpy()
    senal_bb_sup = df_senales["bb_sup"].to_numpy()
    senal_bb_inf = df_senales["bb_inf"].to_numpy()
    senal_atr = df_senales["atr"].to_numpy()
    print(f"=== BACKTEST BOLLINGER BREAKOUT: {coin} {tf_senales} -> {tf_ejecucion} ===")
    print(f"Fuente única: {archivo_1m}")
    print(f"Corte: {fin} (exclusivo; incluye hasta ayer 23:59 UTC)")
    print(f"Bollinger: periodo {bb_period}, {bb_std}x desvío | ATR: periodo {atr_period}, SL {sl_atr_mult}x, TP {tp_atr_mult}x")

    for vela in df_ejecucion.itertuples():
        fecha = vela.fecha_utc
        # Igual que en backtest_simulator: la señal solo existe después de
        # que transcurra todo el intervalo de la vela de señal.
        while idx_senal < n_senales and senal_confirmada[idx_senal] <= fecha:
            if idx_senal > 0 and not np.isnan(senal_bb_sup[idx_senal - 1]) and not np.isnan(senal_atr[idx_senal]):
                # Breakout: el cierre pasa de estar dentro/en la banda a
                # quedar por fuera de ella respecto de la vela anterior.
                cruzo_arriba = senal_close[idx_senal - 1] <= senal_bb_sup[idx_senal - 1] and senal_close[idx_senal] > senal_bb_sup[idx_senal]
                cruzo_abajo = senal_close[idx_senal - 1] >= senal_bb_inf[idx_senal - 1] and senal_close[idx_senal] < senal_bb_inf[idx_senal]
                if cruzo_arriba:
                    pendientes.append({"fecha_senal": senal_fecha[idx_senal], "tipo": "LONG", "atr": float(senal_atr[idx_senal])})
                elif cruzo_abajo:
                    pendientes.append({"fecha_senal": senal_fecha[idx_senal], "tipo": "SHORT", "atr": float(senal_atr[idx_senal])})
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
    parser = argparse.ArgumentParser(description="Backtest Binance: ruptura de Bandas de Bollinger con SL/TP por ATR")
    parser.add_argument("-tf", "--timeframe", default="15m", choices=list(TF_MINUTES))
    parser.add_argument("-tf_exec", "--timeframe-ejecucion", default="1m", choices=list(TF_MINUTES))
    parser.add_argument("-bbp", "--bb-period", type=int, default=BB_PERIOD)
    parser.add_argument("-bbs", "--bb-std", type=float, default=BB_STD)
    parser.add_argument("--data-dir", default=str(DIR_HISTORICOS))
    parser.add_argument("--coin", default="ETH")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None, help="Por defecto: 00:00 UTC de hoy, incluye hasta ayer 23:59")
    parser.add_argument("--fee", type=float, default=FEE_TAKER)
    parser.add_argument("--slippage-bps", type=float, default=SLIPPAGE_BPS)
    parser.add_argument("--atr-period", type=int, default=ATR_PERIOD)
    parser.add_argument("--sl-atr", type=float, default=SL_ATR_MULT)
    parser.add_argument("--tp-atr", type=float, default=TP_ATR_MULT)
    parser.add_argument("--funding-8h", type=float, default=FUNDING_RATE_8H)
    parser.add_argument("--riesgo-pct", type=float, default=RIESGO_PCT, help="Margen por operación como fracción del capital disponible (0.05 = 5%%)")
    args = parser.parse_args()
    backtest(tf_senales=args.timeframe, tf_ejecucion=args.timeframe_ejecucion, bb_period=args.bb_period, bb_std=args.bb_std, data_dir=Path(args.data_dir), coin=args.coin, inicio=args.start, fin=args.end, fee=args.fee, slippage_bps=args.slippage_bps, atr_period=args.atr_period, sl_atr_mult=args.sl_atr, tp_atr_mult=args.tp_atr, funding_rate_8h=args.funding_8h, riesgo_pct=args.riesgo_pct)
