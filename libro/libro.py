# ---------------------------------------------------------------
# libro.py
#
# Graba datos irrecuperables: CVD, volumen compra/venta, libro de órdenes,
# y ahora tambien el detalle de ejecuciones con precio y timestamp de exchange.
#
# Estos datos NO están disponibles en históricos del exchange.
#
# Autonomo a proposito: no importa nada de mercado/ ni del resto del proyecto.
# Su unica dependencia es ccxt. Ver el bloque "Acceso al exchange" mas abajo.
#
# Uso:
#   python libro/libro.py [monedas] [opciones]
#   Monedas: btc,eth,sol (default: btc,eth)
#   Opciones:
#     --cada N           segundos entre capturas (default: 15)
#                        Resolucion minima usable: 5 minutos
#                        Abajo de eso, vol_buy/vol_sell tienen error ±45%
#     --profundidad N    niveles del libro (default: 100)
#                        Bitget solo admite 1, 5, 15, 50, 100: cualquier otro
#                        valor lo ignora y devuelve 100.
#     --funding-cada N   segundos entre updates funding (default: 300)
#                        Resolucion: ≥5 min. Congelado entre updates.
#     --ls-ratio-cada N  segundos entre updates L/S ratio (default: 300)
#                        Resolucion: ≥1h. Endpoint del exchange es horario.
#
# Ejemplos:
#   python libro/libro.py
#   python libro/libro.py btc,eth --cada 300
#   python libro/libro.py btc,eth,icp --profundidad 50 --cada 60
#
# Ficheros de salida:
#
#   libro_<coin>_<mercado>_YYYYMMDD.csv
#     Snapshots del libro, CVD, volumen compra/venta cada N segundos.
#     ROTA A MEDIANOCHE UTC. El nombre es libro_eth_futuros_20260821.csv
#     si corre en ese dia UTC.
#
#     Columnas:
#       timestamp_local_ms: cuando se capturó (máquina local)
#       timestamp_exchange_ms: cuando se capturó (exchange, si disponible)
#       fecha_utc: fecha y hora UTC de la captura
#       estado: 'ok' o 'error_libro' si fallo
#       coin: moneda
#       imbalance, imbalance_niveles: desequilibrio del libro en [-1,+1]
#       open_interest, funding_rate_pct, long_short_ratio: metadata del exchange
#       n_trades, vol_buy, vol_sell, delta_vol, cvd: acumulativo desde arranque
#       session_id: timestamp de inicio de este proceso (identifica cadena CVD)
#       bids_json, asks_json: snapshot del libro completo
#
#   libro_trades_<coin>_<mercado>_YYYYMMDD.csv
#     Detalle granular de cada trade capturado, para reconstruir volume profile.
#     ROTA A MEDIANOCHE UTC como el libro.
#
#     Columnas:
#       timestamp_exchange_ms: cuando se ejecuto el trade (exchange)
#       timestamp_local_ms: cuando se capturó (máquina local)
#       fecha_utc: fecha UTC del trade
#       precio: precio de ejecución
#       volumen: tamaño del trade
#       lado: 'buy' o 'sell'
#       coin: moneda
#
# Limitaciones conocidas:
#   - CVD se reinicia a 0 en cada arranque de este proceso, sin marca historica.
#   - vol_buy/vol_sell/delta_vol tienen error ±45% a resolución <5 min,
#     porque se asignan por timestamp de poll, no de ejecución de trade.
#   - funding_rate_pct se actualiza cada 300+ s (congelado entre updates).
#   - long_short_ratio es horario (endpoint Bitget es de resolucion 1h).
#   - Se pierden ~5,9% del volumen entre polls — lag de paginacion en el exchange.
#
# Parada segura (libera locks, cierra CSVs, registra marca):
#   kill -INT <PID>   (no pkill -f, que mata todas las instancias)
#   o en Linux/Mac: Ctrl-C
#
# ---------------------------------------------------------------

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
import platform

import ccxt

