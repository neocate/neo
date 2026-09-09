import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "niveles"))
from algoritmo_niveles import calcular as calcular_niveles

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
EMA_DIA_PERIOD = 50
EMA_DIA_FILTROS = ("none", "long", "short", "ambos")
NIVELES_FILTROS = ("none", "long", "short", "ambos")
NIVELES_K = 5
NIVELES_TOLERANCIA_ATR = 0.15
NIVELES_TOQUES_MIN = 3
NIVELES_CONFIRMACION_VELAS = 2
NIVELES_RECOMPUTE_DIAS = 30


def calcular_bollinger(df, periodo, num_std):
    media = df["close"].rolling(periodo, min_periods=periodo).mean()
    desvio = df["close"].rolling(periodo, min_periods=periodo).std()
    return media + num_std * desvio, media - num_std * desvio


def calcular_ema_diaria(df_1m: pd.DataFrame, periodo: int) -> pd.DataFrame:
    diario = reconstruir_desde_1m(df_1m, "1d")
    diario["ema_dia"] = diario["close"].ewm(span=periodo, adjust=False).mean()
    # La EMA del día D solo se conoce una vez que ese día cierra, o sea a
    # partir de las 00:00 UTC del día siguiente (evita lookahead).
    diario["fecha_confirmada"] = diario["fecha_utc"] + pd.Timedelta(days=1)
    return diario[["fecha_confirmada", "ema_dia"]]


