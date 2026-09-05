
import argparse
import asyncio
import csv
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

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
DIR_SALIDA = os.path.join(DIR_DATOS, "flujo")

CAMPOS_FLUJO = [
    "ventana_fin_ms", "fecha_utc", "coin",
    "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd",
    "ts_primer_trade", "ts_ultimo_trade", "cobertura_pct",
    "precio_apertura", "precio_cierre", "precio_max", "precio_min", "vwap",
]

CAMPOS_TRADES = [
    "id", "timestamp_exchange_ms", "ventana_fin_ms", "fecha_utc",
    "precio", "volumen", "lado", "coin",
]

CAMPOS_FUNDING = ["timestamp_ms", "fecha_utc", "coin", "funding_rate_pct"]

TRADES_LIMITE = 1000

VENTANA_HISTORICO_DIAS = 7
MARGEN_PARED_MS = 10 * 60 * 1000

MAX_PAGINAS_TRAMO = 4000

TOLERANCIA_SILENCIO_S = 45.0
TOLERANCIA_SILENCIO_FRAC = 0.05

VENTANAS_POR_TRAMO = 15

_LOCK_ESCRITURA = threading.Lock()

GRACIA_FLUSH_MS = 3000

TIMEOUT_WS_S = 20.0


_cliente = None


def _ex_cliente():
    global _cliente
    if _cliente is None:
        _cliente = ccxt.bitget({'enableRateLimit': True})
        _cliente.load_markets()
    return _cliente


def _ex_simbolo(coin, mercado="futuros"):
    return ("{}/USDT".format(coin.upper()) if mercado == "spot"
            else "{}/USDT:USDT".format(coin.upper()))


def _ex_trades_hasta(simbolo, fin_ms=None, limite=TRADES_LIMITE):
    params = {} if fin_ms is None else {'until': int(fin_ms)}
    return _ex_cliente().fetch_trades(simbolo, limit=limite, params=params)


def _ex_funding_historico(simbolo, desde_ms):
    return _ex_cliente().fetch_funding_rate_history(
        simbolo, since=int(desde_ms), limit=100, params={'paginate': True}
    )


def _paseo_atras(simbolo, desde_ms, hasta_ms, logger, etiqueta=""):
    vistos = {}
    fin = hasta_ms
    paginas = 0
    fallos = 0

    while paginas < MAX_PAGINAS_TRAMO:
        try:
            lote = _ex_trades_hasta(simbolo, fin_ms=fin)
        except ccxt.BaseError as e:
            texto = str(e)
            if '40707' in texto:
                logger.error(
                    "%spared de los %d dias alcanzada en %s: ese tramo ya no existe"
                    % (etiqueta, VENTANA_HISTORICO_DIAS, _fmt(fin))
                )
                break
            fallos += 1
            if fallos > 5:
                logger.error("%s6 fallos seguidos, abandono el tramo: %s" % (etiqueta, texto[:160]))
                break
            espera = 2 ** fallos
            logger.warning("%sfallo %d (%s), reintento en %ds" % (etiqueta, fallos, texto[:100], espera))
            time.sleep(espera)
            continue

        fallos = 0
        paginas += 1

        if not lote:
            break

        for t in lote:
            if t.get('id') is not None:
                vistos[t['id']] = t

        mas_antiguo = min(t['timestamp'] for t in lote)

        if mas_antiguo <= desde_ms:
            break

        if mas_antiguo >= fin:
            logger.warning(
                "%sel cursor no retrocede en %s (rafaga de >%d trades en el mismo ms), fuerzo 1 ms"
                % (etiqueta, _fmt(mas_antiguo), TRADES_LIMITE)
            )
            fin = mas_antiguo - 1
            continue

        fin = mas_antiguo
    else:
        logger.error(
            "%stope de %d paginas sin cubrir el tramo; queda sin bajar desde %s hasta %s"
            % (etiqueta, MAX_PAGINAS_TRAMO, _fmt(desde_ms), _fmt(fin))
        )

    dentro = [t for t in vistos.values() if desde_ms <= t['timestamp'] < hasta_ms]
    dentro.sort(key=lambda t: (t['timestamp'], str(t.get('id'))))
    return dentro, paginas


