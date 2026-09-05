
import csv
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import time
from datetime import datetime, timezone, timedelta
import platform

import ccxt

if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

logging.Formatter.converter = time.gmtime

DIR_BASE = os.path.dirname(os.path.abspath(__file__))
DIR_DATOS = os.path.join(DIR_BASE, "datos")
DIR_LOGS = os.path.join(DIR_BASE, "logs")
DIR_TEST = os.path.join(DIR_BASE, "test")

CAMPOS_CSV = [
    "timestamp_local_ms", "fecha_utc", "timestamp_exchange_ms", "estado", "coin",
    "imbalance", "imbalance_niveles", "imbalance_amplio",
    "last_price", "mark_price", "index_price",
    "open_interest", "funding_rate_pct", "long_short_ratio", "session_id",
    "bids_json", "asks_json",
    "bids_amplio_json", "asks_amplio_json",
]

MAX_GAP_FACTOR = 1.1

PROFUNDIDADES_VALIDAS = (1, 5, 15, 50, 100)

PRECISION_AMPLIA = 'scale2' 


_cliente = None


def _ex_credenciales():
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


def _ex_simbolo(coin, mercado="futuros"):
    return ("{}/USDT".format(coin.upper()) if mercado == "spot"
            else "{}/USDT:USDT".format(coin.upper()))


def _ex_libro(simbolo, depth, precision=None):
    params = {} if precision is None else {'precision': precision}
    ob = _ex_cliente().fetch_order_book(simbolo, limit=depth, params=params)
    return {
        'bids': ob.get('bids', []),
        'asks': ob.get('asks', []),
        'timestamp': ob.get('timestamp'),
    }


def _ex_ticker(simbolo):
    t = _ex_cliente().fetch_ticker(simbolo)
    info = t.get('info') or {}

    def num(*claves):
        for k in claves:
            v = info.get(k) if k in info else t.get(k)
            if v not in (None, ''):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return None

    return {
        'last': num('lastPr', 'last'),
        'mark': num('markPrice'),
        'index': num('indexPrice'),
    }


def _ex_open_interest(simbolo):
    return _ex_cliente().fetch_open_interest(simbolo).get('openInterestAmount')


def _ex_funding_rate(simbolo):
    return _ex_cliente().fetch_funding_rate(simbolo).get('fundingRate')


def _ex_long_short_ratio(simbolo, timeframe='1h'):
    serie = _ex_cliente().fetch_long_short_ratio_history(simbolo, timeframe=timeframe, limit=1)
    return serie[-1].get('longShortRatio') if serie else None


def _volumen(niveles, n):
    total = 0.0
    for nivel in niveles[:n]:
        try:
            total += float(nivel[1])
        except (IndexError, TypeError, ValueError):
            continue
    return total


def _imbalance(libro, niveles=10):
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
    directorios = [DIR_DATOS, DIR_LOGS, DIR_TEST]
    for directorio in directorios:
        os.makedirs(directorio, exist_ok=True)
        gitkeep = os.path.join(directorio, ".gitkeep")
        if not os.path.exists(gitkeep):
            open(gitkeep, "a").close()


def _configurar_logging(coins, mercado):
    monedas_str = "-".join(c.upper() for c in coins)
    log_file = os.path.join(DIR_LOGS, f"libro_{monedas_str}_{mercado}.log")

    logger = logging.getLogger("libro")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')

    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024,
                                       backupCount=3, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def _archivo(coins, mercado):
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    moneda = coins[0].upper()
    return os.path.join(DIR_DATOS, f"libro_{moneda}_{mercado}_{fecha}.csv")


def _ruta_compatible(ruta, campos_esperados, logger):

    def _cabecera(r):
        try:
            with open(r, newline='', encoding='utf-8') as f:
                return f.readline().strip()
        except OSError:
            return None

    esperada = ','.join(campos_esperados)
    if not os.path.exists(ruta):
        return ruta
    if _cabecera(ruta) == esperada:
        return ruta

    base, ext = os.path.splitext(ruta)
    n = 2
    while os.path.exists(f'{base}_v{n}{ext}'):
        if _cabecera(f'{base}_v{n}{ext}') == esperada:
            logger.info(f'{ruta} tiene otra cabecera; continuo en {base}_v{n}{ext}')
            return f'{base}_v{n}{ext}'
        n += 1
    nueva = f'{base}_v{n}{ext}'
    logger.warning(f'{ruta} cabecera distinta y ningun _vN compatible, creo {nueva}')
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


LOCK_VIGENCIA_FACTOR = 3.0
LOCK_VIGENCIA_MINIMA_S = 300.0


def _ruta_lock(coins, mercado):
    return os.path.join(DIR_DATOS, ".libro_{}_{}.lock".format(coins[0].upper(), mercado))


