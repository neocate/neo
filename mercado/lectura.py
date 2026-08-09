# ---------------------------------------------------------------
#  mercado/lectura.py - Foto completa del mercado para una moneda: velas,
#  precio, libro, funding, open interest, CVD y las señales de
#  estrategia/senales ya aplicadas - todo lo que necesita una vuelta de
#  monitor.py.
#
#  Extraido de monitor.py el 2026-08-04 (ver estrategia/senales.py para el
#  motivo).
#
#  2026-08-1x: se quito _velas_btc (solo la usaba FiltroBTC, eliminado por
#  empeorar sin excepcion en backtest - ver estrategia/filtros.py).
#  open_interest/cvd se probaron leyendo flujo_*.csv (grabador_libro.py) y se
#  revirtio: ese proceso corre APARTE de monitor.py y su continuidad en
#  produccion no esta decidida - monitor.py no debe depender de datos de un
#  proceso externo que hoy solo existen para pruebas. En su lugar, igual que
#  ya hacia _funding_pct/_mejor_bid_ask, se piden con la MISMA API que el
#  resto de leer() (datos.open_interest/datos.trades) y se acumula la serie
#  en memoria de ESTE proceso - un reinicio de monitor.py la vacia (el
#  filtro se abstiene hasta juntar --ventana lecturas de nuevo), mismo
#  trade-off que ya acepta el resto de leer(), sin depender de nada externo.
# ---------------------------------------------------------------

from datetime import datetime, timezone

from mercado import datos, indicadores
from estrategia.senales import senales, _tendencia


def _cambio_24h(simbolo):
    """% de cambio en las ultimas 24h usando velas de 1h (25 velas)."""
    try:
        v = datos.velas(simbolo, "1h", 25)
        if len(v) >= 25:
            return (v[-1][4] - v[-25][4]) / v[-25][4] * 100
    except Exception:
        pass
    return None


def _funding_pct(simbolo):
    """Funding rate actual del contrato, en % (ctx['funding_pct'] para
    FiltroFunding). None si falla o el simbolo no es perpetuo - un fallo
    aqui no puede tumbar la vuelta."""
    try:
        r = datos.funding_rate(simbolo)
        return r * 100 if r is not None else None
    except Exception:
        return None


# {simbolo: [(ts_ms, oi), ...]} - serie de OI acumulada en memoria de ESTE
# proceso (ver nota de cabecera). Se resetea con cada reinicio de monitor.py.
_CACHE_OI = {}
_VENTANA_SERIE_SEG = 6 * 3600  # de sobra para cualquier --ventana x --tf razonable


def _open_interest_serie(coin, simbolo, cfg, velas):
    """Serie de open_interest reciente para FiltroOpenInterest. Bitget/ccxt
    no da historial (fetch_open_interest solo trae el valor INSTANTANEO,
    ver mercado/datos.py:open_interest) - se pide una vez por vuelta, igual
    que _funding_pct, y se va acumulando aqui. Con --ventana=30 y --cada por
    defecto hacen falta 30 vueltas (varias horas) para que el filtro deje de
    abstenerse - no hay atajo posible sin un proceso que lo grabe aparte."""
    try:
        oi = datos.open_interest(simbolo)
    except Exception:
        oi = None
    ahora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    serie = _CACHE_OI.setdefault(simbolo, [])
    if oi is not None:
        serie.append((ahora_ms, oi))
    limite = ahora_ms - _VENTANA_SERIE_SEG * 1000
    while serie and serie[0][0] < limite:
        serie.pop(0)
    return list(serie)


# {simbolo: {"cursor": ts_ms_o_None, "cvd": float, "serie": [(ts_ms, cvd)],
#            "iniciado": bool}} - mismo patron que
# herramientas/grabador_libro.py:_trade_flow, reimplementado aqui en vez de
# reusado para no importar ese script (es una herramienta standalone, no un
# modulo pensado para importarse) ni depender de su proceso.
_CACHE_TRADES = {}


def _cvd_serie(coin, simbolo, cfg, velas):
    """Serie de CVD (order flow: suma acumulada de volumen comprador menos
    vendedor, por el lado AGRESOR - ver mercado/flujo.py) reciente para
    FiltroCVD. Se pide una vez por vuelta con datos.trades(desde=cursor),
    igual que open_interest arriba - CVD arranca en 0 al iniciar monitor.py
    (no hay forma de reconstruir el CVD de antes de este proceso).

    Primera vuelta: solo fija el cursor, sin sumar nada - si no, los <=500
    trades del primer fetch (que pueden ser de minutos u horas atras) se
    contarian de golpe como si hubieran pasado en este instante."""
    estado = _CACHE_TRADES.setdefault(
        simbolo, {"cursor": None, "cvd": 0.0, "serie": [], "iniciado": False})
    try:
        recientes = datos.trades(simbolo, desde=estado["cursor"], limite=500)
    except Exception:
        recientes = []

    if not estado["iniciado"]:
        estado["iniciado"] = True
        if recientes:
            estado["cursor"] = recientes[-1].get("timestamp")
        return list(estado["serie"])

    ultimo = estado["cursor"]
    vb = vs = 0.0
    for t in recientes:
        tt = t.get("timestamp")
        if ultimo is not None and tt is not None and tt <= ultimo:
            continue  # ya contado en una vuelta anterior
        amt = t.get("amount") or 0.0
        if t.get("side") == "buy":
            vb += amt
        elif t.get("side") == "sell":
            vs += amt
        if tt is not None and (ultimo is None or tt > ultimo):
            ultimo = tt
    estado["cursor"] = ultimo

    if vb or vs:
        estado["cvd"] += vb - vs
        ahora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        estado["serie"].append((ahora_ms, estado["cvd"]))
        limite = ahora_ms - _VENTANA_SERIE_SEG * 1000
        while estado["serie"] and estado["serie"][0][0] < limite:
            estado["serie"].pop(0)
    return list(estado["serie"])