def _agregar(trades, ventana_ms, coin):
    cubos = {}
    for t in trades:
        fin = ((t['timestamp'] // ventana_ms) + 1) * ventana_ms
        cubos.setdefault(fin, []).append(t)

    filas = {}
    for fin, lote in cubos.items():
        vb = sum(t['amount'] for t in lote if t['side'] == 'buy')
        vs = sum(t['amount'] for t in lote if t['side'] == 'sell')
        marcas = [t['timestamp'] for t in lote]
        precios = [t['price'] for t in lote]
        volumen = vb + vs
        vwap = (sum(t['price'] * t['amount'] for t in lote) / volumen) if volumen > 0 else 0.0

        filas[fin] = {
            "ventana_fin_ms": fin,
            "fecha_utc": _fmt(fin, segundos=True),
            "coin": coin,
            "n_trades": len(lote),
            "vol_buy": round(vb, 6),
            "vol_sell": round(vs, 6),
            "delta_vol": round(vb - vs, 6),
            "cvd": 0.0,
            "ts_primer_trade": min(marcas),
            "ts_ultimo_trade": max(marcas),
            "cobertura_pct": round(100.0 * (max(marcas) - min(marcas)) / ventana_ms, 2),
            "precio_apertura": lote[0]['price'],
            "precio_cierre": lote[-1]['price'],
            "precio_max": max(precios),
            "precio_min": min(precios),
            "vwap": round(vwap, 4),
        }
    return filas


def _ruta(salida, prefijo, coin, mercado, ms):
    fecha = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%d")
    return os.path.join(salida, "%s_%s_%s_%s.csv" % (prefijo, coin, mercado, fecha))


def _leer_flujo(ruta):
    if not os.path.exists(ruta):
        return {}
    filas = {}
    with open(ruta, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                filas[int(r["ventana_fin_ms"])] = r
            except (KeyError, ValueError):
                continue
    return filas


def _ventanas_presentes(salida, coin, mercado, desde_ms, hasta_ms):
    presentes = set()
    dia = datetime.fromtimestamp(desde_ms / 1000, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    tope = datetime.fromtimestamp(hasta_ms / 1000, timezone.utc)
    while dia <= tope:
        ruta = _ruta(salida, "flujo", coin, mercado, int(dia.timestamp() * 1000))
        presentes.update(_leer_flujo(ruta).keys())
        dia += timedelta(days=1)
    return set(v for v in presentes if desde_ms < v <= hasta_ms)


def _huecos(salida, coin, mercado, desde_ms, hasta_ms, ventana_ms):
    presentes = _ventanas_presentes(salida, coin, mercado, desde_ms, hasta_ms)
    primera = ((desde_ms // ventana_ms) + 1) * ventana_ms
    esperadas = []
    v = primera
    while v <= hasta_ms:
        esperadas.append(v)
        v += ventana_ms

    faltan = [v for v in esperadas if v not in presentes]
    if not faltan:
        return []

    tramos = []
    ini = prev = faltan[0]
    for v in faltan[1:]:
        if v - prev > ventana_ms or (v - ini) // ventana_ms >= VENTANAS_POR_TRAMO:
            tramos.append((ini, prev))
            ini = v
        prev = v
    tramos.append((ini, prev))
    return tramos


def _escribir_atomico(ruta, campos, filas):
    tmp = ruta + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(filas)
    os.replace(tmp, ruta)


def _fusionar_flujo(salida, coin, mercado, nuevas, sobrescribir, logger):
    por_dia = {}
    for fin, fila in nuevas.items():
        por_dia.setdefault(_ruta(salida, "flujo", coin, mercado, fin - 1), {})[fin] = fila

    escritas = 0
    with _LOCK_ESCRITURA:
      for ruta, filas_nuevas in por_dia.items():
        existentes = _leer_flujo(ruta)
        for fin, fila in filas_nuevas.items():
            if fin in existentes and not sobrescribir:
                continue
            existentes[fin] = fila
            escritas += 1

        ordenadas = [existentes[k] for k in sorted(existentes)]
        cvd = 0.0
        for fila in ordenadas:
            cvd += float(fila["delta_vol"])
            fila["cvd"] = round(cvd, 6)
        _escribir_atomico(ruta, CAMPOS_FLUJO, ordenadas)

    return escritas


_CACHE_CLAVES_MAX_FICHEROS = 4
_claves_por_ruta = OrderedDict()


def _clave_trade(id_, ts, precio, volumen, lado):
    if id_:
        return ("id", str(id_))
    return ("tpl", int(ts), float(precio), float(volumen), lado)


def _claves_existentes(ruta):
    claves = set()
    if os.path.exists(ruta):
        with open(ruta, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    claves.add(_clave_trade(r.get("id"), r["timestamp_exchange_ms"],
                                             r["precio"], r["volumen"], r["lado"]))
                except (KeyError, ValueError):
                    continue
    return claves


def _claves_cache(ruta):
    if ruta in _claves_por_ruta:
        _claves_por_ruta.move_to_end(ruta)
        return _claves_por_ruta[ruta]
    claves = _claves_existentes(ruta)
    _claves_por_ruta[ruta] = claves
    if len(_claves_por_ruta) > _CACHE_CLAVES_MAX_FICHEROS:
        _claves_por_ruta.popitem(last=False)
    return claves


def _anexar_trades(salida, coin, mercado, trades, ventana_ms):
    por_dia = {}
    for t in trades:
        fin = ((t['timestamp'] // ventana_ms) + 1) * ventana_ms
        ruta = _ruta(salida, "trades", coin, mercado, t['timestamp'])
        por_dia.setdefault(ruta, []).append({
            "id": t.get("id") or "",
            "timestamp_exchange_ms": t['timestamp'],
            "ventana_fin_ms": fin,
            "fecha_utc": _fmt(t['timestamp'], segundos=True),
            "precio": t['price'],
            "volumen": t['amount'],
            "lado": t['side'],
            "coin": coin,
        })

    with _LOCK_ESCRITURA:
      for ruta, filas in por_dia.items():
        nuevo = not os.path.exists(ruta)
        vistas = _claves_cache(ruta)

        a_escribir = []
        for fila in filas:
            clave = _clave_trade(fila["id"], fila["timestamp_exchange_ms"],
                                  fila["precio"], fila["volumen"], fila["lado"])
            if clave in vistas:
                continue
            vistas.add(clave)
            a_escribir.append(fila)

        if not a_escribir:
            continue

        with open(ruta, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS_TRADES)
            if nuevo:
                w.writeheader()
            w.writerows(a_escribir)


def _capturar(simbolo, coin, mercado, desde_ms, hasta_ms, ventana_ms,
              salida, sobrescribir, logger, etiqueta=""):
    trades, paginas = _paseo_atras(simbolo, desde_ms, hasta_ms, logger, etiqueta)
    if not trades:
        return 0, 0, paginas

    filas = _agregar(trades, ventana_ms, coin)
    filas = {k: v for k, v in filas.items() if desde_ms < k <= hasta_ms}

    escritas = _fusionar_flujo(salida, coin, mercado, filas, sobrescribir, logger)
    _anexar_trades(salida, coin, mercado, trades, ventana_ms)

    tolerancia = max(TOLERANCIA_SILENCIO_S, ventana_ms / 1000.0 * TOLERANCIA_SILENCIO_FRAC)
    pobres = [f for f in filas.values()
              if (ventana_ms - (f['ts_ultimo_trade'] - f['ts_primer_trade'])) / 1000.0 > tolerancia]
    if pobres:
        logger.warning(
            "%s%d ventanas con mas de %.0fs de silencio: %s"
            % (etiqueta, len(pobres), tolerancia,
               ", ".join(f['fecha_utc'][11:19] for f in sorted(pobres, key=lambda x: x['ventana_fin_ms'])[:6]))
        )

    return escritas, len(trades), paginas


def _reparar_tramos(simbolo, coin, mercado, tramos, ventana_ms, salida,
                    sobrescribir, logger, presupuesto_s=None):
    t0 = time.time()
    repuestas = trades_tot = 0
    for i, (a, b) in enumerate(tramos):
        if presupuesto_s is not None and time.time() - t0 >= presupuesto_s:
            return repuestas, trades_tot, tramos[i:]
        n, tr, _ = _capturar(simbolo, coin, mercado, a - ventana_ms, b, ventana_ms,
                             salida, sobrescribir, logger,
                             etiqueta="[%s reparar %s] " % (coin, _fmt(a)))
        repuestas += n
        trades_tot += tr
    return repuestas, trades_tot, []


def _reparar(simbolo, coin, mercado, desde_ms, hasta_ms, ventana_ms, salida,
             sobrescribir, logger):
    tramos = _huecos(salida, coin, mercado, desde_ms, hasta_ms, ventana_ms)
    if not tramos:
        return 0, 0
    total = sum((b - a) // ventana_ms + 1 for a, b in tramos)
    logger.info("[%s] %d ventanas a reponer en %d tramos" % (coin, total, len(tramos)))
    rep, tr, _ = _reparar_tramos(simbolo, coin, mercado, tramos, ventana_ms,
                                 salida, sobrescribir, logger)
    return rep, tr


def _funding(simbolo, coin, mercado, desde_ms, salida, logger):
    try:
        serie = _ex_funding_historico(simbolo, desde_ms)
    except Exception as e:
        logger.error("[%s] funding: %s" % (coin, str(e)[:160]))
        return
    if not serie:
        logger.warning("[%s] funding: sin datos" % coin)
        return
    serie.sort(key=lambda x: x['timestamp'])
    ruta = os.path.join(salida, "funding_%s_%s.csv" % (coin, mercado))
    _escribir_atomico(ruta, CAMPOS_FUNDING, [{
        "timestamp_ms": x['timestamp'],
        "fecha_utc": _fmt(x['timestamp'], segundos=True),
        "coin": coin,
        "funding_rate_pct": x['fundingRate'] * 100 if x.get('fundingRate') is not None else "",
    } for x in serie])
    logger.info("[%s] funding: %d puntos, %s -> %s"
                % (coin, len(serie), _fmt(serie[0]['timestamp']), _fmt(serie[-1]['timestamp'])))


def _fmt(ms, segundos=False):
    patron = "%Y-%m-%d %H:%M:%S" if segundos else "%Y-%m-%d %H:%M"
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime(patron)


def _parsear_fecha(texto, etiqueta):
    for patron in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(texto, patron).replace(tzinfo=timezone.utc)
            return int(d.timestamp() * 1000)
        except ValueError:
            continue
    raise SystemExit("--%s '%s' no es una fecha UTC valida (YYYY-MM-DD[THH:MM])" % (etiqueta, texto))


def _configurar_logging(coins, mercado):
    monedas = "-".join(c.upper() for c in coins)
    logger = logging.getLogger("flujo")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        logger.handlers.clear()
    formato = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s',
                                datefmt='%Y-%m-%d %H:%M:%S')
    fh = RotatingFileHandler(os.path.join(DIR_LOGS, "flujo_%s_%s.log" % (monedas, mercado)),
                             maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    fh.setFormatter(formato)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(formato)
    logger.addHandler(sh)
    return logger


def _crear_estructura(salida):
    for directorio in (DIR_DATOS, DIR_LOGS, salida):
        os.makedirs(directorio, exist_ok=True)
        gitkeep = os.path.join(directorio, ".gitkeep")
        if not os.path.exists(gitkeep):
            open(gitkeep, "a").close()


LOCK_VIGENCIA_S = 300


def _tomar_lock(salida, monedas, mercado, logger):
    ruta = os.path.join(salida, ".flujo_%s_%s.lock" % (monedas, mercado))

    try:
        with open(ruta, "r", encoding="utf-8") as f:
            partes = f.read().strip().split("|")
        if len(partes) == 3 and (time.time() - float(partes[2])) < LOCK_VIGENCIA_S:
            logger.error(
                "Ya corre otra instancia sobre %s (host=%s pid=%s, marca de hace "
                "%.0fs). Saliendo." % (salida, partes[0], partes[1],
                                       time.time() - float(partes[2])))
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
        logger.error("Otra instancia ya corre sobre %s. Saliendo." % salida)
        arch.close()
        sys.exit(1)
    _refrescar_lock(arch)
    return arch


def _refrescar_lock(arch):
    try:
        arch.seek(0)
        arch.truncate()
        arch.write("%s|%d|%.0f" % (platform.node(), os.getpid(), time.time()))
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


def _modo_vivo(coins, mercado, ventana_ms, salida, cada, reparar_max,
               auditar_cada, con_funding, logger, lock=None):
    simbolos = dict((c, _ex_simbolo(c, mercado)) for c in coins)
    ciclo = 0
    ultima_ventana = {}
    pendientes = dict((c, []) for c in coins)

    logger.info("modo vivo: ventana %ds, reparacion <=%ds/ciclo, auditoria cada %d ciclos"
                % (ventana_ms / 1000, reparar_max, auditar_cada))

    while True:
        t_ciclo = time.time()
        ciclo += 1
        ahora = int(time.time() * 1000)

        for coin in coins:
            fin = (ahora // ventana_ms) * ventana_ms
            if ultima_ventana.get(coin) == fin:
                continue
            try:
                n, tr, pags = _capturar(simbolos[coin], coin, mercado,
                                        fin - ventana_ms, fin, ventana_ms,
                                        salida, True, logger, etiqueta="[%s vivo] " % coin)
                ultima_ventana[coin] = fin
                logger.info("[%s vivo] %s | %d trades, %d peticiones" % (coin, _fmt(fin), tr, pags))
            except Exception as e:
                logger.error("[%s vivo] %s" % (coin, str(e)[:200]))

            if ciclo == 1 or ciclo % auditar_cada == 1 or not pendientes[coin]:
                pared = ahora - VENTANA_HISTORICO_DIAS * 86400000 + MARGEN_PARED_MS
                try:
                    nuevos = _huecos(salida, coin, mercado, pared, fin - ventana_ms, ventana_ms)
                except Exception as e:
                    logger.error("[%s auditar] %s" % (coin, str(e)[:200]))
                    nuevos = []
                if nuevos and nuevos != pendientes[coin]:
                    total = sum((b - a) // ventana_ms + 1 for a, b in nuevos)
                    logger.info("[%s] auditoria: %d ventanas a reponer en %d tramos"
                                % (coin, total, len(nuevos)))
                pendientes[coin] = nuevos

            if pendientes[coin]:
                resto = cada - (time.time() - t_ciclo)
                presupuesto = max(0.0, min(reparar_max, resto - 1.0))
                if presupuesto > 0:
                    try:
                        rep, _, queda = _reparar_tramos(
                            simbolos[coin], coin, mercado, pendientes[coin], ventana_ms,
                            salida, False, logger, presupuesto_s=presupuesto)
                        pendientes[coin] = queda
                        if rep:
                            faltan = sum((b - a) // ventana_ms + 1 for a, b in queda)
                            logger.info("[%s] %d ventanas repuestas, %d pendientes"
                                        % (coin, rep, faltan))
                    except Exception as e:
                        logger.error("[%s reparar] %s" % (coin, str(e)[:200]))

            if con_funding and (ciclo % auditar_cada == 1 or ciclo == 1):
                _funding(simbolos[coin], coin, mercado,
                         ahora - 90 * 86400000, salida, logger)

        if lock is not None:
            _refrescar_lock(lock)

        dormir = cada - (time.time() - t_ciclo)
        if dormir > 0:
            time.sleep(dormir)
        else:
            logger.warning("ciclo %d tardo %.1fs, mas que la cadencia de %.0fs"
                           % (ciclo, time.time() - t_ciclo, cada))


def _parche_dns():
    try:
        import aiohttp.connector as _c
        from aiohttp.resolver import ThreadedResolver
        _c.DefaultResolver = ThreadedResolver
    except ImportError:
        pass


def _volcar_cerradas(buffers, simbolo_coin, ventana_ms, ahora_ms, mercado,
                     salida, logger, forzar=False):
    limite = ahora_ms if forzar else ahora_ms - GRACIA_FLUSH_MS
    tope = (limite // ventana_ms) * ventana_ms
    total = 0
    for coin, buf in buffers.items():
        if not buf:
            continue
        cerradas = {}
        for tid in list(buf):
            t = buf[tid]
            fin = ((t["timestamp"] // ventana_ms) + 1) * ventana_ms
            if fin <= tope:
                cerradas.setdefault(fin, []).append(t)
                del buf[tid]
        if not cerradas:
            continue
        trades = sorted((t for lote in cerradas.values() for t in lote),
                        key=lambda x: (x["timestamp"], str(x.get("id"))))
        filas = _agregar(trades, ventana_ms, coin)
        _fusionar_flujo(salida, coin, mercado, filas, True, logger)
        _anexar_trades(salida, coin, mercado, trades, ventana_ms)
        total += len(filas)
        logger.info("[%s ws] %s | %d ventanas, %d trades"
                    % (coin, _fmt(max(cerradas)), len(filas), len(trades)))
    return total


async def _bucle_ws(coins, mercado, ventana_ms, salida, reparar_max,
                    auditar_cada, logger, lock=None):
    _parche_dns()
    try:
        import ccxt.pro as ccxtpro
    except ImportError:
        raise SystemExit("ccxt.pro no disponible: hace falta ccxt >= 4 para --ws")

    ex = ccxtpro.bitget({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    await ex.load_markets()
    simbolos = dict((c, _ex_simbolo(c, mercado)) for c in coins)
    por_simbolo = dict((v, k) for k, v in simbolos.items())
    buffers = dict((c, {}) for c in coins)
    pendientes = dict((c, []) for c in coins)
    tarea_rep = None
    ciclo = 0
    loop = asyncio.get_running_loop()

    logger.info("modo vivo WS: ventana %ds, reparacion <=%ds, auditoria cada %d vueltas"
                % (ventana_ms / 1000, reparar_max, auditar_cada))

    def _reponer(coin):
        ahora = int(time.time() * 1000)
        pared = ahora - VENTANA_HISTORICO_DIAS * 86400000 + MARGEN_PARED_MS
        tope = (ahora // ventana_ms) * ventana_ms - ventana_ms
        if not pendientes[coin]:
            pendientes[coin] = _huecos(salida, coin, mercado, pared, tope, ventana_ms)
            if pendientes[coin]:
                n = sum((b - a) // ventana_ms + 1 for a, b in pendientes[coin])
                logger.info("[%s] auditoria: %d ventanas a reponer en %d tramos"
                            % (coin, n, len(pendientes[coin])))
        if not pendientes[coin]:
            return coin, 0
        rep, _, queda = _reparar_tramos(simbolos[coin], coin, mercado, pendientes[coin],
                                        ventana_ms, salida, False, logger,
                                        presupuesto_s=reparar_max)
        pendientes[coin] = queda
        return coin, rep

    try:
        while True:
            ciclo += 1
            try:
                trades = await asyncio.wait_for(
                    ex.watch_trades_for_symbols(list(simbolos.values())),
                    timeout=TIMEOUT_WS_S)
                for t in trades:
                    coin = por_simbolo.get(t.get("symbol"))
                    if coin and t.get("id") is not None:
                        buffers[coin][t["id"]] = t
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.error("ws: %s" % str(e)[:200])
                await asyncio.sleep(5)
                continue

            _volcar_cerradas(buffers, por_simbolo, ventana_ms,
                             int(time.time() * 1000), mercado, salida, logger)
            if lock is not None:
                _refrescar_lock(lock)

            if (tarea_rep is None or tarea_rep.done()) and ciclo % auditar_cada == 1:
                if tarea_rep is not None and tarea_rep.done():
                    try:
                        rep_coin, n = tarea_rep.result()
                        if n:
                            logger.info("[%s] %d ventanas repuestas" % (rep_coin, n))
                    except Exception as e:
                        logger.error("reparar: %s" % str(e)[:200])
                objetivo = coins[(ciclo // max(auditar_cada, 1)) % len(coins)]
                tarea_rep = loop.run_in_executor(None, _reponer, objetivo)
    finally:
        try:
            _volcar_cerradas(buffers, por_simbolo, ventana_ms,
                             int(time.time() * 1000), mercado, salida, logger, forzar=True)
        finally:
            await ex.close()


def _modo_vivo_ws(coins, mercado, ventana_ms, salida, reparar_max, auditar_cada,
                  logger, lock=None):
    asyncio.run(
        _bucle_ws(coins, mercado, ventana_ms, salida, reparar_max, auditar_cada,
                  logger, lock))


def _modo_reparacion(coins, mercado, desde_ms, hasta_ms, ventana_ms, salida,
                     sobrescribir, con_funding, logger):
    for coin in coins:
        simbolo = _ex_simbolo(coin, mercado)
        t0 = time.time()
        rep, tr = _reparar(simbolo, coin, mercado, desde_ms, hasta_ms, ventana_ms,
                           salida, sobrescribir, logger)
        if rep:
            logger.info("[%s] %d ventanas, %d trades, %.0fs" % (coin, rep, tr, time.time() - t0))
        else:
            logger.info("[%s] sin huecos en %s -> %s" % (coin, _fmt(desde_ms), _fmt(hasta_ms)))
        if con_funding:
            _funding(simbolo, coin, mercado, desde_ms, salida, logger)


def main():
    p = argparse.ArgumentParser(
        description="Flujo agresor de Bitget: captura en vivo con reparacion automatica de cortes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("monedas", nargs="?", default="eth", help="monedas separadas por coma (default: eth)")
    p.add_argument("--loop", action="store_true", help="modo vivo continuo")
    p.add_argument("--ws", action="store_true",
                   help="en modo vivo, usar WebSocket como fuente primaria "
                        "(REST queda solo para auditar y reponer huecos)")
    p.add_argument("--cada", type=float, default=60.0, help="segundos por ventana (default: 60)")
    p.add_argument("--reparar-max", type=float, default=20.0,
                   help="segundos de reparacion por ciclo en modo vivo (default: 20)")
    p.add_argument("--auditar-cada", type=int, default=60,
                   help="ciclos entre auditorias de huecos (default: 60)")
    p.add_argument("--desde", help="inicio UTC YYYY-MM-DD[THH:MM] (default: pared -7d)")
    p.add_argument("--hasta", help="fin UTC YYYY-MM-DD[THH:MM] (default: ahora)")
    p.add_argument("--salida", default=DIR_SALIDA, help="carpeta destino (default: datos/flujo)")
    p.add_argument("--mercado", default="futuros", help="etiqueta de mercado (default: futuros)")
    p.add_argument("--funding", action="store_true", help="baja tambien el funding historico")
    p.add_argument("--sobrescribir", action="store_true", help="rehace ventanas que ya existen")
    args = p.parse_args()

    coins = [c.strip().upper() for c in args.monedas.split(",") if c.strip()]
    if not coins:
        raise SystemExit("no se indico ninguna moneda")
    if args.cada <= 0:
        raise SystemExit("--cada debe ser mayor que 0")
    if args.auditar_cada < 1:
        raise SystemExit("--auditar-cada debe ser 1 o mas")

    _crear_estructura(args.salida)
    logger = _configurar_logging(coins, args.mercado)
    lock = _tomar_lock(args.salida, "-".join(coins), args.mercado, logger)

    ventana_ms = int(args.cada * 1000)
    ahora = int(time.time() * 1000)
    pared = ahora - VENTANA_HISTORICO_DIAS * 86400000 + MARGEN_PARED_MS

    try:
        if args.loop:
            if args.desde or args.hasta:
                logger.warning("--desde/--hasta se ignoran en modo --loop")
            logger.info("flujo %s %s | ventana %.0fs | fuente %s | salida %s"
                        % (",".join(coins), args.mercado, args.cada,
                           "WebSocket (REST repara)" if args.ws else "REST", args.salida))
            logger.info("caduca lo anterior a %s"
                        % _fmt(ahora - VENTANA_HISTORICO_DIAS * 86400000))
            if args.ws:
                _modo_vivo_ws(coins, args.mercado, ventana_ms, args.salida,
                              args.reparar_max, args.auditar_cada, logger, lock)
            else:
                _modo_vivo(coins, args.mercado, ventana_ms, args.salida, args.cada,
                           args.reparar_max, args.auditar_cada, args.funding, logger, lock)
        else:
            hasta_ms = _parsear_fecha(args.hasta, "hasta") if args.hasta else ahora
            desde_ms = _parsear_fecha(args.desde, "desde") if args.desde else pared
            ultimo_cierre = (ahora // ventana_ms) * ventana_ms
            if hasta_ms > ultimo_cierre:
                if hasta_ms > ahora:
                    logger.warning("--hasta %s es futuro" % _fmt(hasta_ms))
                hasta_ms = ultimo_cierre
            if desde_ms < pared:
                logger.warning(
                    "--desde %s cae fuera de los %d dias que sirve el exchange; se recorta a %s"
                    % (_fmt(desde_ms), VENTANA_HISTORICO_DIAS, _fmt(pared))
                )
                desde_ms = pared
            if desde_ms >= hasta_ms:
                raise SystemExit(
                    "no queda rango que bajar (%s -> %s): el tramo pedido ya caduco"
                    % (_fmt(desde_ms), _fmt(hasta_ms))
                )
            logger.info("flujo %s %s | %s -> %s (%.1f h) | ventana %.0fs | salida %s"
                        % (",".join(coins), args.mercado, _fmt(desde_ms), _fmt(hasta_ms),
                           (hasta_ms - desde_ms) / 3600000, args.cada, args.salida))
            _modo_reparacion(coins, args.mercado, desde_ms, hasta_ms, ventana_ms,
                             args.salida, args.sobrescribir, args.funding, logger)
    finally:
        _soltar_lock(lock)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrumpido")
        sys.exit(130)