def calcular_niveles_serie(df_senales, senal_confirmada, k, tolerancia_atr, toques_min, confirmacion_velas, periodo_atr, recompute_dias):
    # Snapshots recalculados periódicamente, cada uno usando solo velas ya
    # confirmadas hasta ese punto (mismo límite de causalidad que el resto
    # de señales: "senal_confirmada[idx]" es cuándo esa vela se conoce).
    ts_ms = (df_senales["fecha_utc"].astype("int64") // 10**6).to_numpy()
    o = df_senales["open"].to_numpy()
    h = df_senales["high"].to_numpy()
    l = df_senales["low"].to_numpy()
    c = df_senales["close"].to_numpy()
    v = df_senales["volume"].to_numpy()
    n = len(df_senales)
    cfg = dict(k=k, tolerancia_atr=tolerancia_atr, toques_min=toques_min,
               confirmacion_velas=confirmacion_velas, periodo_atr=periodo_atr,
               max_dist_pct=None, max_antig_dias=None, separacion_min_atr=0.3)
    minimo = periodo_atr + 30
    disponibles, niveles_por_snap, atr_por_snap = [], [], []
    proxima = None
    paso = np.timedelta64(recompute_dias, "D")
    for idx in range(minimo, n):
        if proxima is not None and senal_confirmada[idx] < proxima:
            continue
        velas = [[int(ts_ms[i]), float(o[i]), float(h[i]), float(l[i]), float(c[i]), float(v[i])] for i in range(idx + 1)]
        try:
            niveles, meta = calcular_niveles(velas, cfg)
        except ValueError:
            continue
        disponibles.append(senal_confirmada[idx])
        niveles_por_snap.append(niveles)
        atr_por_snap.append(meta["atr_actual"])
        proxima = senal_confirmada[idx] + paso
    return np.array(disponibles), niveles_por_snap, atr_por_snap


def _nivel_bloquea(niveles, atr_actual, tipo, precio, tolerancia_atr):
    if not niveles:
        return False
    tol = tolerancia_atr * atr_actual
    candidatos = []
    for n in niveles:
        if tipo == "LONG":
            # Resistencia por encima: techo intacto (rol original), o suelo
            # con flip — rompió hacia abajo y, al re-testearse desde abajo,
            # pasó a actuar de techo.
            if n["precio"] <= precio:
                continue
            if (n["tipo"] == "techo" and n["estado"] == "vivo") or (n["tipo"] == "suelo" and n["estado"] == "flip"):
                candidatos.append(n)
        else:
            # Soporte por debajo: suelo intacto, o techo con flip — rompió
            # hacia arriba y, retesteado desde arriba, pasó a actuar de suelo.
            if n["precio"] >= precio:
                continue
            if (n["tipo"] == "suelo" and n["estado"] == "vivo") or (n["tipo"] == "techo" and n["estado"] == "flip"):
                candidatos.append(n)
    if not candidatos:
        return False
    mas_cercano = min(candidatos, key=lambda n: abs(n["precio"] - precio))
    return abs(mas_cercano["precio"] - precio) <= tol


def backtest(tf_senales="15m", tf_ejecucion="1m", bb_period=BB_PERIOD, bb_std=BB_STD, ema_dia_period=EMA_DIA_PERIOD, ema_dia_short_period=None, ema_dia_filtro="none", niveles_filtro="none", niveles_k=NIVELES_K, niveles_tolerancia_atr=NIVELES_TOLERANCIA_ATR, niveles_toques_min=NIVELES_TOQUES_MIN, niveles_confirmacion_velas=NIVELES_CONFIRMACION_VELAS, niveles_recompute_dias=NIVELES_RECOMPUTE_DIAS, data_dir=DIR_HISTORICOS, coin="ETH", inicio=None, fin=None, riesgo_pct=RIESGO_PCT, leverage=LEVERAGE, fee=FEE_TAKER, slippage_bps=SLIPPAGE_BPS, atr_period=ATR_PERIOD, sl_atr_mult=SL_ATR_MULT, tp_atr_mult=TP_ATR_MULT, funding_rate_8h=FUNDING_RATE_8H):
    if tf_senales not in TF_MINUTES or tf_ejecucion not in TF_MINUTES:
        raise ValueError(f"Timeframes válidos: {', '.join(TF_MINUTES)}")
    if bb_period <= 1 or bb_std <= 0 or atr_period <= 0 or ema_dia_period <= 0 or (ema_dia_short_period is not None and ema_dia_short_period <= 0):
        raise ValueError("Debe cumplirse bb_period > 1, bb_std > 0, atr_period > 0, ema_dia_period > 0 y ema_dia_short_period > 0")
    if ema_dia_filtro not in EMA_DIA_FILTROS:
        raise ValueError(f"ema_dia_filtro válidos: {', '.join(EMA_DIA_FILTROS)}")
    if niveles_filtro not in NIVELES_FILTROS:
        raise ValueError(f"niveles_filtro válidos: {', '.join(NIVELES_FILTROS)}")
    # Si no se especifica un periodo distinto para SHORT, ambos lados usan el
    # mismo (comportamiento previo, sin duplicar cálculo).
    ema_dia_short_period = ema_dia_short_period or ema_dia_period
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
    print(f"[*] Calculando EMA {ema_dia_period} diaria (LONG)...")
    ema_dia_df = calcular_ema_diaria(df_1m, ema_dia_period)
    df_ejecucion = pd.merge_asof(df_ejecucion, ema_dia_df, left_on="fecha_utc", right_on="fecha_confirmada", direction="backward")
    if ema_dia_short_period != ema_dia_period:
        print(f"[*] Calculando EMA {ema_dia_short_period} diaria (SHORT)...")
        ema_dia_short_df = calcular_ema_diaria(df_1m, ema_dia_short_period).rename(columns={"ema_dia": "ema_dia_short", "fecha_confirmada": "fecha_confirmada_short"})
        df_ejecucion = pd.merge_asof(df_ejecucion, ema_dia_short_df, left_on="fecha_utc", right_on="fecha_confirmada_short", direction="backward")
    else:
        df_ejecucion["ema_dia_short"] = df_ejecucion["ema_dia"]
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

    niveles_disponibles = niveles_por_snap = niveles_atr_por_snap = None
    if niveles_filtro != "none":
        print(f"[*] Calculando niveles de soporte/resistencia (recompute cada {niveles_recompute_dias}d)...")
        niveles_disponibles, niveles_por_snap, niveles_atr_por_snap = calcular_niveles_serie(
            df_senales, senal_confirmada, niveles_k, niveles_tolerancia_atr, niveles_toques_min,
            niveles_confirmacion_velas, atr_period, niveles_recompute_dias)
        print(f"[*] {len(niveles_disponibles)} snapshots de niveles calculados")

    print(f"=== BACKTEST BOLLINGER BREAKOUT: {coin} {tf_senales} -> {tf_ejecucion} ===")
    print(f"Fuente única: {archivo_1m}")
    print(f"Corte: {fin} (exclusivo; incluye hasta ayer 23:59 UTC)")
    print(f"Bollinger: periodo {bb_period}, {bb_std}x desvío | ATR: periodo {atr_period}, SL {sl_atr_mult}x, TP {tp_atr_mult}x")
    print(f"EMA diaria: LONG {ema_dia_period} | SHORT {ema_dia_short_period} | filtro {ema_dia_filtro}")
    print(f"Niveles: k={niveles_k} tolerancia_atr={niveles_tolerancia_atr} toques_min={niveles_toques_min} | filtro {niveles_filtro}")

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
            estado["trades_cerrados"][-1]["precio_vs_ema_dia"] = pos.get("precio_vs_ema_dia")
            estado["trades_cerrados"][-1]["tipo"] = pos["tipo"]
            del estado["posiciones"][i]

        ejecutables = [p for p in pendientes if p["fecha_senal"] <= fecha]
        pendientes = [p for p in pendientes if p["fecha_senal"] > fecha]
        for orden in ejecutables:
            tipo = orden["tipo"]
            # Cada lado se clasifica contra su propia EMA diaria (pueden ser
            # periodos distintos): LONG usa ema_dia, SHORT usa ema_dia_short.
            # Se calcula contra el open, previo a decidir si se entra, para
            # poder usarlo tanto en el filtro como, si pasa, en el registro.
            if tipo == "LONG":
                precio_vs_ema_dia = None
                if not np.isnan(vela.ema_dia):
                    precio_vs_ema_dia = "ARRIBA" if vela.open > vela.ema_dia else "ABAJO"
                filtrado = ema_dia_filtro in ("long", "ambos") and precio_vs_ema_dia == "ABAJO"
            else:
                precio_vs_ema_dia = None
                if not np.isnan(vela.ema_dia_short):
                    precio_vs_ema_dia = "ARRIBA" if vela.open > vela.ema_dia_short else "ABAJO"
                filtrado = ema_dia_filtro in ("short", "ambos") and precio_vs_ema_dia == "ARRIBA"
            if not filtrado and niveles_filtro != "none" and niveles_disponibles is not None and len(niveles_disponibles):
                aplica = (niveles_filtro == "ambos"
                          or (niveles_filtro == "long" and tipo == "LONG")
                          or (niveles_filtro == "short" and tipo == "SHORT"))
                if aplica:
                    idx_snap = np.searchsorted(niveles_disponibles, fecha, side="right") - 1
                    if idx_snap >= 0:
                        filtrado = _nivel_bloquea(niveles_por_snap[idx_snap], niveles_atr_por_snap[idx_snap], tipo, vela.open, niveles_tolerancia_atr)
            if filtrado:
                continue
            for i in reversed([i for i, pos in enumerate(estado["posiciones"]) if pos["tipo"] != tipo]):
                pos = estado["posiciones"][i]
                salida = aplicar_slippage(vela.open, pos["tipo"], False, slippage_bps)
                cerrar_posicion(estado, pos, salida, fecha, "FLIP", fee, funding_rate_8h)
                estado["trades_cerrados"][-1]["precio_vs_ema_dia"] = pos.get("precio_vs_ema_dia")
                estado["trades_cerrados"][-1]["tipo"] = pos["tipo"]
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
            estado["posiciones"].append({"fecha": fecha, "entrada": entrada, "tipo": tipo, "sl_precio": sl, "tp_precio": tp, "nominal": nominal_trade, "margen": margen_trade, "fee_entrada": fee_entrada, "precio_vs_ema_dia": precio_vs_ema_dia})
            estado["entradas"] += 1
            estado["ultima_entrada_fecha"] = fecha

    ultima = df_ejecucion.iloc[-1]
    fin_periodo = ultima["fecha_utc"]
    for pos in list(estado["posiciones"]):
        salida = aplicar_slippage(float(ultima["close"]), pos["tipo"], False, slippage_bps)
        cerrar_posicion(estado, pos, salida, fin_periodo, "FIN_TEST", fee, funding_rate_8h)
        estado["trades_cerrados"][-1]["precio_vs_ema_dia"] = pos.get("precio_vs_ema_dia")
        estado["trades_cerrados"][-1]["tipo"] = pos["tipo"]
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

    def _resumen_grupo(lista):
        if not lista:
            return "sin trades"
        w = [x for x in lista if x > 0]
        l = [x for x in lista if x < 0]
        pf_grupo = sum(w) / abs(sum(l)) if l else np.inf
        return f"{len(lista)} trades | win rate {len(w) / len(lista) * 100:.2f}% | PF {pf_grupo:.4f}"

    arriba = [x["ganancia_neta"] for x in trades if x.get("precio_vs_ema_dia") == "ARRIBA"]
    abajo = [x["ganancia_neta"] for x in trades if x.get("precio_vs_ema_dia") == "ABAJO"]
    etiqueta_ema = f"LONG {ema_dia_period}" if ema_dia_short_period == ema_dia_period else f"LONG {ema_dia_period} / SHORT {ema_dia_short_period}"
    print(f"\n=== EMA diaria {etiqueta_ema} (filtro: {ema_dia_filtro}) ===")
    print(f"Precio ARRIBA al entrar: {_resumen_grupo(arriba)}")
    print(f"Precio ABAJO al entrar: {_resumen_grupo(abajo)}")
    for tipo in ("LONG", "SHORT"):
        grupo_arriba = [x["ganancia_neta"] for x in trades if x.get("tipo") == tipo and x.get("precio_vs_ema_dia") == "ARRIBA"]
        grupo_abajo = [x["ganancia_neta"] for x in trades if x.get("tipo") == tipo and x.get("precio_vs_ema_dia") == "ABAJO"]
        print(f"  {tipo} + ARRIBA: {_resumen_grupo(grupo_arriba)}")
        print(f"  {tipo} + ABAJO : {_resumen_grupo(grupo_abajo)}")
    return estado


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Binance: ruptura de Bandas de Bollinger con SL/TP por ATR")
    parser.add_argument("-tf", "--timeframe", default="15m", choices=list(TF_MINUTES))
    parser.add_argument("-tf_exec", "--timeframe-ejecucion", default="1m", choices=list(TF_MINUTES))
    parser.add_argument("-bbp", "--bb-period", type=int, default=BB_PERIOD)
    parser.add_argument("-bbs", "--bb-std", type=float, default=BB_STD)
    parser.add_argument("--ema-dia", type=int, default=EMA_DIA_PERIOD, help="Periodo EMA diaria para el lado LONG")
    parser.add_argument("--ema-dia-short", type=int, default=None, help="Periodo EMA diaria para el lado SHORT (default: igual a --ema-dia)")
    parser.add_argument("--ema-dia-filtro", choices=list(EMA_DIA_FILTROS), default="none", help="none=solo reporta | long=bloquea LONG si precio<EMA | short=bloquea SHORT si precio>EMA | ambos")
    parser.add_argument("--niveles-filtro", choices=list(NIVELES_FILTROS), default="none", help="none=desactivado | long=bloquea LONG si hay techo vivo cerca | short=bloquea SHORT si hay suelo vivo cerca | ambos")
    parser.add_argument("--niveles-k", type=int, default=NIVELES_K)
    parser.add_argument("--niveles-tolerancia-atr", type=float, default=NIVELES_TOLERANCIA_ATR)
    parser.add_argument("--niveles-toques-min", type=int, default=NIVELES_TOQUES_MIN)
    parser.add_argument("--niveles-confirmacion-velas", type=int, default=NIVELES_CONFIRMACION_VELAS)
    parser.add_argument("--niveles-recompute-dias", type=int, default=NIVELES_RECOMPUTE_DIAS, help="Cada cuántos días se recalculan los niveles (causal, solo con velas ya cerradas)")
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
    backtest(tf_senales=args.timeframe, tf_ejecucion=args.timeframe_ejecucion, bb_period=args.bb_period, bb_std=args.bb_std, ema_dia_period=args.ema_dia, ema_dia_short_period=args.ema_dia_short, ema_dia_filtro=args.ema_dia_filtro, niveles_filtro=args.niveles_filtro, niveles_k=args.niveles_k, niveles_tolerancia_atr=args.niveles_tolerancia_atr, niveles_toques_min=args.niveles_toques_min, niveles_confirmacion_velas=args.niveles_confirmacion_velas, niveles_recompute_dias=args.niveles_recompute_dias, data_dir=Path(args.data_dir), coin=args.coin, inicio=args.start, fin=args.end, fee=args.fee, slippage_bps=args.slippage_bps, atr_period=args.atr_period, sl_atr_mult=args.sl_atr, tp_atr_mult=args.tp_atr, funding_rate_8h=args.funding_8h, riesgo_pct=args.riesgo_pct)
