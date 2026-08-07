# ---------------------------------------------------------------
# grabador_libro.py - Graba en CSV lo UNICO que no se puede reconstruir
# despues a partir de velas: el libro de ordenes COMPLETO (resumen +
# crudo nivel a nivel), el open interest, el funding rate, el trade flow
# (CVD) y el long/short ratio agregado del mercado.
#
# Velas (y por tanto RSI/EMA/ATR/todo lo que sale de ellas) SI tienen
# historico en el exchange - un reinicio de monitor.py no las pierde, las
# vuelve a pedir. El libro (liquidez en reposo) y el open interest NO tienen
# historico en NINGUN exchange (ni Bitget ni Binance) - si no se graban EN
# VIVO en el momento, se pierden para siempre (ver anotaciones.md
# 2026-08-03: "lo que SI carece de historico y obliga a grabar en vivo es
# solo el libro de ordenes"). El funding y el long/short ratio SI tienen algo
# de historico propio en Bitget (~90d y ~30h vistos respectivamente) pero se
# graban aqui igual, emparejados con el resto, para no depender de acordarse
# de bajarlos aparte despues.
#
# CVD (Cumulative Volume Delta): suma acumulada de (volumen comprador -
# volumen vendedor) de trades YA EJECUTADOS, por el lado del AGRESOR (quien
# cruzo el spread, no quien puso la orden pasiva) - distinto del libro, que
# es liquidez EN REPOSO todavia sin ejecutar. Un CVD subiendo con precio
# lateral/bajando (o viceversa) es la divergencia clasica que se usa como
# señal de agotamiento. Arranca en 0 al iniciar este proceso (no hay forma
# de reconstruir el CVD de antes de que empezara a grabar).
#
# El libro se graba DOBLE: el resumen (bid/ask/spread/mid/microprecio/
# imbalance, para leer rapido sin parsear JSON) Y el crudo completo
# (bids_json/asks_json, hasta --profundidad niveles) - el resumen fija
# niveles=10 para el imbalance a proposito; el crudo permite recalcularlo
# despues con otro 'niveles', u otra metrica que ni se nos ocurrio todavia
# (tamaño de un muro a un precio concreto, por ejemplo). Es la misma logica
# de "grabar lo que no se puede reconstruir" aplicada al detalle, no solo al
# resumen.
#
# Proceso SEPARADO de monitor.py (2026-08-06) a proposito: monitor.py se
# reinicia varias veces al dia (despliegues, ajustes de parametros - ver
# anotaciones.md), y cada reinicio cortaba esta serie sin motivo real, ya
# que grabarla no depende de que rama/señal/posicion este corriendo. Este
# proceso no tiene ninguna logica de trading, solo escribe - no hay motivo
# para tocarlo con la frecuencia de monitor.py.
#
# Ademas, al arrancar empieza a bajar velas de Bitget en TIMEFRAMES_VELAS
# para cada moneda - no porque las velas necesiten grabarse en vivo (si
# tienen historico, ver arriba), sino para tener la foto completa (libro+OI
# en vivo + velas de varios TF) con la que despues cruzar y hacer pruebas en
# frio, sin tener que acordarse de correr descargar_bit.py aparte. Arranca
# SOLO desde el momento en que arranca este proceso (desde=0, no el
# historico completo desde 2019) - el historico profundo para backtests de
# otro tipo ya lo cubre descargar_bin.py/Binance por separado. Usa
# actualizar() (resume incremental, mismo patron que descargar_bin.py - ver
# anotaciones.md), asi que se puede refrescar seguido (--velas-cada) sin
# re-bajar nada de lo ya guardado.
#
# Uso (desde la raiz del repo):
#   python herramientas/grabador_libro.py [coin[,coin2,...]] [--cada 15]
#                             [--profundidad 20] [--velas-cada 3600]
#                             [--funding-cada 300] [--libro-crudo-cada 60]
#                             [--ls-ratio-cada 300]
#   coin por defecto: btc,eth
#
# Ejemplos:
#   python herramientas/grabador_libro.py
#   python herramientas/grabador_libro.py btc,eth
#   python herramientas/grabador_libro.py btc,eth,sol --cada 30 --velas-cada 1800
# ---------------------------------------------------------------

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import datos, flujo
from herramientas.descargar_bit import actualizar as _actualizar_velas, DIR_LIBRO