# File locking multiplataforma
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

DIR_BASE = os.path.dirname(os.path.abspath(__file__))
DIR_DATOS = os.path.join(DIR_BASE, "datos")
DIR_LOGS = os.path.join(DIR_BASE, "logs")
DIR_TEST = os.path.join(DIR_BASE, "test")

CAMPOS_CSV = [
    "timestamp_local_ms", "fecha_utc", "timestamp_exchange_ms", "estado", "coin",
    "imbalance", "imbalance_niveles",
    "open_interest", "funding_rate_pct", "long_short_ratio",
    "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd", "session_id",
    "bids_json", "asks_json",
]

CAMPOS_TRADES = [
    "timestamp_exchange_ms", "timestamp_local_ms", "fecha_utc",
    "precio", "volumen", "lado", "coin",
]

# Alertas de integridad
MAX_GAP_SEGUNDOS = 30.0  # Gap máximo esperado entre registros
ALERTAR_SI_NO_TRADES = 5  # Alertar si N registros sin trades (posible downtime)

# Trades: el CVD y vol_buy/vol_sell son irrecuperables, no pueden perderse.
# Bitget (v2/mix/market/fills-history) admite hasta 1000 por peticion y el coste
# de rate limit es por peticion, no por tamaño: pedir 1000 sale igual de barato
# que pedir 500. Si aun asi vuelve una pagina llena, se pagina hasta alcanzar el
# presente en vez de descartar lo que no cupo.
TRADES_LIMITE = 1000
MAX_PAGINAS_TRADES = 10

# Bitget (v2/mix/market/merge-depth) solo admite estos niveles; con cualquier
# otro valor ignora el parametro y devuelve 100.
PROFUNDIDADES_VALIDAS = (1, 5, 15, 50, 100)


# ---------------------------------------------------------------
# Acceso al exchange (Bitget via ccxt)
#
# Deliberadamente inline y sin depender de mercado/: este grabador captura CVD
# y volumen comprador/vendedor, lo unico que NO se puede reconstruir despues
# desde ningun historico. Un refactor ajeno que le rompiera el import lo
# pararia de madrugada sin que nadie se entere hasta mirar el CSV. Su unica
# dependencia es ccxt.
#
# Los 6 endpoints que usa son publicos: funciona sin credenciales.
# ---------------------------------------------------------------

_cliente = None


