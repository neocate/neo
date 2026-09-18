"""
Paper trading en vivo: aplica la estrategia Bollinger breakout + EMA-diaria +
niveles de soporte/resistencia sobre los feeds de Bitget que ya corren en
velas/ y niveles/. No manda ninguna orden real — solo simula posiciones y
avisa por Telegram. Cada moneda tiene su propia config (timeframe, SL/TP,
EMA-diaria, niveles) validada por separado en CONFIG_COINS.

Uso:
    python vivo_multi.py --una-vez              # un solo chequeo, para probar
    python vivo_multi.py --loop 300              # chequea cada 5 min

Requiere que velas/<COIN>/bitget_<COIN>_<tf>_futuros.csv y
niveles/json/nivel_<COIN>_<tf>_futuros_k5_toques3.json existan y se sigan
actualizando (por descargar_bit_futuros.py --loop y niveles.py --loop) para
cada timeframe que use CONFIG_COINS.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_bollinger import BB_PERIOD, BB_STD, calcular_bollinger, _nivel_bloquea
from backtest_ema import ATR_PERIOD, calcular_atr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "alertas"))
try:
    import avisos
except ImportError:
    avisos = None

DIR_VELAS = Path(__file__).resolve().parent.parent / "velas"
DIR_NIVELES_JSON = Path(__file__).resolve().parent.parent / "niveles" / "json"
ARCHIVO_ESTADO = Path(__file__).resolve().parent / "posiciones_vivo_multi.json"
ARCHIVO_LOG = Path(__file__).resolve().parent / "vivo_multi.log"

# Config validada por moneda (ver sesiones de backtest en resultados_backtest.md
# y la exploración de timeframe/SL-TP hecha sobre 1m de historicos/):
# - BTC/SOL: config original 4h, SL2x/TP3x, niveles activo.
# - ETH: 1h con SL1.5x/TP2x supera a 4h en completo/in-sample/OOS a la vez,
#   con win rate ~50% (vs 20-28% de las variantes de TP muy estirado en 4h) y
#   baja concentración en pocos trades grandes. Niveles se probó y no aporta
#   para ETH en OOS, así que queda desactivado para esa moneda.
CONFIG_COINS = {
    "BTC": {"tf_senal": "4h", "sl_atr_mult": 2.0, "tp_atr_mult": 3.0,
            "ema_dia_long": 20, "ema_dia_short": 50, "niveles_filtro": True},
    "ETH": {"tf_senal": "1h", "sl_atr_mult": 1.5, "tp_atr_mult": 2.0,
            "ema_dia_long": 20, "ema_dia_short": 50, "niveles_filtro": False},
    "SOL": {"tf_senal": "4h", "sl_atr_mult": 2.0, "tp_atr_mult": 3.0,
            "ema_dia_long": 20, "ema_dia_short": 50, "niveles_filtro": True},
}
COINS = tuple(CONFIG_COINS)

NIVELES_K = 5
NIVELES_TOQUES_MIN = 3
NIVELES_TOLERANCIA_ATR = 0.15

CAPITAL_TOTAL = 100.0
RIESGO_PCT = 0.05
LEVERAGE = 10.0
FEE_TAKER = 0.0004

VELAS_MINIMAS = BB_PERIOD + ATR_PERIOD + 5


def _log(msg):
    linea = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {msg}"
    print(linea, flush=True)
    with open(ARCHIVO_LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def _avisar(msg):
    _log(msg.replace("\n", " | "))
    if avisos:
        avisos.enviar(msg)


def _ruta_velas(coin, tf):
    return DIR_VELAS / coin / f"bitget_{coin}_{tf}_futuros.csv"


def _ruta_niveles(coin, tf):
    return DIR_NIVELES_JSON / f"nivel_{coin}_{tf}_futuros_k{NIVELES_K}_toques{NIVELES_TOQUES_MIN}.json"


def cargar_velas(coin, tf, n=500):
    ruta = _ruta_velas(coin, tf)
    if not ruta.exists():
        return None
    df = pd.read_csv(ruta)
    df["fecha_utc"] = pd.to_datetime(df["fecha_utc"], utc=True)
    return df.tail(n).reset_index(drop=True)


def cargar_niveles(coin, tf):
    ruta = _ruta_niveles(coin, tf)
    if not ruta.exists():
        return None
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def cargar_ema_diaria(coin, periodo):
    df = cargar_velas(coin, "1d", n=periodo * 5 + 30)
    if df is None or len(df) < periodo:
        return None, None
    # Solo velas ya cerradas: la ultima fila del CSV de velas_bit.py siempre
    # es una vela cerrada (poner_al_dia solo escribe una vez sellada).
    ema = df["close"].ewm(span=periodo, adjust=False).mean()
    return float(ema.iloc[-1]), df["fecha_utc"].iloc[-1]


def leer_estado():
    if ARCHIVO_ESTADO.exists():
        with open(ARCHIVO_ESTADO, encoding="utf-8") as f:
            return json.load(f)
    return {
        coin: {
            "capital_disponible": CAPITAL_TOTAL,
            "posiciones": [],
            "trades_cerrados": [],
            "ultima_vela_procesada": None,
        }
        for coin in COINS
    }


def guardar_estado(estado):
    tmp = ARCHIVO_ESTADO.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2, default=str)
    tmp.replace(ARCHIVO_ESTADO)


def cerrar_posicion(estado_coin, pos, precio_salida, fecha, razon):
    cantidad = pos["nominal"] / pos["entrada"]
    bruto = (precio_salida - pos["entrada"]) * cantidad if pos["tipo"] == "LONG" else (pos["entrada"] - precio_salida) * cantidad
    fee_salida = abs(cantidad * precio_salida) * FEE_TAKER
    ganancia_neta = bruto - fee_salida - pos["fee_entrada"]
    estado_coin["capital_disponible"] += pos["margen"] + bruto - fee_salida
    estado_coin["trades_cerrados"].append({
        "entrada_fecha": pos["fecha"], "salida_fecha": str(fecha), "tipo": pos["tipo"],
        "entrada": pos["entrada"], "salida": precio_salida, "ganancia_neta": round(ganancia_neta, 4),
        "razon": razon,
    })
    _avisar(f"SALIDA {razon} {pos['coin']}\n{pos['tipo']} | entrada {pos['entrada']:.4f} -> salida {precio_salida:.4f}"
            f"\nP&L: ${ganancia_neta:.4f} | capital: ${estado_coin['capital_disponible']:.4f}")


def abrir_posicion(estado_coin, coin, tipo, entrada, atr, fecha, sl_atr_mult, tp_atr_mult):
    margen = estado_coin["capital_disponible"] * RIESGO_PCT
    nominal = margen * LEVERAGE
    fee_entrada = nominal * FEE_TAKER
    sl_dist = atr * sl_atr_mult
    tp_dist = atr * tp_atr_mult
    sl = entrada - sl_dist if tipo == "LONG" else entrada + sl_dist
    tp = entrada + tp_dist if tipo == "LONG" else entrada - tp_dist
    estado_coin["capital_disponible"] -= margen + fee_entrada
    estado_coin["posiciones"].append({
        "coin": coin, "fecha": str(fecha), "entrada": entrada, "tipo": tipo,
        "sl_precio": sl, "tp_precio": tp, "nominal": nominal, "margen": margen,
        "fee_entrada": fee_entrada,
    })
    _avisar(f"ENTRADA {tipo} {coin}\nPrecio: {entrada:.4f} | SL: {sl:.4f} | TP: {tp:.4f}"
            f"\nCapital disponible: ${estado_coin['capital_disponible']:.4f}")


def revisar_sl_tp(estado_coin, vela):
    for pos in list(estado_coin["posiciones"]):
        long = pos["tipo"] == "LONG"
        hit_sl = vela["low"] <= pos["sl_precio"] if long else vela["high"] >= pos["sl_precio"]
        hit_tp = vela["high"] >= pos["tp_precio"] if long else vela["low"] <= pos["tp_precio"]
        if hit_sl:
            cerrar_posicion(estado_coin, pos, pos["sl_precio"], vela["fecha_utc"], "SL")
            estado_coin["posiciones"].remove(pos)
        elif hit_tp:
            cerrar_posicion(estado_coin, pos, pos["tp_precio"], vela["fecha_utc"], "TP")
            estado_coin["posiciones"].remove(pos)


def procesar_coin(coin, estado):
    cfg = CONFIG_COINS[coin]
    tf = cfg["tf_senal"]
    estado_coin = estado[coin]
    df = cargar_velas(coin, tf, n=max(VELAS_MINIMAS, 300))
    if df is None or len(df) < VELAS_MINIMAS:
        _log(f"{coin}: faltan velas de {tf} ({0 if df is None else len(df)}/{VELAS_MINIMAS})")
        return

    banda_sup, banda_inf = calcular_bollinger(df, BB_PERIOD, BB_STD)
    df["bb_sup"] = banda_sup
    df["bb_inf"] = banda_inf
    df["atr"] = calcular_atr(df, ATR_PERIOD)

    # Retoma desde la vela siguiente a la última procesada, para no saltarse
    # cierres de SL/TP ni señales de velas perdidas durante una caída del proceso.
    ts_previa = estado_coin.get("ultima_vela_procesada")
    coincide = df.index[df["fecha_utc"].astype(str) == ts_previa] if ts_previa else []
    if ts_previa and not len(coincide):
        _log(f"{coin}: vela previa ({ts_previa}) fuera de la ventana cargada — "
             f"se retoma solo desde la última vela, sin recuperar el hueco")
    inicio = int(coincide[0]) + 1 if len(coincide) else len(df) - 1

    for i in range(max(inicio, 1), len(df)):
        vela = df.iloc[i]
        anterior = df.iloc[i - 1]

        # Cierres de SL/TP contra cada vela pendiente, en orden, siempre.
        revisar_sl_tp(estado_coin, vela)
        estado_coin["ultima_vela_procesada"] = str(vela["fecha_utc"])

        if np.isnan(anterior["bb_sup"]) or np.isnan(vela["atr"]):
            continue

        cruzo_arriba = anterior["close"] <= anterior["bb_sup"] and vela["close"] > vela["bb_sup"]
        cruzo_abajo = anterior["close"] >= anterior["bb_inf"] and vela["close"] < vela["bb_inf"]
        if not (cruzo_arriba or cruzo_abajo):
            continue
        tipo = "LONG" if cruzo_arriba else "SHORT"

        ema_long, _ = cargar_ema_diaria(coin, cfg["ema_dia_long"])
        ema_short, _ = cargar_ema_diaria(coin, cfg["ema_dia_short"])
        precio = float(vela["close"])
        if tipo == "LONG" and ema_long is not None and precio < ema_long:
            _log(f"{coin}: señal LONG bloqueada por filtro EMA-diaria({cfg['ema_dia_long']}) — precio {precio:.4f} < EMA {ema_long:.4f}")
            continue
        if tipo == "SHORT" and ema_short is not None and precio > ema_short:
            _log(f"{coin}: señal SHORT bloqueada por filtro EMA-diaria({cfg['ema_dia_short']}) — precio {precio:.4f} > EMA {ema_short:.4f}")
            continue

        if cfg["niveles_filtro"]:
            snap = cargar_niveles(coin, tf)
            if snap and _nivel_bloquea(snap["niveles"], snap["atr_actual"], tipo, precio, NIVELES_TOLERANCIA_ATR):
                _log(f"{coin}: señal {tipo} bloqueada por filtro de niveles")
                continue

        # Stop-and-reverse: señal opuesta cierra lo que hubiera abierto.
        for pos in list(estado_coin["posiciones"]):
            if pos["tipo"] != tipo:
                cerrar_posicion(estado_coin, pos, precio, vela["fecha_utc"], "FLIP")
                estado_coin["posiciones"].remove(pos)

        if estado_coin["posiciones"] or estado_coin["capital_disponible"] <= 0:
            continue

        abrir_posicion(estado_coin, coin, tipo, precio, float(vela["atr"]), vela["fecha_utc"],
                       cfg["sl_atr_mult"], cfg["tp_atr_mult"])


def ciclo(estado):
    for coin in COINS:
        try:
            procesar_coin(coin, estado)
        except Exception as e:
            _log(f"{coin}: ERROR {type(e).__name__}: {e}")
    guardar_estado(estado)


def main():
    p = argparse.ArgumentParser(description="Paper trading en vivo: Bollinger + EMA-diaria + niveles, config por moneda")
    p.add_argument("--loop", type=float, default=None, help="segundos entre chequeos (modo demonio)")
    p.add_argument("--una-vez", action="store_true", help="un solo chequeo y termina")
    args = p.parse_args()

    if not args.una_vez and args.loop is None:
        print("Requiere --loop <seg> o --una-vez")
        return 1

    estado = leer_estado()
    _log("Iniciando vivo_multi (PAPER, sin ordenes reales):")
    for coin in COINS:
        cfg = CONFIG_COINS[coin]
        niveles_txt = f"k={NIVELES_K}" if cfg["niveles_filtro"] else "off"
        _log(f"  {coin}: tf {cfg['tf_senal']} | EMA-diaria {cfg['ema_dia_long']}/{cfg['ema_dia_short']} | "
             f"niveles {niveles_txt} | SL {cfg['sl_atr_mult']}x TP {cfg['tp_atr_mult']}x")

    if args.una_vez:
        ciclo(estado)
        for coin in COINS:
            e = estado[coin]
            print(f"{coin}: capital ${e['capital_disponible']:.4f} | "
                  f"posiciones abiertas {len(e['posiciones'])} | trades cerrados {len(e['trades_cerrados'])}")
        return 0

    try:
        while True:
            ciclo(estado)
            time.sleep(args.loop)
    except KeyboardInterrupt:
        _log("Detenido por el usuario")
    return 0


if __name__ == "__main__":
    sys.exit(main())
