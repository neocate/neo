# ---------------------------------------------------------------
# libro.py
#
# Graba lo UNICO que el exchange no sirve hacia atras: el libro de ordenes y el
# open interest. Si este proceso no esta corriendo, esos datos se pierden para
# siempre; no hay endpoint que los recupere.
#
#   libro de ordenes -> no existe endpoint historico en ningun exchange
#   open interest    -> fetchOpenInterestHistory = False. Solo snapshot
#   long/short ratio -> ventana fija (29 h en 1h, 92 h en 4h): casi irrecuperable
#   funding          -> SI recuperable a ~90 dias; se graba por conveniencia
#
# Lo que este fichero NO graba, y por que:
#
#   El tape (trades, delta, CVD) lo graba flujo.py. Durante mucho tiempo lo
#   grabo este fichero bajo la premisa de que era irrecuperable. Era falsa:
#   Bitget sirve fills-history 7.00 dias hacia atras. Y peor, se grababa mal:
#   se paginaba con since= (startTime), que Bitget IGNORA en ese endpoint, asi
#   que cada ciclo se quedaba con la COLA de la ventana -- entre el 17% y el 20%
#   del tape, con el delta del signo cambiado en 4 de 7 dias medidos.
#
#   flujo.py lo hace con endTime, que si se respeta, y ademas repara sus propios
#   huecos dentro de la ventana de 7 dias. Ver libro/flujo.py.
#
# Autonomo a proposito: no importa nada de mercado/ ni del resto del proyecto.
# Su unica dependencia es ccxt. Ver el bloque "Acceso al exchange" mas abajo.
# Un refactor ajeno que le rompiera el import lo pararia de madrugada, y aqui
# eso si es perdida definitiva.
#
# Uso:
#   python libro/libro.py [monedas] [opciones]
#   Monedas: btc,eth,sol (default: btc,eth)
#   Opciones:
#     --cada N           segundos entre capturas (default: 900)
#                        Cada ciclo guarda DOS libros de 100 niveles, asi que el
#                        CSV crece rapido: a 900s son ~1,5 MB/dia por moneda.
#     --profundidad N    niveles del libro (default: 100)
#                        Bitget solo admite 1, 5, 15, 50, 100: cualquier otro
#                        valor lo ignora y devuelve 100.
#     --funding-cada N   segundos entre updates funding (default: 300)
#                        Resolucion: >=5 min. Congelado entre updates.
#     --ls-ratio-cada N  segundos entre updates L/S ratio (default: 300)
#                        Resolucion: >=1h. Endpoint del exchange es horario.
#
# Ejemplos:
#   python libro/libro.py
#   python libro/libro.py btc,eth --cada 900
#   python libro/libro.py btc,eth,icp --profundidad 50 --cada 300
#
# Fichero de salida:
#
#   libro_<coin>_<mercado>_YYYYMMDD.csv
#     ROTA A MEDIANOCHE UTC.
#
#     Columnas:
#       timestamp_local_ms: cuando se capturo (maquina local)
#       timestamp_exchange_ms: cuando se capturo (exchange, si disponible)
#       fecha_utc: fecha y hora UTC de la captura
#       estado: 'ok' o 'error_libro' si fallo el libro fino
#       coin: moneda
#       imbalance, imbalance_niveles: desequilibrio del libro FINO en [-1,+1]
#       imbalance_amplio: lo mismo sobre el libro agrupado (~±4%)
#       last_price: ultimo precio negociado del perpetuo
#       mark_price: precio de marca (contra el que se disparan las liquidaciones)
#       index_price: precio indice (cesta de spot)
#         La BASE se calcula como (last_price - index_price) / index_price.
#         Negativa = el perpetuo cotiza por debajo del contado, es decir cortos
#         pagando por estarlo. Es la medida directa del apetito apalancado, y
#         mark/index tienen historico propio en Bitget desde 2019 via
#         fetch_mark_ohlcv / fetch_index_ohlcv, asi que se puede backtestear
#         sin esperar a acumular.
#       open_interest, funding_rate_pct, long_short_ratio: metadata del exchange
#       session_id: timestamp de arranque del proceso. Marca discontinuidades
#                   de la serie; ya no gobierna ninguna acumulacion
#       bids_json, asks_json: libro fino (~±8 bps del mid)
#       bids_amplio_json, asks_amplio_json: libro agrupado (~±4% del mid)
#
# Limitaciones conocidas:
#   - El libro fino alcanza solo ~7 bps a cada lado: su imbalance no predice el
#     precio (medido: corr -0.021 a 15 min sobre 511 snapshots). Para
#     posicionamiento hay que mirar imbalance_amplio.
#   - last/mark/index vienen del ticker, que trae ademas holdingAmount y
#     fundingRate identicos a los de sus endpoints dedicados. NO se usan de ahi
#     a proposito: el OI es irrecuperable y no conviene que un fallo del ticker
#     se lleve OI, funding y precios de una vez.
#   - funding_rate_pct se actualiza cada 300+ s (congelado entre updates).
#   - long_short_ratio es horario (endpoint Bitget es de resolucion 1h).
#   - Un hueco en esta serie NO se puede reponer. Es el unico proceso del
#     proyecto del que eso es cierto.
#
# Parada segura (libera locks, cierra CSVs, registra marca):
#   kill -INT <PID>   (no pkill -f, que mata todas las instancias)
#   o en Linux/Mac: Ctrl-C
#
# ---------------------------------------------------------------

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