def _mejor_bid_ask(simbolo):
    """(bid, ask, spread_bps) del libro real, o (None, None, None) si falla.

    Se lee en CADA vuelta aunque no haya posicion: es lo que permite medir
    despues, offline, si una orden LIMITE puesta al mejor bid/ask se habria
    llenado - sin cambiar todavia como entra el monitor. Un fallo aqui no
    puede tumbar la vuelta, por eso se traga la excepcion."""
    try:
        libro = datos.libro(simbolo, depth=5)
    except Exception:
        return None, None, None
    if not libro or not libro.get("bids") or not libro.get("asks"):
        return None, None, None
    bid = libro["bids"][0][0]
    ask = libro["asks"][0][0]
    medio = (bid + ask) / 2
    spread = (ask - bid) / medio * 1e4 if medio else None
    return bid, ask, spread


def leer(coin, cfg):
    """Toma una foto del mercado de una moneda y devuelve metricas + senales."""
    simbolo, _ = datos.normalizar_simbolo(coin, "f")
    velas = datos.velas(simbolo, cfg["tf"], 200)
    precio = datos.precio(simbolo)
    bid, ask, spread_bps = _mejor_bid_ask(simbolo)
    cierres = [c[4] for c in velas]
    rsi_serie = indicadores.rsi(cierres, 14)

    funding_pct = _funding_pct(simbolo)
    oi_serie = _open_interest_serie(coin, simbolo, cfg, velas)
    cvd_serie = _cvd_serie(coin, simbolo, cfg, velas)

    n = cfg["ventana"]
    rango = velas[-(n + 1):-1]  # ultimas n velas cerradas
    max_rango = max(v[2] for v in rango) if rango else None
    min_rango = min(v[3] for v in rango) if rango else None

    sen, alto_c, bajo_c, rsi_c = senales(velas, rsi_serie, cfg)

    # ATR de la vela CERRADA: la unidad de medida de todo lo demas (stop,
    # trailing, tolerancia de niveles). Un % fijo no significa lo mismo en BTC
    # que en ICP ni en 5m que en 15m - eso ya degenero tres filtros distintos.
    serie_atr = indicadores.atr([v[2] for v in velas], [v[3] for v in velas],
                                cierres, 14)
    valor_atr = serie_atr[-2] if len(serie_atr) >= 2 else None

    # Ancho de banda de Bollinger (% del precio), de la vela CERRADA - segunda
    # medida de volatilidad, independiente del ATR (formula distinta), grabada
    # CADA vuelta para ir armando el futuro clasificador de regimen. No
    # decide nada, solo se registra (distinto de FiltroBollinger, que SI
    # decidia algo con esto y se elimino - ver estrategia/filtros.py).
    bb = indicadores.bollinger_bands(cierres[:-1], 20, 2)
    bollinger_ancho_pct = None
    if bb["superior"] and bb["superior"][-1] is not None and bb["media"][-1]:
        bollinger_ancho_pct = (bb["superior"][-1] - bb["inferior"][-1]) / bb["media"][-1] * 100

    return {
        "simbolo": simbolo,
        "precio": precio,
        "bid": bid,
        "ask": ask,
        "spread_bps": spread_bps,
        "rsi": rsi_c,
        # cierres[:-1]: igual que ATR/Bollinger arriba, sin la vela EN
        # FORMACION - si no, el EMA20/50 (y por tanto la tendencia mostrada)
        # incluye un precio que todavia puede cambiar en la proxima vuelta.
        "tendencia": _tendencia(cierres[:-1]),
        "cambio24h": _cambio_24h(simbolo),
        "max": max_rango,
        "min": min_rango,
        "senales": sen,
        "alto_c": alto_c,
        "bajo_c": bajo_c,
        "atr": valor_atr,
        "atr_pct": (valor_atr / precio * 100) if (valor_atr and precio) else None,
        "bollinger_ancho_pct": bollinger_ancho_pct,
        "velas": velas,
        "funding_pct": funding_pct,
        "cvd_serie": cvd_serie,
        "oi_serie": oi_serie,
    }


def formatear(coin, cfg, m):
    """Arma la linea de estado para consola."""
    hora = datetime.now().strftime("%H:%M:%S")
    cambio = f"{m['cambio24h']:+.2f}%" if m["cambio24h"] is not None else "?"
    rsi = f"{m['rsi']:.1f}" if m["rsi"] is not None else "?"
    p = m["precio"]
    dmax = f"{(m['max']-p)/p*100:+.1f}%" if m["max"] else "?"
    dmin = f"{(m['min']-p)/p*100:+.1f}%" if m["min"] else "?"
    return (
        f"[{hora}] {coin}  {p:.4f} USDT  ({cambio} 24h)\n"
        f"   RSI({cfg['tf']}): {rsi}   Tend: {m['tendencia']}   |   "
        f"max{cfg['ventana']}: {m['max']:.4f} ({dmax})   "
        f"min{cfg['ventana']}: {m['min']:.4f} ({dmin})"
    )