def _tomar_lock(ruta, vigencia_s, logger):
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            partes = f.read().strip().split("|")
        if len(partes) == 3 and (time.time() - float(partes[2])) < vigencia_s:
            logger.error(
                f"Ya hay otra instancia en ejecucion sobre {ruta} "
                f"(host={partes[0]} pid={partes[1]}, marca de hace "
                f"{time.time() - float(partes[2]):.0f}s). Saliendo."
            )
            sys.exit(1)
    except (IOError, OSError, ValueError, IndexError):
        pass

    arch = open(ruta, "w+", encoding="utf-8")
    try:
        if platform.system() == "Windows":
            msvcrt.locking(arch.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(arch.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        logger.error("Otra instancia en ejecución. Saliendo.")
        arch.close()
        sys.exit(1)
    _refrescar_lock(arch)
    return arch


def _refrescar_lock(arch):
    try:
        arch.seek(0)
        arch.truncate()
        arch.write(f"{platform.node()}|{os.getpid()}|{time.time():.0f}")
        arch.flush()
    except (IOError, OSError):
        pass


def _soltar_lock(arch):
    try:
        if platform.system() == "Windows":
            msvcrt.locking(arch.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(arch.fileno(), fcntl.LOCK_UN)
    except (IOError, OSError):
        pass
    arch.close()


def _fila(coin, simbolo, profundidad, funding_cache, funding_cada,
          ls_ratio_cache, ls_ratio_cada, session_id, logger):
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
        libro_amplio = _ex_libro(simbolo, depth=profundidad, precision=PRECISION_AMPLIA)
    except Exception as e:
        logger.warning(f"libro amplio {coin}: {e}")
        libro_amplio = None

    try:
        precios = _ex_ticker(simbolo)
    except Exception as e:
        logger.warning(f"ticker {coin}: {e}")
        precios = {}

    try:
        oi = _ex_open_interest(simbolo)
    except Exception as e:
        logger.warning(f"open_interest {coin}: {e}")
        oi = None

    funding = _funding_pct(coin, simbolo, funding_cache, funding_cada, logger)
    ls_ratio = _ls_ratio(coin, simbolo, ls_ratio_cache, ls_ratio_cada, logger)

    bids = None
    asks = None
    if libro:
        bids = libro.get("bids")
        asks = libro.get("asks")
        timestamp_exchange_ms = libro.get("timestamp")

    bids_amplio = libro_amplio.get("bids") if libro_amplio else None
    asks_amplio = libro_amplio.get("asks") if libro_amplio else None

    return {
        "timestamp_local_ms": timestamp_local_ms,
        "fecha_utc": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_exchange_ms": timestamp_exchange_ms if timestamp_exchange_ms is not None else "",
        "estado": estado,
        "coin": coin,
        "imbalance": _imbalance(libro, niveles=profundidad) if libro else "",
        "imbalance_niveles": profundidad,
        "imbalance_amplio": _imbalance(libro_amplio, niveles=profundidad) if libro_amplio else "",
        "last_price": precios.get("last") if precios.get("last") is not None else "",
        "mark_price": precios.get("mark") if precios.get("mark") is not None else "",
        "index_price": precios.get("index") if precios.get("index") is not None else "",
        "open_interest": oi if oi is not None else "",
        "funding_rate_pct": funding if funding is not None else "",
        "long_short_ratio": ls_ratio if ls_ratio is not None else "",
        "session_id": session_id,
        "bids_json": json.dumps(bids) if bids else "",
        "asks_json": json.dumps(asks) if asks else "",
        "bids_amplio_json": json.dumps(bids_amplio) if bids_amplio else "",
        "asks_amplio_json": json.dumps(asks_amplio) if asks_amplio else "",
    }


def _ayuda():
    print("Uso:")
    print("  python libro/libro.py [monedas] [opciones]")
    print("  Monedas: btc,eth,sol (default: btc,eth)")
    print("\nOpciones:")
    print("  --cada N           segundos entre capturas (default: 900)")
    print("  --profundidad N    niveles del libro (default: 100; validos: "
          + ", ".join(str(p) for p in PROFUNDIDADES_VALIDAS) + ")")
    print("  --funding-cada N   segundos entre updates de funding (default: 300)")
    print("  --ls-ratio-cada N  segundos entre updates de long/short ratio (default: 300)")
    print("  --mercado NOMBRE   spot|futuros (default: futuros)")
    print("\nEjemplos:")
    print("  python libro/libro.py")
    print("  python libro/libro.py btc,eth --cada 900")
    print("  python libro/libro.py btc,eth,icp --profundidad 50 --cada 300")
    print(f"\nSalida: {DIR_DATOS}/libro_<coin>_<mercado>_YYYYMMDD.csv (rota a medianoche UTC)")
    print("Parada segura: Ctrl-C, o 'kill -INT <PID>' (no pkill -f, que mataria todas las instancias)")


def main():
    args = sys.argv[1:]

    if any(a in ("-h", "--help") for a in args):
        _ayuda()
        return

    _crear_estructura()
    coins = []
    mercado = "futuros"

    if args and not args[0].startswith("--"):
        coins = [c.strip().upper() for c in args[0].split(",")]
        args = args[1:]

    if not coins:
        coins = ["BTC", "ETH"]

    cada = 900.0
    profundidad = 100
    funding_cada = 300.0
    ls_ratio_cada = 300.0

    FLAGS_CON_VALOR = ("--cada", "--profundidad", "--funding-cada",
                       "--ls-ratio-cada", "--mercado")
    i = 0
    while i < len(args):
        arg = args[i]
        if arg not in FLAGS_CON_VALOR:
            print(f"[ERROR] argumento no reconocido: {arg}")
            print(f"        opciones validas: {', '.join(FLAGS_CON_VALOR)}")
            sys.exit(1)
        if i + 1 >= len(args):
            print(f"[ERROR] {arg} requiere un valor")
            sys.exit(1)
        valor = args[i + 1]
        try:
            if arg == "--cada":
                cada = float(valor)
            elif arg == "--profundidad":
                profundidad = int(valor)
            elif arg == "--funding-cada":
                funding_cada = float(valor)
            elif arg == "--ls-ratio-cada":
                ls_ratio_cada = float(valor)
            elif arg == "--mercado":
                mercado = valor
        except ValueError:
            print(f"[ERROR] valor invalido para {arg}: {valor!r}")
            sys.exit(1)
        i += 2

    logger = _configurar_logging(coins, mercado)

    if profundidad not in PROFUNDIDADES_VALIDAS:
        logger.warning(
            f"--profundidad {profundidad} no existe en Bitget "
            f"(validas: {', '.join(str(p) for p in PROFUNDIDADES_VALIDAS)}); "
            f"el exchange lo ignorara y devolvera 100 niveles"
        )

    if cada < 60:
        logger.warning(
            f"--cada {cada:.0f}s guarda dos libros completos por ciclo: el CSV crece "
            f"~{2 * 86400 / cada * 4 / 1024:.0f} MB/dia. Se recomienda >=300s."
        )

    simbolos = {c: _ex_simbolo(c, mercado) for c in coins}
    session_id = int(datetime.now(timezone.utc).timestamp() * 1000)
    gap_maximo_s = cada * MAX_GAP_FACTOR

    vigencia_lock_s = max(LOCK_VIGENCIA_MINIMA_S, cada * LOCK_VIGENCIA_FACTOR)
    lock = _tomar_lock(_ruta_lock(coins, mercado), vigencia_lock_s, logger)

    ruta_snapshot = _archivo(coins, mercado)
    ruta_snapshot = _ruta_compatible(ruta_snapshot, CAMPOS_CSV, logger)

    nuevo_snapshot = not os.path.exists(ruta_snapshot)
    arch_snapshot = open(ruta_snapshot, "a", newline="")
    writer_snapshot = csv.DictWriter(arch_snapshot, fieldnames=CAMPOS_CSV)

    if nuevo_snapshot:
        writer_snapshot.writeheader()
        arch_snapshot.flush()

    logger.info(f"Grabando {', '.join(coins)} cada {cada:.0f}s (profundidad {profundidad}) mercado={mercado}")
    logger.info(f"  Snapshots -> {ruta_snapshot}")
    logger.info(f"  Session ID: {session_id} (marca de arranque de este proceso)")
    logger.info(f"Funding cada {funding_cada:.0f}s, long/short ratio cada {ls_ratio_cada:.0f}s")
    logger.info(f"Libro fino + amplio ({PRECISION_AMPLIA}). El tape lo graba flujo.py")

    funding_cache = {}
    ls_ratio_cache = {}
    timestamp_previo = None
    fecha_previo = None

    try:
        while True:
            ahora_utc = datetime.now(timezone.utc)
            fecha_hoy = ahora_utc.strftime("%Y%m%d")

            if fecha_previo is not None and fecha_hoy != fecha_previo:
                logger.info(f"ROTACION: Cambio de dia UTC ({fecha_previo} -> {fecha_hoy})")
                arch_snapshot.close()

                ruta_snapshot = _archivo(coins, mercado)
                ruta_snapshot = _ruta_compatible(ruta_snapshot, CAMPOS_CSV, logger)

                nuevo_snapshot = not os.path.exists(ruta_snapshot)
                arch_snapshot = open(ruta_snapshot, "a", newline="")
                writer_snapshot = csv.DictWriter(arch_snapshot, fieldnames=CAMPOS_CSV)

                if nuevo_snapshot:
                    writer_snapshot.writeheader()

                logger.info(f"  Snapshots -> {ruta_snapshot}")

            fecha_previo = fecha_hoy

            for coin in coins:
                fila = _fila(coin, simbolos[coin], profundidad, funding_cache, funding_cada,
                             ls_ratio_cache, ls_ratio_cada, session_id, logger)

                ts_actual = fila["timestamp_local_ms"]
                if timestamp_previo is not None:
                    gap_ms = ts_actual - timestamp_previo
                    gap_s = gap_ms / 1000.0
                    if gap_s > gap_maximo_s:
                        logger.error(f"GAP DETECTADO: {gap_s:.1f}s entre registros (esperado: {cada:.0f}s) - posible downtime")
                timestamp_previo = ts_actual

                writer_snapshot.writerow(fila)
                arch_snapshot.flush()

            _refrescar_lock(lock)
            time.sleep(cada)
    except KeyboardInterrupt:
        logger.info("Parado por el usuario.")
    finally:
        _soltar_lock(lock)
        arch_snapshot.close()


if __name__ == "__main__":
    main()
