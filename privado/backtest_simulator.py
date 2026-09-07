import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

CAPITAL_TOTAL = 100.0
RIESGO_PCT = 0.05
LEVERAGE = 10.0
FEE_TAKER = 0.0004
SLIPPAGE_BPS = 2.0
FUNDING_RATE_8H = 0.0
ATR_PERIOD = 14
SL_ATR_MULT = 1.0
TP_ATR_MULT = 2.0
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
ARCHIVO_RE = re.compile(r"^(?P<actualizacion>\d{2}-\d{2}-\d{2})_(?P<coin>[A-Za-z0-9]+)_(?P<tf>[A-Za-z0-9]+)_binance\.csv$", re.IGNORECASE)


def buscar_historico(data_dir: Path, coin: str, timeframe: str) -> Path:
    candidatos = []
    for archivo in data_dir.glob(f"*_{coin}_{timeframe}_binance.csv"):
        match = ARCHIVO_RE.match(archivo.name)
        if match:
            fecha = datetime.strptime(match.group("actualizacion"), "%d-%m-%y")
            candidatos.append((fecha, archivo))
    if not candidatos:
        raise FileNotFoundError(f"No se encontró {coin} {timeframe} en {data_dir}")
    return max(candidatos, key=lambda x: x[0])[1]


def cargar_velas(archivo: Path) -> pd.DataFrame:
    if not archivo.exists():
        raise FileNotFoundError(f"{archivo} no existe")

    pickle_file = archivo.with_suffix('.pkl')
    if pickle_file.exists():
        print(f"[*] Leyendo cache {pickle_file.name}...")
        return pd.read_pickle(pickle_file)

    print(f"[*] Leyendo CSV (primera vez, será lento)...")
    df = pd.read_csv(archivo, dtype={'open': float, 'high': float, 'low': float, 'close': float, 'volume': float, 'volumen': float})
    requeridas = {"fecha_utc", "open", "high", "low", "close"}
    faltantes = requeridas - set(df.columns)
    if faltantes:
        raise ValueError(f"{archivo.name}: faltan columnas {sorted(faltantes)}")

    col_volumen = "volume" if "volume" in df.columns else ("volumen" if "volumen" in df.columns else None)
    if col_volumen:
        df["volume"] = pd.to_numeric(df[col_volumen], errors="coerce")
    else:
        df["volume"] = 0.0

    df["fecha_utc"] = pd.to_datetime(df["fecha_utc"], utc=True, errors="coerce")
    if df["fecha_utc"].isna().any():
        raise ValueError(f"{archivo.name}: fechas inválidas")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError(f"{archivo.name}: valores numéricos inválidos")
    if (df["high"] < df[["open", "close"]].max(axis=1)).any() or (df["low"] > df[["open", "close"]].min(axis=1)).any():
        raise ValueError(f"{archivo.name}: OHLC inconsistente")
    if df["fecha_utc"].duplicated().any():
        raise ValueError(f"{archivo.name}: timestamps duplicados")
    resultado = df[["fecha_utc", "open", "high", "low", "close", "volume"]].sort_values("fecha_utc").reset_index(drop=True)
    print(f"[*] Guardando cache {pickle_file.name}...")
    resultado.to_pickle(pickle_file)
    return resultado


