# ---------------------------------------------------------------
# grabador_libro.py
#
# Graba en CSV: libro (bids/asks JSON completo), open interest, funding rate,
# trade flow (CVD), long/short ratio. Datos irrecuperables no disponibles en
# históricos del exchange.
#
# Uso:
#   python libro/grabador_libro.py [monedas] [opciones]
#   Monedas: btc,eth,sol (default: btc,eth)
#   Opciones:
#     --cada N           segundos entre capturas (default: 15)
#     --profundidad N    niveles del libro (default: 20)
#     --funding-cada N   segundos entre updates funding (default: 300)
#     --ls-ratio-cada N  segundos entre updates L/S ratio (default: 300)
#
# Ejemplos:
#   python libro/grabador_libro.py
#   python libro/grabador_libro.py btc,eth --cada 10
#   python libro/grabador_libro.py btc,eth,icp --profundidad 30 --cada 5
#
# CSV contiene:
#   timestamp_local_ms: cuando se capturó (máquina local)
#   timestamp_exchange_ms: cuando se capturó (exchange, si disponible)
#   estado: 'ok' o 'error_libro' si fallo
#   bids_json, asks_json: libro completo en cada fila
# ---------------------------------------------------------------

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
import platform

# File locking multiplataforma
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import datos, flujo

DIR_BASE = os.path.dirname(os.path.abspath(__file__))
DIR_DATOS = os.path.join(DIR_BASE, "datos")
DIR_LOGS = os.path.join(DIR_BASE, "logs")
DIR_TEST = os.path.join(DIR_BASE, "test")

CAMPOS_CSV = [
    "timestamp_local_ms", "fecha_utc", "timestamp_exchange_ms", "estado", "coin",
    "imbalance", "imbalance_niveles",
    "open_interest", "funding_rate_pct", "long_short_ratio",
    "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd",
    "bids_json", "asks_json",
]


def _crear_estructura():
    """Crea carpetas necesarias y archivos .gitkeep si no existen."""
    directorios = [DIR_DATOS, DIR_LOGS, DIR_TEST]
    for directorio in directorios:
        os.makedirs(directorio, exist_ok=True)
        gitkeep = os.path.join(directorio, ".gitkeep")
        if not os.path.exists(gitkeep):
            open(gitkeep, "a").close()


def _configurar_logging(coins):
    """Configura logging para escribir en logs/libro_MONEDAS.log"""
    monedas_str = "-".join(c.upper() for c in coins)
    log_file = os.path.join(DIR_LOGS, f"libro_{monedas_str}.log")
    
    logger = logging.getLogger("grabador_libro")
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        logger.handlers.clear()
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', 
                                  datefmt='%Y-%m-%d %H:%M:%S')
    
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    return logger


def _archivo(coins):
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    monedas = "-".join(c.upper() for c in coins)
    return os.path.join(DIR_DATOS, f"libro_{fecha}_{monedas}.csv")


def _ruta_compatible(ruta, logger):
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
    logger.warning(f"{ruta} cabecera distinta, usando {nueva}")
    return nueva


def _funding_pct(coin, simbolo, cache, funding_cada, logger):
    valor_previo, ultimo = cache.get(coin, (None, 0.0))
    ahora = time.monotonic()
    if ahora - ultimo < funding_cada:
        return valor_previo
    try:
        r = datos.funding_rate(simbolo)
        valor = r * 100 if r is not None else valor_previo
    except Exception as e:
        logger.warning(f"funding_rate {coin}: {e}")
        valor = valor_previo
    cache[coin] = (valor, ahora)
    return valor


def _ls_ratio(coin, simbolo, cache, ls_ratio_cada, logger):
    valor_previo, ultimo = cache.get(coin, (None, 0.0))
    ahora = time.monotonic()
    if ahora - ultimo < ls_ratio_cada:
        return valor_previo
    try:
        valor = datos.long_short_ratio(simbolo)
        if valor is None:
            if valor_previo is not None:
                logger.info(f"long_short_ratio {coin}: sin datos, usando previo={valor_previo}")
                valor = valor_previo
            else:
                logger.info(f"long_short_ratio {coin}: sin datos disponibles")
                valor = "-"
    except Exception as e:
        logger.warning(f"long_short_ratio {coin}: {e}")
        valor = valor_previo if valor_previo is not None else "-"
    cache[coin] = (valor, ahora)
    return valor