TIMEFRAMES_VELAS = ["1m", "5m", "15m", "30m", "1h", "4h"]

CAMPOS_CSV = [
    "timestamp_ms", "fecha_utc", "coin",
    "bid", "ask", "spread_bps", "mid", "microprecio",
    "imbalance", "imbalance_niveles",
    "open_interest", "funding_rate_pct", "long_short_ratio",
    "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd",
    "bids_json", "asks_json",
]


def _archivo(coins):
    """flujo_<fecha_UTC_arranque>_<monedas>.csv en herramientas/libro/ (mismo
    sitio que los historico_*_bitget.csv que baja descargar_bit.py) - mismo
    patron de nombre que monitor.py (ver registro/csv_monitor.py), ya
    gitignored (flujo_*.csv)."""
    os.makedirs(DIR_LIBRO, exist_ok=True)
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    monedas = "-".join(c.upper() for c in coins)
    return os.path.join(DIR_LIBRO, f"flujo_{fecha}_{monedas}.csv")


def _ruta_compatible(ruta):
    """Si ya existe con OTRA cabecera, no se reescribe encima - se abre
    _v2/_v3... (mismo patron que registro/csv_monitor.py._ruta_compatible)."""
    if not os.path.exists(ruta):
        return ruta
    with open(ruta, newline="", encoding="utf-8") as f:
        primera = f.readline().strip()
    if primera == ",".join(CAMPOS_CSV):
        return ruta
    base, ext = os.path.splitext(ruta)
    n = 2
    while os.path.exists(f"{base}_v{n}{ext}"):
        n += 1
    nueva = f"{base}_v{n}{ext}"
    print(f"(aviso) {ruta} lo escribio otra version del grabador (cabecera distinta).")
    print(f"        Para no corromperlo, esta sesion va a {nueva}")
    return nueva


def _funding_pct(coin, simbolo, cache, funding_cada):
    """Funding rate en %, refrescado como mucho cada 'funding_cada' segundos
    (Bitget solo lo asienta cada 8h - pedirlo en cada vuelta es rate-limit
    tirado a la basura). 'cache' es {coin: (valor_pct_o_None, ultimo_ts)},
    se muta in-place; devuelve el valor CACHEADO (o el recien pedido si
    tocaba refrescar). Un fallo de red conserva el valor viejo en vez de
    dejarlo vacio - el funding no cambia de golpe entre refrescos."""
    valor_previo, ultimo = cache.get(coin, (None, 0.0))
    ahora = time.monotonic()
    if ahora - ultimo < funding_cada:
        return valor_previo
    try:
        r = datos.funding_rate(simbolo)
        valor = r * 100 if r is not None else valor_previo
    except Exception as e:
        print(f"  (aviso) funding_rate {coin}: {e}")
        valor = valor_previo
    cache[coin] = (valor, ahora)
    return valor


def _ls_ratio(coin, simbolo, cache, ls_ratio_cada):
    """Long/short ratio, refrescado como mucho cada 'ls_ratio_cada' segundos
    (el dato de Bitget es de resolucion horaria - pedirlo cada vuelta no
    aporta nada). Mismo patron de cache que _funding_pct."""
    valor_previo, ultimo = cache.get(coin, (None, 0.0))
    ahora = time.monotonic()
    if ahora - ultimo < ls_ratio_cada:
        return valor_previo
    try:
        valor = datos.long_short_ratio(simbolo)
        if valor is None:
            valor = valor_previo
    except Exception as e:
        print(f"  (aviso) long_short_ratio {coin}: {e}")
        valor = valor_previo
    cache[coin] = (valor, ahora)
    return valor