# File locking multiplataforma
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

# Encoding UTF-8 para stderr/stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Los logs van en UTC, como los datos. velas_bit.py ya lo hacia asi por un
# motivo bueno: "una linea del log y una vela se cruzan sin conversiones de por
# medio". El resto usaba hora local, asi que correlacionar un incidente entre
# modulos obligaba a sumar o restar horas a ojo -- y en un NAS que ademas se
# toca desde otra maquina, a adivinar de que huso era cada marca.
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

# Alertas de integridad
MAX_GAP_FACTOR = 1.1  # Gap máximo tolerado, como múltiplo de --cada, antes de alertar

# Bitget (v2/mix/market/merge-depth) solo admite estos niveles; con cualquier
# otro valor ignora el parametro y devuelve 100.
PROFUNDIDADES_VALIDAS = (1, 5, 15, 50, 100)

# El libro fino (100 niveles sin agrupar) solo alcanza ~7 bps a cada lado del
# mid: es la capa que los market makers repintan en milisegundos, y su imbalance
# no correlaciona con el precio (medido: -0.021 a 15 min sobre 511 snapshots).
# merge-depth agrupa por rango de precio y estira muchisimo el alcance:
#   scale0 (fino)  -> ±8 bps,   2.455 ETH en bids
#   scale1         -> ±42 bps, 19.770 ETH
#   scale2 (amplio)-> ±402 bps, 52.589 ETH   <- cubre la banda de niveles
#   scale3         -> ±788 bps pero solo 20 niveles
# Se graban los dos: el fino para microestructura del toque, el amplio para
# posicionamiento. ±4% es justo donde viven las confluencias de niveles/.
PRECISION_AMPLIA = 'scale2' 


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


def _ex_simbolo(coin, mercado="futuros"):
    """'eth' -> 'ETH/USDT:USDT' en futuros, 'ETH/USDT' en spot.

    Antes devolvia SIEMPRE el perpetuo y 'mercado' solo entraba en el nombre
    del fichero: con --mercado spot se grababa libro_ETH_spot_*.csv lleno de
    datos de perpetuo. El fichero mentia sobre su contenido, que es la peor
    clase de bug en un historico que luego se analiza a ciegas.

    En spot no existen open interest, funding ni long/short ratio: esas
    columnas quedan vacias (cada llamada ya tiene su try/except y escribe "").
    """
    return ("{}/USDT".format(coin.upper()) if mercado == "spot"
            else "{}/USDT:USDT".format(coin.upper()))


def _ex_libro(simbolo, depth, precision=None):
    """Libro de ordenes. Con precision (merge-depth: scale1..scale3) Bitget
    agrupa por rango de precio y devuelve los mismos 100 niveles cubriendo
    mucho mas rango. Sin precision son niveles sin agrupar (~7 bps)."""
    params = {} if precision is None else {'precision': precision}
    ob = _ex_cliente().fetch_order_book(simbolo, limit=depth, params=params)
    return {
        'bids': ob.get('bids', []),
        'asks': ob.get('asks', []),
        'timestamp': ob.get('timestamp'),
    }


def _ex_ticker(simbolo):
    """last / mark / index en una sola peticion.

    Los tres son necesarios y ninguno estaba: el CSV solo tenia el mejor bid y
    ask del libro. Importan por dos motivos concretos:
      - las liquidaciones se disparan contra el MARK, no contra el ultimo precio
      - la base (last - index) / index mide el apetito apalancado: si el
        perpetuo cotiza por debajo del contado, hay cortos pagando por estarlo
    El ticker trae ademas holdingAmount y fundingRate, identicos a los de
    fetch_open_interest y fetch_funding_rate (comprobado). No se usan de aqui a
    proposito: el OI es irrecuperable y no conviene que un fallo del ticker se
    lleve por delante OI, funding y precios a la vez."""
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
    """Genera ruta con rotacion diaria a medianoche UTC.

    Formato: libro_<coin>_<mercado>_YYYYMMDD.csv
    """
    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    moneda = coins[0].upper()  # una sola moneda por fichero
    return os.path.join(DIR_DATOS, f"libro_{moneda}_{mercado}_{fecha}.csv")