def _trade_flow(coin, simbolo, cache, logger):
    estado = cache.setdefault(coin, {"cursor": None, "cvd": 0.0, "iniciado": False})
    try:
        trades = datos.trades(simbolo, desde=estado["cursor"], limite=500)
    except Exception as e:
        logger.warning(f"trades {coin}: {e}")
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
            continue
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


def _aplicar_lock(arch, logger):
    """Aplica file lock multiplataforma (Windows/Linux) para evitar race conditions."""
    try:
        if platform.system() == "Windows":
            msvcrt.locking(arch.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(arch.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        logger.error("Otra instancia en ejecución. Saliendo.")
        arch.close()
        sys.exit(1)


def _liberar_lock(arch):
    """Libera file lock multiplataforma."""
    try:
        if platform.system() == "Windows":
            msvcrt.locking(arch.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(arch.fileno(), fcntl.LOCK_UN)
    except (IOError, OSError):
        pass


def _fila(coin, simbolo, profundidad, funding_cache, funding_cada,
          ls_ratio_cache, ls_ratio_cada, trade_cache, logger):
    ahora = datetime.now(timezone.utc)
    timestamp_local_ms = int(ahora.timestamp() * 1000)
    estado = "ok"
    timestamp_exchange_ms = None

    try:
        libro = datos.libro(simbolo, depth=profundidad)
    except Exception as e:
        logger.warning(f"libro {coin}: {e}")
        libro = None
        estado = "error_libro"

    try:
        oi = datos.open_interest(simbolo)
    except Exception as e:
        logger.warning(f"open_interest {coin}: {e}")
        oi = None

    funding = _funding_pct(coin, simbolo, funding_cache, funding_cada, logger)
    ls_ratio = _ls_ratio(coin, simbolo, ls_ratio_cache, ls_ratio_cada, logger)
    n_trades, vol_buy, vol_sell, cvd = _trade_flow(coin, simbolo, trade_cache, logger)

    bids = None
    asks = None
    if libro:
        bids = libro.get("bids")
        asks = libro.get("asks")
        timestamp_exchange_ms = libro.get("timestamp")

    return {
        "timestamp_local_ms": timestamp_local_ms,
        "fecha_utc": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_exchange_ms": timestamp_exchange_ms if timestamp_exchange_ms is not None else "",
        "estado": estado,
        "coin": coin,
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
        "bids_json": json.dumps(bids) if bids else "",
        "asks_json": json.dumps(asks) if asks else "",
    }


def main():
    _crear_estructura()

    args = sys.argv[1:]
    if args and not args[0].startswith("--"):
        coins = [c.strip().upper() for c in args[0].split(",")]
        args = args[1:]
    else:
        coins = ["BTC", "ETH"]
    
    logger = _configurar_logging(coins)

    cada = 15.0
    profundidad = 20
    funding_cada = 300.0
    ls_ratio_cada = 300.0
    i = 0
    while i < len(args):
        if args[i] == "--cada":
            i += 1
            cada = float(args[i])
        elif args[i] == "--profundidad":
            i += 1
            profundidad = int(args[i])
        elif args[i] == "--funding-cada":
            i += 1
            funding_cada = float(args[i])
        elif args[i] == "--ls-ratio-cada":
            i += 1
            ls_ratio_cada = float(args[i])
        i += 1

    simbolos = {c: datos.normalizar_simbolo(c, "f")[0] for c in coins}
    ruta = _ruta_compatible(_archivo(coins), logger)
    nuevo = not os.path.exists(ruta)
    arch = open(ruta, "a", newline="")

    _aplicar_lock(arch, logger)

    writer = csv.DictWriter(arch, fieldnames=CAMPOS_CSV)
    if nuevo:
        writer.writeheader()
        arch.flush()

    logger.info(f"Grabando {', '.join(coins)} cada {cada:.0f}s (profundidad {profundidad}) -> {ruta}")
    logger.info(f"Funding cada {funding_cada:.0f}s, long/short ratio cada {ls_ratio_cada:.0f}s")
    funding_cache = {}
    ls_ratio_cache = {}
    trade_cache = {}
    try:
        while True:
            for coin in coins:
                fila = _fila(coin, simbolos[coin], profundidad, funding_cache, funding_cada,
                             ls_ratio_cache, ls_ratio_cada, trade_cache, logger)
                writer.writerow(fila)
                arch.flush()
            time.sleep(cada)
    except KeyboardInterrupt:
        logger.info("Parado por el usuario.")
    finally:
        _liberar_lock(arch)
        arch.close()


if __name__ == "__main__":
    main()