def reconstruir_desde_1m(df_1m: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "1m":
        return df_1m.copy()
    if timeframe not in TF_MINUTES:
        raise ValueError(f"Timeframe no soportado: {timeframe}")
    rule = {"3m": "3min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}[timeframe]
    base = df_1m.set_index("fecha_utc")
    salida = base.resample(rule, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(subset=["open", "high", "low", "close"]).reset_index()
    return salida


def filtrar_periodo(df, inicio=None, fin=None):
    if inicio:
        df = df[df["fecha_utc"] >= pd.Timestamp(inicio, tz="UTC")]
    if fin:
        df = df[df["fecha_utc"] < pd.Timestamp(fin, tz="UTC")]
    return df.reset_index(drop=True)


def fecha_fin_por_defecto():
    # Corte exclusivo a las 00:00 UTC del día actual: incluye hasta ayer 23:59.
    hoy = pd.Timestamp.now(tz="UTC").normalize()
    return hoy.strftime("%Y-%m-%dT%H:%M:%SZ")


def aplicar_slippage(precio, tipo, entrada, bps):
    factor = bps / 10000.0
    if tipo == "LONG":
        return precio * (1 + factor) if entrada else precio * (1 - factor)
    return precio * (1 - factor) if entrada else precio * (1 + factor)


def calcular_atr(df, periodo):
    previo = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - previo).abs(), (df["low"] - previo).abs()], axis=1).max(axis=1)
    return tr.rolling(periodo, min_periods=periodo).mean()


def cerrar_posicion(estado, pos, precio_salida, fecha, razon, fee, funding_rate_8h):
    cantidad = pos["nominal"] / pos["entrada"]
    bruto = (precio_salida - pos["entrada"]) * cantidad if pos["tipo"] == "LONG" else (pos["entrada"] - precio_salida) * cantidad
    fee_salida = abs(cantidad * precio_salida) * fee
    horas = max((fecha - pos["fecha"]).total_seconds() / 3600, 0)
    funding = pos["nominal"] * funding_rate_8h * horas / 8
    neto = bruto - pos["fee_entrada"] - fee_salida - funding
    estado["capital_disponible"] += pos["margen"] + neto
    estado["trades_cerrados"].append({"bruto": bruto, "comisiones": pos["fee_entrada"] + fee_salida, "funding": funding, "ganancia_neta": neto, "razon": razon})
    estado["salidas"] += 1