def _ruta_compatible(ruta, campos_esperados, logger):
    """Ruta cuyo CSV tiene la cabecera esperada, sin mezclar esquemas.

    Antes solo miraba el fichero base y, si no encajaba, cogia el primer _vN
    LIBRE sin comprobar si alguno existente ya era compatible. Resultado: cada
    reinicio abria un _vN nuevo y el dia quedaba partido en trozos (paso el
    2026-09-01: _v2 y _v3 con cabecera identica y datos solapados en el tiempo).
    Ahora se reutiliza el primer candidato compatible y solo se crea uno nuevo
    si ninguno lo es."""

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


# Cuanto vale una marca de lock antes de considerarla de un proceso muerto.
# Debe superar de sobra el peor ciclo (cada, que es un parametro del usuario):
# si la vigencia fuese mas corta que la cadencia real, un proceso vivo pero
# que solo refresca una vez por ciclo perderia su propio lock entre refrescos.
LOCK_VIGENCIA_FACTOR = 3.0
LOCK_VIGENCIA_MINIMA_S = 300.0


def _ruta_lock(coins, mercado):
    """Fichero de lock dedicado, aparte del CSV de datos: asi la marca de
    texto (host|pid|timestamp) no se mezcla con las filas, y el lock
    sobrevive a la rotacion diaria del CSV sin tener que soltarse y
    retomarse en cada cambio de dia."""
    return os.path.join(DIR_DATOS, ".libro_{}_{}.lock".format("-".join(coins), mercado))


def _tomar_lock(ruta, vigencia_s, logger):
    """Impide dos instancias escribiendo sobre la misma salida.

    Dos mecanismos, porque uno solo no basta -- el mismo problema que ya
    resolvio flujo.py el 2026-09-02 (ver su _tomar_lock), nunca aplicado
    aqui hasta ahora:

    1. Lock del SO (fcntl / msvcrt). Fiable ENTRE PROCESOS DE LA MISMA
       PLATAFORMA, pero NO entre ellas: este proyecto se toca desde Windows
       (msvcrt) y corre en un NAS Linux (fcntl) por SMB. Un proceso Windows
       puede arrancar encima del de Linux sin que ninguno vea el lock del
       otro -- comprobado en vivo el 2026-09-02 con flujo.py, y reproducido
       por accidente con este mismo libro.py durante una auditoria.

    2. Marca host|pid|timestamp dentro del propio fichero de lock, que el
       proceso vivo refresca cada ciclo (ver _refrescar_lock). Si la marca es
       reciente hay otro corriendo, venga de la plataforma que venga: es lo
       unico que cruza SMB.
    """
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
        pass  # sin fichero, ilegible o formato viejo: se toma igualmente

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
    """Renueva la marca. Si se deja de llamar, a los `vigencia_s` de
    _tomar_lock el lock se considera huerfano y otro proceso puede tomarlo."""
    try:
        arch.seek(0)
        arch.truncate()
        arch.write(f"{platform.node()}|{os.getpid()}|{time.time():.0f}")
        arch.flush()
    except (IOError, OSError):
        pass


def _soltar_lock(arch):
    """Libera el lock del SO y cierra el fichero de marca."""
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

    # El libro amplio es informativo: si falla no invalida la fila, porque el
    # fino es el que sostiene el imbalance historico.
    try:
        libro_amplio = _ex_libro(simbolo, depth=profundidad, precision=PRECISION_AMPLIA)
    except Exception as e:
        logger.warning(f"libro amplio {coin}: {e}")
        libro_amplio = None

    # Informativo: si falla, la fila sigue siendo valida (el libro y el OI son
    # lo irrecuperable, no los precios de referencia).
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

    # Se mira ANTES de tocar disco o el exchange: un '--help' colado entre
    # otros argumentos no debe arrancar la captura en vivo por error (ver
    # _tomar_lock / validacion de argumentos mas abajo para el resto de
    # typos).
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

    # Lo que no se reconoce ES UN ERROR, no se ignora en silencio: un typo (o
    # un '--help' sin soporte dedicado) arrancaba igualmente la captura en
    # vivo con los valores por defecto sin que nadie se enterase -- pasó de
    # verdad durante una auditoría. Mismo criterio que parsear_args en
    # velas_bit.py, aplicado aquí por primera vez.
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

    # Moneda es singular, pero agrupa todas las monedas en un fichero por ahora
    # Si hay varias, el primero es el nombre (compatible hacia atrás).
    # Se crea DESPUES de leer --mercado: antes se creaba con el default
    # "futuros" fijo, así que "--mercado spot" grababa datos de spot en un
    # log que decía "futuros" en el nombre -- exactamente la clase de
    # fichero-que-miente-sobre-su-contenido que este proyecto ya evita en
    # otros sitios (ver _ex_simbolo).
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

    # Fichero de snapshots - se rota en bucle si cambia la fecha UTC
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

            # Rotar ficheros si cambio la fecha UTC. El lock vive aparte del
            # CSV (ver _ruta_lock) y no necesita soltarse ni retomarse aqui.
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

                # Validar gap temporal
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