def _ex_credenciales():
    """Credenciales opcionales. No hacen falta para ningun endpoint de aqui;
    se cargan si existen para no alterar el comportamiento actual."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    claves = {
        'apiKey': os.getenv('BITGET_API_KEY'),
        'secret': os.getenv('BITGET_SECRET_KEY'),
        'password': os.getenv('BITGET_PASSPHRASE'),
    }
    return {k: v for k, v in claves.items() if v}


def _ex_cliente():
    global _cliente
    if _cliente is None:
        config = {'enableRateLimit': True}
        config.update(_ex_credenciales())
        _cliente = ccxt.bitget(config)
    return _cliente


def _ex_simbolo(coin):
    """'eth' -> 'ETH/USDT:USDT' (perpetuo USDT-M)."""
    return f"{coin.upper()}/USDT:USDT"


def _ex_libro(simbolo, depth):
    ob = _ex_cliente().fetch_order_book(simbolo, limit=depth)
    return {
        'bids': ob.get('bids', []),
        'asks': ob.get('asks', []),
        'timestamp': ob.get('timestamp'),
    }


def _ex_open_interest(simbolo):
    return _ex_cliente().fetch_open_interest(simbolo).get('openInterestAmount')


def _ex_funding_rate(simbolo):
    return _ex_cliente().fetch_funding_rate(simbolo).get('fundingRate')


def _ex_trades(simbolo, desde=None, limite=TRADES_LIMITE):
    """Trades ejecutados, con lado agresor. Cada uno trae 'timestamp', 'id',
    'amount' y 'side' (minusculas): los cuatro campos de los que depende el CVD.
    El 'id' sale del tradeId de Bitget y es lo que permite distinguir trades
    que comparten el mismo milisegundo."""
    return _ex_cliente().fetch_trades(simbolo, since=desde, limit=limite)


def _ex_long_short_ratio(simbolo, timeframe='1h'):
    """Bitget no pagina de forma fiable por since/limit en este endpoint:
    devuelve una ventana fija propia, asi que se toma el ultimo punto."""
    serie = _ex_cliente().fetch_long_short_ratio_history(simbolo, timeframe=timeframe, limit=1)
    return serie[-1].get('longShortRatio') if serie else None


def _volumen(niveles, n):
    """Suma el volumen de los primeros N niveles, con casteo de seguridad."""
    total = 0.0
    for nivel in niveles[:n]:
        try:
            total += float(nivel[1])
        except (IndexError, TypeError, ValueError):
            continue
    return total


def _imbalance(libro, niveles=10):
    """Desequilibrio del libro en [-1, +1]: (volBid - volAsk) / (volBid + volAsk).
    +1 = todo el volumen en bids (presion compradora), -1 = todo en asks."""
    if not isinstance(libro, dict) or niveles <= 0:
        return 0.0
    bids = libro.get('bids')
    asks = libro.get('asks')
    if not bids or not asks:
        return 0.0
    vb = _volumen(bids, niveles)
    va = _volumen(asks, niveles)
    total = vb + va
    if total <= 0:
        return 0.0
    return (vb - va) / total


def _crear_estructura():
    """Crea carpetas necesarias y archivos .gitkeep si no existen."""
    directorios = [DIR_DATOS, DIR_LOGS, DIR_TEST]
    for directorio in directorios:
        os.makedirs(directorio, exist_ok=True)
        gitkeep = os.path.join(directorio, ".gitkeep")
        if not os.path.exists(gitkeep):
            open(gitkeep, "a").close()


def _configurar_logging(coins, mercado):
    """Configura logging para escribir en logs/libro_MONEDAS_MERCADO.log"""
    monedas_str = "-".join(c.upper() for c in coins)
    log_file = os.path.join(DIR_LOGS, f"libro_{monedas_str}_{mercado}.log")

    logger = logging.getLogger("libro")
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


def _archivo(coins, mercado, es_trades=False):
    """Genera ruta con rotacion diaria a medianoche UTC.

    Formato: libro_<coin>_<mercado>_YYYYMMDD.csv (para snapshots)
    o:       libro_trades_<coin>_<mercado>_YYYYMMDD.csv (para trades granulares)
    """
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    moneda = coins[0].upper()  # una sola moneda por fichero
    prefijo = "libro_trades" if es_trades else "libro"
    return os.path.join(DIR_DATOS, f"{prefijo}_{moneda}_{mercado}_{fecha}.csv")


def _ruta_compatible(ruta, campos_esperados, logger):
    if not os.path.exists(ruta):
        return ruta
    with open(ruta, newline="", encoding="utf-8") as f:
        primera = f.readline().strip()
    if primera == ",".join(campos_esperados):
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
        r = _ex_funding_rate(simbolo)
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
        valor = _ex_long_short_ratio(simbolo)
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


def _es_nuevo(estado, t):
    """Un trade ya contabilizado se reconoce por (timestamp, id), no solo por
    timestamp: en rafagas varios trades comparten el mismo milisegundo y
    descartar por 'ts <= cursor' se comeria los que caen justo en el borde."""
    tt = t.get("timestamp")
    if tt is None:
        return False
    cursor = estado["cursor"]
    if cursor is None:
        return True
    if tt < cursor:
        return False
    if tt == cursor:
        return t.get("id") not in estado["ids_cursor"]
    return True


def _avanzar_cursor(estado, lote):
    """Deja el cursor en el ts mas alto del lote y memoriza los ids de ese ts."""
    marcas = [t.get("timestamp") for t in lote if t.get("timestamp") is not None]
    if not marcas:
        return
    tope = max(marcas)
    if estado["cursor"] is not None and tope < estado["cursor"]:
        return
    ids_tope = {t.get("id") for t in lote if t.get("timestamp") == tope}
    if tope == estado["cursor"]:
        estado["ids_cursor"].update(ids_tope)
    else:
        estado["cursor"] = tope
        estado["ids_cursor"] = ids_tope


def _trade_flow(coin, simbolo, cache, logger):
    estado = cache.setdefault(coin, {
        "cursor": None, "ids_cursor": set(), "cvd": 0.0,
        "iniciado": False, "sin_trades": 0, "trades": [],
    })

    # Primera pasada: solo fija el punto de partida, no contabiliza nada.
    if not estado["iniciado"]:
        try:
            lote = _ex_trades(simbolo, limite=TRADES_LIMITE)
        except Exception as e:
            logger.warning(f"trades {coin}: {e}")
            return 0, 0.0, 0.0, estado["cvd"], []
        estado["iniciado"] = True
        if lote:
            _avanzar_cursor(estado, lote)
        logger.info(f"CVD arranca en 0.0 para {coin} (cursor={estado['cursor']})")
        return 0, 0.0, 0.0, estado["cvd"], []

    n = 0
    vb = vs = 0.0
    paginas = 0
    trades_nuevos = []

    while paginas < MAX_PAGINAS_TRADES:
        try:
            lote = _ex_trades(simbolo, desde=estado["cursor"], limite=TRADES_LIMITE)
        except Exception as e:
            logger.warning(f"trades {coin} (pagina {paginas + 1}): {e}")
            break
        paginas += 1

        nuevos = [t for t in lote if _es_nuevo(estado, t)]
        for t in nuevos:
            amt = t.get("amount") or 0.0
            lado = t.get("side")
            ts_exchange = t.get("timestamp")
            precio = t.get("price")

            if lado == "buy":
                vb += amt
            elif lado == "sell":
                vs += amt

            # Guardar para CSV de trades
            if ts_exchange is not None and precio is not None:
                trades_nuevos.append({
                    "ts_exchange": ts_exchange,
                    "precio": precio,
                    "volumen": amt,
                    "lado": lado,
                })

        n += len(nuevos)
        if nuevos:
            _avanzar_cursor(estado, nuevos)

        # Pagina corta: ya alcanzamos el presente, no hay mas cola.
        if len(lote) < TRADES_LIMITE:
            break
        # Pagina llena pero nada nuevo: el cursor no avanzaria, cortar.
        if not nuevos:
            logger.warning(f"trades {coin}: pagina llena sin trades nuevos, corto paginacion")
            break
    else:
        logger.error(
            f"trades {coin}: {MAX_PAGINAS_TRADES} paginas sin vaciar la cola, "
            f"POSIBLE PERDIDA de trades - bajar --cada"
        )

    if paginas > 1:
        logger.warning(f"trades {coin}: {paginas} paginas para {n} trades - actividad alta")

    estado["cvd"] += vb - vs
    estado["trades"] = trades_nuevos

    # Detectar período sin trades
    if n == 0:
        estado["sin_trades"] += 1
        if estado["sin_trades"] % ALERTAR_SI_NO_TRADES == 0:
            logger.warning(f"trades {coin}: sin trades en últimos {estado['sin_trades']} registros (posible downtime)")
    else:
        estado["sin_trades"] = 0

    return n, vb, vs, estado["cvd"], trades_nuevos


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
          ls_ratio_cache, ls_ratio_cada, trade_cache, session_id, logger):
    ahora = datetime.now(timezone.utc)
    timestamp_local_ms = int(ahora.timestamp() * 1000)
    estado = "ok"
    timestamp_exchange_ms = None

    try:
        libro = _ex_libro(simbolo, depth=profundidad)
    except Exception as e:
        logger.warning(f"libro {coin}: {e}")
        libro = None
        estado = "error_libro"

    try:
        oi = _ex_open_interest(simbolo)
    except Exception as e:
        logger.warning(f"open_interest {coin}: {e}")
        oi = None

    funding = _funding_pct(coin, simbolo, funding_cache, funding_cada, logger)
    ls_ratio = _ls_ratio(coin, simbolo, ls_ratio_cache, ls_ratio_cada, logger)
    n_trades, vol_buy, vol_sell, cvd, trades = _trade_flow(coin, simbolo, trade_cache, logger)

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
        "imbalance": _imbalance(libro, niveles=profundidad) if libro else "",
        "imbalance_niveles": profundidad,
        "open_interest": oi if oi is not None else "",
        "funding_rate_pct": funding if funding is not None else "",
        "long_short_ratio": ls_ratio if ls_ratio is not None else "",
        "n_trades": n_trades,
        "vol_buy": round(vol_buy, 6),
        "vol_sell": round(vol_sell, 6),
        "delta_vol": round(vol_buy - vol_sell, 6),
        "cvd": round(cvd, 6),
        "session_id": session_id,
        "bids_json": json.dumps(bids) if bids else "",
        "asks_json": json.dumps(asks) if asks else "",
    }, trades


def main():
    _crear_estructura()

    args = sys.argv[1:]
    coins = []
    mercado = "futuros"

    if args and not args[0].startswith("--"):
        coins = [c.strip().upper() for c in args[0].split(",")]
        args = args[1:]

    if not coins:
        coins = ["BTC", "ETH"]

    # Moneda es singular, pero agrupa todas las monedas en un fichero por ahora
    # Si hay varias, el primero es el nombre (compatible hacia atrás)
    logger = _configurar_logging(coins, mercado)

    cada = 15.0
    profundidad = 100
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
        elif args[i] == "--mercado":
            i += 1
            mercado = args[i]
        i += 1

    if profundidad not in PROFUNDIDADES_VALIDAS:
        logger.warning(
            f"--profundidad {profundidad} no existe en Bitget "
            f"(validas: {', '.join(str(p) for p in PROFUNDIDADES_VALIDAS)}); "
            f"el exchange lo ignorara y devolvera 100 niveles"
        )

    if cada < 300:
        logger.warning(
            f"--cada {cada:.0f}s es menor que 5 min: vol_buy/vol_sell tendran error ±45%. "
            f"Se recomienda ≥300s para datos confiables."
        )

    simbolos = {c: _ex_simbolo(c) for c in coins}
    session_id = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Archivos snapshots y trades - se rotan en bucle si cambia la fecha
    ruta_snapshot = _archivo(coins, mercado, es_trades=False)
    ruta_trades = _archivo(coins, mercado, es_trades=True)
    ruta_snapshot = _ruta_compatible(ruta_snapshot, CAMPOS_CSV, logger)
    ruta_trades = _ruta_compatible(ruta_trades, CAMPOS_TRADES, logger)

    nuevo_snapshot = not os.path.exists(ruta_snapshot)
    nuevo_trades = not os.path.exists(ruta_trades)

    arch_snapshot = open(ruta_snapshot, "a", newline="")
    arch_trades = open(ruta_trades, "a", newline="")

    _aplicar_lock(arch_snapshot, logger)

    writer_snapshot = csv.DictWriter(arch_snapshot, fieldnames=CAMPOS_CSV)
    writer_trades = csv.DictWriter(arch_trades, fieldnames=CAMPOS_TRADES)

    if nuevo_snapshot:
        writer_snapshot.writeheader()
        arch_snapshot.flush()
    if nuevo_trades:
        writer_trades.writeheader()
        arch_trades.flush()

    logger.info(f"Grabando {', '.join(coins)} cada {cada:.0f}s (profundidad {profundidad}) mercado={mercado}")
    logger.info(f"  Snapshots -> {ruta_snapshot}")
    logger.info(f"  Trades    -> {ruta_trades}")
    logger.info(f"  Session ID: {session_id} (identifica esta cadena de CVD)")
    logger.info(f"Funding cada {funding_cada:.0f}s, long/short ratio cada {ls_ratio_cada:.0f}s")
    logger.info(f"Trades: hasta {TRADES_LIMITE} por peticion, max {MAX_PAGINAS_TRADES} paginas por ciclo")

    funding_cache = {}
    ls_ratio_cache = {}
    trade_cache = {}
    timestamp_previo = None
    fecha_previo = None

    try:
        while True:
            ahora_utc = datetime.now(timezone.utc)
            fecha_hoy = ahora_utc.strftime("%Y%m%d")

            # Rotar ficheros si cambio la fecha UTC
            if fecha_previo is not None and fecha_hoy != fecha_previo:
                logger.info(f"ROTACION: Cambio de dia UTC ({fecha_previo} -> {fecha_hoy})")
                _liberar_lock(arch_snapshot)
                arch_snapshot.close()
                arch_trades.close()

                ruta_snapshot = _archivo(coins, mercado, es_trades=False)
                ruta_trades = _archivo(coins, mercado, es_trades=True)
                ruta_snapshot = _ruta_compatible(ruta_snapshot, CAMPOS_CSV, logger)
                ruta_trades = _ruta_compatible(ruta_trades, CAMPOS_TRADES, logger)

                nuevo_snapshot = not os.path.exists(ruta_snapshot)
                nuevo_trades = not os.path.exists(ruta_trades)

                arch_snapshot = open(ruta_snapshot, "a", newline="")
                arch_trades = open(ruta_trades, "a", newline="")
                _aplicar_lock(arch_snapshot, logger)

                writer_snapshot = csv.DictWriter(arch_snapshot, fieldnames=CAMPOS_CSV)
                writer_trades = csv.DictWriter(arch_trades, fieldnames=CAMPOS_TRADES)

                if nuevo_snapshot:
                    writer_snapshot.writeheader()
                if nuevo_trades:
                    writer_trades.writeheader()

                logger.info(f"  Snapshots -> {ruta_snapshot}")
                logger.info(f"  Trades    -> {ruta_trades}")

                # Reinicia CVD para la nueva sesion
                session_id = int(datetime.now(timezone.utc).timestamp() * 1000)
                logger.info(f"[CVD RESET] Nuevo session_id={session_id}")
                trade_cache.clear()

            fecha_previo = fecha_hoy

            for coin in coins:
                fila, trades = _fila(coin, simbolos[coin], profundidad, funding_cache, funding_cada,
                                      ls_ratio_cache, ls_ratio_cada, trade_cache, session_id, logger)

                # Validar gap temporal
                ts_actual = fila["timestamp_local_ms"]
                if timestamp_previo is not None:
                    gap_ms = ts_actual - timestamp_previo
                    gap_s = gap_ms / 1000.0
                    if gap_s > MAX_GAP_SEGUNDOS:
                        logger.error(f"GAP DETECTADO: {gap_s:.1f}s entre registros (esperado: {cada:.0f}s) - posible downtime")
                timestamp_previo = ts_actual

                writer_snapshot.writerow(fila)
                arch_snapshot.flush()

                # Guardar trades granulares
                ts_local = int(datetime.now(timezone.utc).timestamp() * 1000)
                fecha_utc = datetime.fromtimestamp(ts_local / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                for t in trades:
                    writer_trades.writerow({
                        "timestamp_exchange_ms": t["ts_exchange"],
                        "timestamp_local_ms": ts_local,
                        "fecha_utc": fecha_utc,
                        "precio": round(t["precio"], 2),
                        "volumen": round(t["volumen"], 6),
                        "lado": t["lado"],
                        "coin": coin,
                    })
                if trades:
                    arch_trades.flush()

            time.sleep(cada)
    except KeyboardInterrupt:
        logger.info("Parado por el usuario.")
    finally:
        _liberar_lock(arch_snapshot)
        arch_snapshot.close()
        arch_trades.close()


if __name__ == "__main__":
    main()