def backtest(tf_senales="15m", tf_ejecucion="1m", ema1=12, ema2=26, data_dir=Path(r"Z:\neo\historicos"), coin="ETH", inicio=None, fin=None, riesgo_pct=RIESGO_PCT, leverage=LEVERAGE, fee=FEE_TAKER, slippage_bps=SLIPPAGE_BPS, atr_period=ATR_PERIOD, sl_atr_mult=SL_ATR_MULT, tp_atr_mult=TP_ATR_MULT, funding_rate_8h=FUNDING_RATE_8H):
    if tf_senales not in TF_MINUTES or tf_ejecucion not in TF_MINUTES:
        raise ValueError(f"Timeframes válidos: {', '.join(TF_MINUTES)}")
    if ema1 <= 0 or ema2 <= ema1 or atr_period <= 0:
        raise ValueError("Debe cumplirse ema1 > 0, ema2 > ema1 y atr_period > 0")
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
    print(f"[*] Calculando EMA y ATR...")

    df_senales[f"ema{ema1}"] = df_senales["close"].ewm(span=ema1, adjust=False).mean()
    df_senales[f"ema{ema2}"] = df_senales["close"].ewm(span=ema2, adjust=False).mean()
    df_senales["atr"] = calcular_atr(df_senales, atr_period)

    estado = {"capital_disponible": CAPITAL_TOTAL, "posiciones": [], "trades_cerrados": [], "entradas": 0, "salidas": 0, "ultima_entrada_fecha": None}
    pendientes = []
    idx_senal = 0
    n_senales = len(df_senales)
    # Arrays numpy en vez de .iloc/iterrows: el loop corre por cada vela de
    # ejecución (hasta millones de filas en barridos multi-año) y el acceso
    # posicional de pandas fila a fila es el cuello de botella dominante.
    duracion_senal = pd.Timedelta(minutes=TF_MINUTES[tf_senales])
    senal_fecha = df_senales["fecha_utc"].to_numpy()
    senal_confirmada = (df_senales["fecha_utc"] + duracion_senal).to_numpy()
    senal_ema1 = df_senales[f"ema{ema1}"].to_numpy()
    senal_ema2 = df_senales[f"ema{ema2}"].to_numpy()
    senal_atr = df_senales["atr"].to_numpy()
    print(f"=== BACKTEST BINANCE RECONSTRUIDO: {coin} {tf_senales} -> {tf_ejecucion} ===")
    print(f"Fuente única: {archivo_1m}")
    print(f"Corte: {fin} (exclusivo; incluye hasta ayer 23:59 UTC)")
    print(f"ATR: periodo {atr_period}, SL {sl_atr_mult}x, TP {tp_atr_mult}x")

    for vela in df_ejecucion.itertuples():
        fecha = vela.fecha_utc
        # Las marcas reconstruidas son el inicio de la vela; la señal solo
        # existe después de que transcurra todo el intervalo de esa vela.
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
            # OHLC no revela el orden intrabar. Si ambos niveles se tocan,
            # se elige SL primero como política conservadora y reproducible.
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
            # Stop-and-reverse: una señal opuesta cierra la posición abierta
            # en vez de acumularse en paralelo (evita long+short simultáneos).
            for i in reversed([i for i, pos in enumerate(estado["posiciones"]) if pos["tipo"] != tipo]):
                pos = estado["posiciones"][i]
                salida = aplicar_slippage(vela.open, pos["tipo"], False, slippage_bps)
                cerrar_posicion(estado, pos, salida, fecha, "FLIP", fee, funding_rate_8h)
                del estado["posiciones"][i]
            if estado["posiciones"] or estado["capital_disponible"] <= 0:
                continue
            # Margen = % del capital disponible EN ESE MOMENTO (no un $ fijo):
            # una racha de pérdidas reduce el tamaño de la siguiente entrada
            # en vez de bloquear la cuenta por debajo de un umbral fijo.
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
    # Con margen en % del capital ya no hay un umbral fijo que bloquee la
    # cuenta, así que el riesgo ahora es agotamiento progresivo, no bloqueo.
    if estado["capital_disponible"] <= CAPITAL_TOTAL * 0.01:
        print(f"AVISO: capital por debajo del 1% del inicial (${estado['capital_disponible']:.4f}) — la cuenta sigue operando pero con tamaños marginales")
    print(f"Equity final: ${estado['capital_disponible']:.4f} | P&L neto: ${estado['capital_disponible'] - CAPITAL_TOTAL:.4f}")
    print(f"Win rate: {len(wins) / len(resultados) * 100:.2f}%" if resultados else "Win rate: n/a")
    print(f"Expectativa/trade: ${np.mean(resultados):.4f}" if resultados else "Expectativa/trade: n/a")
    print(f"Profit factor: {pf:.4f}")
    print(f"Costes totales: ${sum(x['comisiones'] + x['funding'] for x in trades):.4f}")
    return estado


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Binance: timeframes reconstruidos desde velas 1m y SL/TP por ATR")
    parser.add_argument("-tf", "--timeframe", default="15m", choices=list(TF_MINUTES))
    parser.add_argument("-tf_exec", "--timeframe-ejecucion", default="1m", choices=list(TF_MINUTES))
    parser.add_argument("-ema1", "--ema1", type=int, default=12)
    parser.add_argument("-ema2", "--ema2", type=int, default=26)
    parser.add_argument("--data-dir", default=r"Z:\neo\historicos")
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
    backtest(tf_senales=args.timeframe, tf_ejecucion=args.timeframe_ejecucion, ema1=args.ema1, ema2=args.ema2, data_dir=Path(args.data_dir), coin=args.coin, inicio=args.start, fin=args.end, fee=args.fee, slippage_bps=args.slippage_bps, atr_period=args.atr_period, sl_atr_mult=args.sl_atr, tp_atr_mult=args.tp_atr, funding_rate_8h=args.funding_8h, riesgo_pct=args.riesgo_pct)