def _trade_flow(coin, simbolo, cache):
    """Trades ejecutados desde la ultima vuelta (lado agresor buy/sell) y el
    CVD acumulado DESDE QUE ARRANCO este proceso (no hay forma de reconstruir
    el CVD de antes). 'cache' es {coin: {cursor, cvd, iniciado}}, mutada
    in-place. Devuelve (n_trades, vol_buy, vol_sell, cvd).

    Primera vuelta por moneda: solo fija el cursor en el ultimo trade visto,
    sin sumar nada al CVD - si no, los <=500 trades del primer fetch (que
    pueden ser de minutos u horas atras) se contarian de golpe como si
    hubieran pasado en este instante, un salto de CVD que no fue real."""
    estado = cache.setdefault(coin, {"cursor": None, "cvd": 0.0, "iniciado": False})
    try:
        trades = datos.trades(simbolo, desde=estado["cursor"], limite=500)
    except Exception as e:
        print(f"  (aviso) trades {coin}: {e}")
        return 0, 0.0, 0.0, estado["cvd"]

    if not estado["iniciado"]:
        estado["iniciado"] = True
        if trades:
            estado["cursor"] = trades[-1].get("timestamp")
        return 0, 0.0, 0.0, estado["cvd"]

    n = 0
    vb = vs = 0.0
    ultimo = estado["cursor"]
    for t in trades:
        tt = t.get("timestamp")
        if ultimo is not None and tt is not None and tt <= ultimo:
            continue  # ya contado en una vuelta anterior
        n += 1
        amt = t.get("amount") or 0.0
        lado = t.get("side")
        if lado == "buy":
            vb += amt
        elif lado == "sell":
            vs += amt
        if tt is not None and (ultimo is None or tt > ultimo):
            ultimo = tt
    estado["cursor"] = ultimo
    estado["cvd"] += vb - vs
    return n, vb, vs, estado["cvd"]


def _toca_libro_crudo(coin, cache, libro_crudo_cada):
    """True si toca grabar bids_json/asks_json esta vuelta (como mucho cada
    'libro_crudo_cada' segundos - ver cabecera del archivo: el resumen ya
    sale cada --cada, el crudo completo pesa ~25x mas por fila y no hace
    falta esa frecuencia). Actualiza 'cache' (mutada in-place) si toca."""
    ultimo = cache.get(coin, 0.0)
    ahora = time.monotonic()
    if ahora - ultimo < libro_crudo_cada:
        return False
    cache[coin] = ahora
    return True


def _fila(coin, simbolo, profundidad, funding_cache, funding_cada,
          libro_crudo_cache, libro_crudo_cada, ls_ratio_cache, ls_ratio_cada,
          trade_cache):
    """Una lectura de libro+OI+funding+trades+long/short. Un fallo de API en
    cualquiera de ellos deja ese campo vacio en vez de tumbar la vuelta - la
    siguiente lectura, unos segundos despues, puede volver a tener el dato."""
    try:
        libro = datos.libro(simbolo, depth=profundidad)
    except Exception as e:
        print(f"  (aviso) libro {coin}: {e}")
        libro = None
    try:
        oi = datos.open_interest(simbolo)
    except Exception as e:
        print(f"  (aviso) open_interest {coin}: {e}")
        oi = None
    funding = _funding_pct(coin, simbolo, funding_cache, funding_cada)
    ls_ratio = _ls_ratio(coin, simbolo, ls_ratio_cache, ls_ratio_cada)
    n_trades, vol_buy, vol_sell, cvd = _trade_flow(coin, simbolo, trade_cache)

    bids = libro.get("bids") if libro else None
    asks = libro.get("asks") if libro else None
    bid = bids[0][0] if bids else None
    ask = asks[0][0] if asks else None
    ahora = datetime.now(timezone.utc)
    grabar_crudo = _toca_libro_crudo(coin, libro_crudo_cache, libro_crudo_cada)

    return {
        "timestamp_ms": int(ahora.timestamp() * 1000),
        "fecha_utc": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "coin": coin,
        "bid": bid if bid is not None else "",
        "ask": ask if ask is not None else "",
        "spread_bps": flujo.spread_bps(libro) if libro else "",
        "mid": flujo.mid(libro) if libro else "",
        "microprecio": flujo.microprecio(libro) if libro else "",
        "imbalance": flujo.imbalance(libro, niveles=10) if libro else "",
        "imbalance_niveles": 10,
        "open_interest": oi if oi is not None else "",
        "funding_rate_pct": funding if funding is not None else "",
        "long_short_ratio": ls_ratio if ls_ratio is not None else "",
        "n_trades": n_trades,
        "vol_buy": round(vol_buy, 6),
        "vol_sell": round(vol_sell, 6),
        "delta_vol": round(vol_buy - vol_sell, 6),
        "cvd": round(cvd, 6),
        "bids_json": json.dumps(bids) if (grabar_crudo and bids) else "",
        "asks_json": json.dumps(asks) if (grabar_crudo and asks) else "",
    }


def _foto_completa_velas(coins):
    """Pone al dia el historico de Bitget en TIMEFRAMES_VELAS para cada
    moneda (solo baja/anexa lo que falte - ver descargar_bit.actualizar()).
    desde=0 solo importa la PRIMERA vez (sin fichero previo): arranca desde
    ahora, no desde el historico completo - ver comentario de cabecera.
    Un fallo puntual (red, simbolo) no debe impedir que arranque o siga la
    grabacion del libro/OI - solo se avisa y se sigue con el resto."""
    print(f"Foto completa de velas ({', '.join(TIMEFRAMES_VELAS)}) ...")
    for coin in coins:
        for tf in TIMEFRAMES_VELAS:
            try:
                _actualizar_velas(coin, tf, desde=0)
            except Exception as e:
                print(f"  (aviso) velas {coin} {tf}: {e}")


def main():
    args = sys.argv[1:]
    if args and not args[0].startswith("--"):
        coins = [c.strip().upper() for c in args[0].split(",")]
        args = args[1:]
    else:
        coins = ["BTC", "ETH"]

    cada = 15.0
    profundidad = 20
    velas_cada = 3600.0
    funding_cada = 300.0
    libro_crudo_cada = 60.0
    ls_ratio_cada = 300.0
    i = 0
    while i < len(args):
        if args[i] == "--cada":
            i += 1
            cada = float(args[i])
        elif args[i] == "--profundidad":
            i += 1
            profundidad = int(args[i])
        elif args[i] == "--velas-cada":
            i += 1
            velas_cada = float(args[i])
        elif args[i] == "--funding-cada":
            i += 1
            funding_cada = float(args[i])
        elif args[i] == "--libro-crudo-cada":
            i += 1
            libro_crudo_cada = float(args[i])
        elif args[i] == "--ls-ratio-cada":
            i += 1
            ls_ratio_cada = float(args[i])
        i += 1

    _foto_completa_velas(coins)
    ultimo_refresco_velas = time.monotonic()

    simbolos = {c: datos.normalizar_simbolo(c, "f")[0] for c in coins}
    ruta = _ruta_compatible(_archivo(coins))
    nuevo = not os.path.exists(ruta)
    arch = open(ruta, "a", newline="")
    writer = csv.DictWriter(arch, fieldnames=CAMPOS_CSV)
    if nuevo:
        writer.writeheader()
        arch.flush()

    print(f"Grabando libro/OI/funding/trades de {', '.join(coins)} cada {cada:.0f}s "
          f"(profundidad {profundidad}) -> {ruta}")
    print(f"Velas refrescadas cada {velas_cada:.0f}s. Funding cada {funding_cada:.0f}s. "
          f"Long/short ratio cada {ls_ratio_cada:.0f}s. "
          f"Libro crudo (bids_json/asks_json) cada {libro_crudo_cada:.0f}s.")
    print("CVD arranca en 0 (acumulado desde que arranca este proceso).")
    print("Ctrl+C para parar.")
    funding_cache = {}
    libro_crudo_cache = {}
    ls_ratio_cache = {}
    trade_cache = {}
    try:
        while True:
            for coin in coins:
                fila = _fila(coin, simbolos[coin], profundidad, funding_cache, funding_cada,
                             libro_crudo_cache, libro_crudo_cada, ls_ratio_cache, ls_ratio_cada,
                             trade_cache)
                writer.writerow(fila)
                arch.flush()
            if time.monotonic() - ultimo_refresco_velas >= velas_cada:
                _foto_completa_velas(coins)
                ultimo_refresco_velas = time.monotonic()
            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")
    finally:
        arch.close()


if __name__ == "__main__":
    main()
