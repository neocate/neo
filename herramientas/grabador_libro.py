# Uso (desde la raiz del repo):
#   python herramientas/grabador_libro.py [coin[,coin2,...]] [--cada 15]
#                             [--profundidad 1000]
#                             [--libro-crudo-cada 60] [--ls-ratio-cada 300]
#   coin por defecto: btc,eth
#
# Ajuste en caliente y anadir/quitar/reiniciar una moneda SIN reiniciar el
# proceso: dejar caer un JSON en herramientas/grabador_libro/comandos_grabador/
#
# Ejemplos:
#   python herramientas/grabador_libro.py
#   python herramientas/grabador_libro.py btc,eth,icp
#   python herramientas/grabador_libro.py btc,eth --cada 30

import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import aiohttp
import ccxt.pro as ccxtpro

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import datos, flujo

DIR_GRABADOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grabador_libro")

CAMPOS_CSV = [
    "timestamp_ms", "fecha_utc",
    "bid", "ask", "spread_bps", "mid", "microprecio",
    "imbalance", "imbalance_niveles",
    "open_interest", "funding_rate_pct", "long_short_ratio",
    "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd",
    "bids_json", "asks_json",
    "pid",
]

CAMPOS_HUECOS = [
    "timestamp_ms", "fecha_utc", "coin",
    "ts_ultimo_trade_previo", "duracion_seg", "estado",
    "n_trades_recuperados", "vol_recuperado", "pid",
]


def _flt(s):
    if s in (None, ""):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _archivo(coin):
    os.makedirs(DIR_GRABADOR, exist_ok=True)
    return os.path.join(DIR_GRABADOR, f"flujo_{coin.upper()}.csv")


def _ruta_compatible(ruta):
    """_ruta_compatible(ruta: str) -> str"""
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


def _ruta_lock(coin):
    return os.path.join(DIR_GRABADOR, f"grabador_libro_{coin.upper()}.lock")


def _pid_vivo(pid):
    """_pid_vivo(pid: int) -> bool"""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    import ctypes.wintypes as wt
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    abrir = ctypes.windll.kernel32.OpenProcess
    abrir.restype = wt.HANDLE
    abrir.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    handle = abrir(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _lock_libre_o_huerfano(coin):
    """_lock_libre_o_huerfano(coin: str) -> bool"""
    ruta_lock = _ruta_lock(coin)
    if not os.path.exists(ruta_lock):
        return True
    try:
        with open(ruta_lock) as f:
            pid_viejo = int(f.read().strip())
        return not _pid_vivo(pid_viejo)
    except (ValueError, OSError):
        return True


def _bloquear_coin(coin):
    with open(_ruta_lock(coin), "w") as f:
        f.write(str(os.getpid()))


def _desbloquear_coin(coin):
    try:
        os.remove(_ruta_lock(coin))
    except OSError:
        pass


def _bloquear_instancia_unica(coins):
    """_bloquear_instancia_unica(coins: list[str]) -> None"""
    for coin in coins:
        if not _lock_libre_o_huerfano(coin):
            with open(_ruta_lock(coin)) as f:
                pid_viejo = f.read().strip()
            print(f"ERROR: ya hay un grabador_libro.py corriendo para {coin} (PID {pid_viejo}).")
            print(f"       Si el proceso murio sin limpiar {_ruta_lock(coin)}, borralo a mano y reintenta.")
            sys.exit(1)
        elif os.path.exists(_ruta_lock(coin)):
            print(f"(aviso) lock huerfano de {coin} - se reemplazara al arrancar.")


def _ultimo_cvd(ruta):
    """_ultimo_cvd(ruta: str) -> float | None"""
    if not os.path.exists(ruta):
        return None
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 262_144))
        cola = f.read()
    lineas = [l.rstrip("\r") for l in cola.decode("utf-8", errors="replace").split("\n") if l.strip()]
    cabecera = ",".join(CAMPOS_CSV)
    if lineas and lineas[0] == cabecera:
        lineas = lineas[1:]
    elif len(lineas) > 1:
        lineas = lineas[1:]
    for linea in reversed(lineas):
        campos = next(csv.reader([linea]))
        if len(campos) != len(CAMPOS_CSV):
            continue
        fila = dict(zip(CAMPOS_CSV, campos))
        valor = fila.get("cvd")
        return float(valor) if valor not in (None, "") else None
    return None


def _ruta_cursor(coin):
    return os.path.join(DIR_GRABADOR, f"cursor_{coin.upper()}.json")


def _guardar_cursor(coin, estado):
    """_guardar_cursor(coin: str, estado: dict) -> None"""
    if estado["ultimo_trade_ts"] is None:
        return
    ruta = _ruta_cursor(coin)
    tmp = ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump({
            "ultimo_trade_ts": estado["ultimo_trade_ts"],
            "ids_en_ultimo_ts": sorted(estado["ids_en_ultimo_ts"]),
        }, f)
    os.replace(tmp, ruta)


def _cargar_cursor(coin):
    """_cargar_cursor(coin: str) -> (ultimo_trade_ts: int|None, ids: set)"""
    ruta = _ruta_cursor(coin)
    if not os.path.exists(ruta):
        return None, set()
    try:
        with open(ruta) as f:
            data = json.load(f)
        return data.get("ultimo_trade_ts"), set(data.get("ids_en_ultimo_ts", []))
    except (OSError, ValueError):
        return None, set()


PARAMS_DEFECTO = {
    "cada": 15.0, "profundidad": 1000,
    "libro_crudo_cada": 60.0, "ls_ratio_cada": 300.0,
}

LIMITES_PARAMS = {
    "cada": (1.0, 300.0), "profundidad": (1, 5000),
    "libro_crudo_cada": (10.0, 86400.0), "ls_ratio_cada": (10.0, 86400.0),
}

DIR_COMANDOS = os.path.join(DIR_GRABADOR, "comandos_grabador")


def _ruta_config():
    return os.path.join(DIR_GRABADOR, "grabador_config.json")


def _cargar_config():
    params = dict(PARAMS_DEFECTO)
    ruta = _ruta_config()
    if os.path.exists(ruta):
        try:
            with open(ruta) as f:
                guardado = json.load(f)
            params.update({k: v for k, v in guardado.items() if k in PARAMS_DEFECTO})
        except (OSError, ValueError):
            pass
    return params


def _guardar_config(params):
    ruta = _ruta_config()
    tmp = ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump({k: params[k] for k in PARAMS_DEFECTO}, f)
    os.replace(tmp, ruta)


def _crear_estado(cvd_previo):
    return {
        "cvd": cvd_previo if cvd_previo is not None else 0.0,
        "libro": None, "libro_ts": None,
        "funding": None, "oi": None, "ticker_ts": None,
        "ls_ratio": None,
        "ultimo_trade_ts": None,
        "ids_en_ultimo_ts": set(),
        "ids_recientes": {},
        "n_trades_fila": 0, "vol_buy_fila": 0.0, "vol_sell_fila": 0.0,
        "libro_crudo_ultimo": 0.0,
    }


def _procesar_trade(estado, t):
    """_procesar_trade(estado: dict, t: dict) -> bool"""
    tid = t.get("id")
    if tid is not None and tid in estado["ids_recientes"]:
        return False
    amt = t.get("amount") or 0.0
    lado = t.get("side")
    if lado == "buy":
        estado["cvd"] += amt
        estado["vol_buy_fila"] += amt
    elif lado == "sell":
        estado["cvd"] -= amt
        estado["vol_sell_fila"] += amt
    estado["n_trades_fila"] += 1
    tt = t.get("timestamp")
    if tt is not None:
        if estado["ultimo_trade_ts"] is None or tt > estado["ultimo_trade_ts"]:
            estado["ultimo_trade_ts"] = tt
            estado["ids_en_ultimo_ts"] = set()
        if tt == estado["ultimo_trade_ts"] and tid is not None:
            estado["ids_en_ultimo_ts"].add(tid)
    if tid is not None:
        estado["ids_recientes"][tid] = time.monotonic()
    return True


def _podar_ids_recientes(estado, ventana_seg=180.0):
    ahora = time.monotonic()
    viejos = [tid for tid, ts in estado["ids_recientes"].items() if ahora - ts > ventana_seg]
    for tid in viejos:
        del estado["ids_recientes"][tid]


def _niveles_limpios(niveles, n):
    """_niveles_limpios(niveles, n: int) -> list[[precio, cantidad], ...]"""
    return [[nivel[0], nivel[1]] for nivel in niveles[:n]]


def _toca_libro_crudo(estado, libro_crudo_cada):
    ahora = time.monotonic()
    if ahora - estado["libro_crudo_ultimo"] < libro_crudo_cada:
        return False
    estado["libro_crudo_ultimo"] = ahora
    return True


UMBRAL_HUECO_SEG = 15.0
TOPE_HUECO_SEG = 600.0
TOPE_LLAMADAS_RECUPERACION = 10
TOPE_TRADES_RECUPERACION = 5000


def _ruta_huecos(coin):
    return os.path.join(DIR_GRABADOR, f"huecos_{coin.upper()}.csv")


def _registrar_hueco(coin, ts_previo, duracion_seg, estado_txt, n_trades, vol):
    ruta = _ruta_huecos(coin)
    nuevo = not os.path.exists(ruta)
    with open(ruta, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HUECOS)
        if nuevo:
            w.writeheader()
        ahora = datetime.now(timezone.utc)
        w.writerow({
            "timestamp_ms": int(ahora.timestamp() * 1000),
            "fecha_utc": ahora.strftime("%Y-%m-%d %H:%M:%S"),
            "coin": coin.upper(),
            "ts_ultimo_trade_previo": ts_previo if ts_previo is not None else "",
            "duracion_seg": round(duracion_seg, 1),
            "estado": estado_txt,
            "n_trades_recuperados": n_trades,
            "vol_recuperado": round(vol, 6),
            "pid": os.getpid(),
        })


async def _recuperar_hueco(coin, simbolo, estado):
    """_recuperar_hueco(coin, simbolo, estado) -> None"""
    ts_previo = estado.get("ultimo_trade_ts")
    if ts_previo is None:
        return

    duracion = (int(time.time() * 1000) - ts_previo) / 1000.0
    if duracion > TOPE_HUECO_SEG:
        _registrar_hueco(coin, ts_previo, duracion, "no_intentado_hueco_grande", 0, 0.0)
        print(f"  (hueco) {coin}: {duracion:.0f}s, supera el tope de {TOPE_HUECO_SEG:.0f}s, no se intenta recuperar")
        return

    loop = asyncio.get_running_loop()
    n_nuevos, vol_nuevo, llamadas, cursor = 0, 0.0, 0, ts_previo
    try:
        while llamadas < TOPE_LLAMADAS_RECUPERACION:
            llamadas += 1
            lote = await loop.run_in_executor(None, lambda c=cursor: datos.trades(simbolo, desde=c, limite=500))
            if not lote:
                break
            for t in sorted(lote, key=lambda x: x.get("timestamp") or 0):
                tt = t.get("timestamp")
                if tt is None or tt < ts_previo:
                    continue
                if _procesar_trade(estado, t):
                    n_nuevos += 1
                    vol_nuevo += t.get("amount") or 0.0
            marcas = [t.get("timestamp") for t in lote if t.get("timestamp") is not None]
            if not marcas:
                break
            nuevo_cursor = max(marcas)
            fin = nuevo_cursor <= cursor or len(lote) < 500 or n_nuevos >= TOPE_TRADES_RECUPERACION
            cursor = nuevo_cursor
            if fin:
                break
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"  (aviso) recuperacion de hueco {coin}: {e}")
        _registrar_hueco(coin, ts_previo, duracion, "error", n_nuevos, vol_nuevo)
        return

    parcial = llamadas >= TOPE_LLAMADAS_RECUPERACION or n_nuevos >= TOPE_TRADES_RECUPERACION
    estado_txt = "parcial" if parcial else ("completo" if n_nuevos else "sin_datos_nuevos")
    _registrar_hueco(coin, ts_previo, duracion, estado_txt, n_nuevos, vol_nuevo)
    print(f"  (hueco) {coin}: {duracion:.1f}s, {estado_txt}, {n_nuevos} trades recuperados")


async def _recuperar_al_arrancar(coin, simbolo, estado):
    """_recuperar_al_arrancar(coin, simbolo, estado) -> None"""
    if estado["ultimo_trade_ts"] is None:
        return
    await _recuperar_hueco(coin, simbolo, estado)


async def _watch_book(exchange, coin, simbolo, estado):
    while True:
        try:
            ob = await exchange.watch_order_book(simbolo)
            bids, asks = ob["bids"], ob["asks"]
            if bids and asks and bids[0][0] > asks[0][0]:
                print(f"  (aviso) libro {coin}: cruzado (bid {bids[0][0]} > ask {asks[0][0]}), "
                      f"forzando resuscripcion...")
                await exchange.un_watch_order_book(simbolo)
                continue
            ahora = time.monotonic()
            if estado["libro_ts"] is not None and (ahora - estado["libro_ts"]) > UMBRAL_HUECO_SEG:
                await _recuperar_hueco(coin, simbolo, estado)
            estado["libro"] = ob
            estado["libro_ts"] = ahora
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"  (aviso) libro {coin}: {e}")
            await asyncio.sleep(2)


async def _watch_trades(exchange, coin, simbolo, estado):
    while True:
        try:
            trades = await exchange.watch_trades(simbolo)
            for t in trades:
                _procesar_trade(estado, t)
            _podar_ids_recientes(estado)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"  (aviso) trades {coin}: {e}")
            await asyncio.sleep(2)


async def _watch_ticker(exchange, coin, simbolo, estado):
    while True:
        try:
            ticker = await exchange.watch_ticker(simbolo)
            info = ticker.get("info", {})
            estado["funding"] = _flt(info.get("fundingRate"))
            estado["oi"] = _flt(info.get("holdingAmount"))
            estado["ticker_ts"] = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"  (aviso) ticker {coin}: {e}")
            await asyncio.sleep(2)


async def _actualizar_ls_ratio(coin, simbolo, estado, params):
    """_actualizar_ls_ratio(coin, simbolo, estado, params) -> None"""
    loop = asyncio.get_running_loop()
    while True:
        try:
            valor = await loop.run_in_executor(None, datos.long_short_ratio, simbolo)
            if valor is not None:
                estado["ls_ratio"] = valor
        except asyncio.CancelledError:
            raise
        except datos.SinDatoParaSimbolo:
            print(f"  (aviso) long_short_ratio {coin}: Bitget no tiene este dato para "
                  f"{simbolo}, no se volvera a pedir en esta sesion (columna queda en blanco).")
            return
        except Exception as e:
            print(f"  (aviso) long_short_ratio {coin}: {e}")
        await asyncio.sleep(params["ls_ratio_cada"])


def _iniciar_coin(exchange, coin, simbolo, params, estados, tareas, arch, writer):
    """_iniciar_coin(exchange, coin, simbolo, params, estados, tareas, arch, writer) -> bool"""
    if not _lock_libre_o_huerfano(coin):
        print(f"(aviso) {coin}: ya hay un grabador_libro.py corriendo para esta moneda, no se anade.")
        return False
    _bloquear_coin(coin)

    ruta = _ruta_compatible(_archivo(coin))
    nuevo = not os.path.exists(ruta)
    arch[coin] = open(ruta, "a", newline="")
    writer[coin] = csv.DictWriter(arch[coin], fieldnames=CAMPOS_CSV)
    if nuevo:
        writer[coin].writeheader()
        arch[coin].flush()

    cvd_previo = _ultimo_cvd(_archivo(coin))
    estados[coin] = _crear_estado(cvd_previo)
    if cvd_previo is not None:
        print(f"  {coin}: CVD retomado en {cvd_previo:+.4f} (fichero existente)")
    else:
        print(f"  {coin}: CVD arranca en 0 (sin fichero previo, o con cabecera pero sin filas)")

    estado = estados[coin]
    ts_cursor, ids_cursor = _cargar_cursor(coin)
    if ts_cursor is not None:
        estado["ultimo_trade_ts"] = ts_cursor
        estado["ids_en_ultimo_ts"] = set(ids_cursor)
        estado["ids_recientes"] = {tid: time.monotonic() for tid in ids_cursor}

    tareas[coin] = [
        asyncio.create_task(_recuperar_al_arrancar(coin, simbolo, estado), name=f"recuperacion_arranque_{coin}"),
        asyncio.create_task(_watch_book(exchange, coin, simbolo, estado), name=f"libro_{coin}"),
        asyncio.create_task(_watch_trades(exchange, coin, simbolo, estado), name=f"trades_{coin}"),
        asyncio.create_task(_watch_ticker(exchange, coin, simbolo, estado), name=f"ticker_{coin}"),
        asyncio.create_task(_actualizar_ls_ratio(coin, simbolo, estado, params), name=f"lsratio_{coin}"),
    ]
    print(f"  {coin} -> {ruta}")
    return True


async def _detener_coin(coin, estados, tareas, arch, writer):
    """_detener_coin(coin, estados, tareas, arch, writer) -> None"""
    lista = tareas.pop(coin, [])
    for t in lista:
        t.cancel()
    if lista:
        await asyncio.gather(*lista, return_exceptions=True)
    estados.pop(coin, None)
    if coin in arch:
        arch[coin].close()
        del arch[coin]
    writer.pop(coin, None)
    _desbloquear_coin(coin)
    print(f"  {coin}: parado y lock liberado.")


async def _reiniciar_coin(exchange, coin, simbolo, params, estados, tareas, arch, writer):
    await _detener_coin(coin, estados, tareas, arch, writer)
    _iniciar_coin(exchange, coin, simbolo, params, estados, tareas, arch, writer)
    print(f"(comando) {coin}: reiniciada en caliente.")


def _procesar_comandos(exchange, coins_activas, params, simbolos, estados, tareas, arch, writer):
    """_procesar_comandos(...) -> None. Lee JSON pendientes en DIR_COMANDOS."""
    if not os.path.isdir(DIR_COMANDOS):
        return
    for nombre in os.listdir(DIR_COMANDOS):
        if not nombre.endswith(".json"):
            continue
        ruta_cmd = os.path.join(DIR_COMANDOS, nombre)
        try:
            with open(ruta_cmd) as f:
                cmd = json.load(f)
        except (OSError, ValueError):
            continue
        coin_cmd = str(cmd.get("coin", "")).upper()
        accion = cmd.get("accion")

        if accion == "anadir_coin":
            if coin_cmd in coins_activas:
                print(f"(aviso) comando ignorado: {coin_cmd} ya esta activa.")
            else:
                simbolo = datos.normalizar_simbolo(coin_cmd, "f")[0]
                simbolos[coin_cmd] = simbolo
                if _iniciar_coin(exchange, coin_cmd, simbolo, params, estados, tareas, arch, writer):
                    coins_activas.add(coin_cmd)
                    print(f"(comando) {coin_cmd}: anadida en caliente.")
            os.remove(ruta_cmd)
            continue

        if accion == "quitar_coin":
            if coin_cmd not in coins_activas:
                print(f"(aviso) comando ignorado: {coin_cmd} no esta activa.")
            else:
                coins_activas.discard(coin_cmd)
                asyncio.create_task(_detener_coin(coin_cmd, estados, tareas, arch, writer))
                print(f"(comando) {coin_cmd}: quitada en caliente.")
            os.remove(ruta_cmd)
            continue

        if accion == "reiniciar_coin":
            if coin_cmd not in coins_activas:
                print(f"(aviso) comando ignorado: {coin_cmd} no esta activa, usa anadir_coin.")
            else:
                asyncio.create_task(_reiniciar_coin(
                    exchange, coin_cmd, simbolos.get(coin_cmd), params, estados, tareas, arch, writer))
            os.remove(ruta_cmd)
            continue

        if coin_cmd not in coins_activas:
            continue

        if accion == "reset":
            params.clear()
            params.update(PARAMS_DEFECTO)
            print(f"(comando) {coin_cmd}: parametros reseteados a valores por defecto.")
        else:
            parametro, valor = cmd.get("parametro"), cmd.get("valor")
            if parametro not in LIMITES_PARAMS:
                print(f"(aviso) comando ignorado: parametro desconocido {parametro!r}")
                os.remove(ruta_cmd)
                continue
            try:
                valor = float(valor)
            except (TypeError, ValueError):
                print(f"(aviso) comando ignorado: valor invalido para {parametro}: {valor!r}")
                os.remove(ruta_cmd)
                continue
            lo, hi = LIMITES_PARAMS[parametro]
            if not (lo <= valor <= hi):
                print(f"(aviso) comando ignorado: {parametro}={valor} fuera de rango [{lo},{hi}]")
                os.remove(ruta_cmd)
                continue
            if parametro == "profundidad":
                valor = int(valor)
            params[parametro] = valor
            print(f"(comando) {coin_cmd}: {parametro} = {valor}")

        _guardar_config(params)
        os.remove(ruta_cmd)


def _fila(coin, estado, params):
    """_fila(coin, estado, params) -> dict, una fila del CSV"""
    ahora_mono = time.monotonic()
    umbral = max(2 * params["cada"], UMBRAL_HUECO_SEG)
    fresco_libro = estado["libro_ts"] is not None and (ahora_mono - estado["libro_ts"]) <= umbral
    fresco_ticker = estado["ticker_ts"] is not None and (ahora_mono - estado["ticker_ts"]) <= umbral

    libro = estado["libro"] if fresco_libro else None
    bids = libro["bids"] if libro else None
    asks = libro["asks"] if libro else None
    bid = bids[0][0] if bids else None
    ask = asks[0][0] if asks else None

    n_trades = estado["n_trades_fila"]
    vol_buy = estado["vol_buy_fila"]
    vol_sell = estado["vol_sell_fila"]
    estado["n_trades_fila"] = 0
    estado["vol_buy_fila"] = 0.0
    estado["vol_sell_fila"] = 0.0

    grabar_crudo = _toca_libro_crudo(estado, params["libro_crudo_cada"])
    profundidad = params["profundidad"]

    ahora = datetime.now(timezone.utc)
    return {
        "timestamp_ms": int(ahora.timestamp() * 1000),
        "fecha_utc": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "bid": bid if bid is not None else "",
        "ask": ask if ask is not None else "",
        "spread_bps": flujo.spread_bps(libro) if libro else "",
        "mid": flujo.mid(libro) if libro else "",
        "microprecio": flujo.microprecio(libro) if libro else "",
        "imbalance": flujo.imbalance(libro, niveles=10) if libro else "",
        "imbalance_niveles": 10,
        "open_interest": estado["oi"] if (fresco_ticker and estado["oi"] is not None) else "",
        "funding_rate_pct": estado["funding"] * 100 if (fresco_ticker and estado["funding"] is not None) else "",
        "long_short_ratio": estado["ls_ratio"] if estado["ls_ratio"] is not None else "",
        "n_trades": n_trades,
        "vol_buy": round(vol_buy, 6),
        "vol_sell": round(vol_sell, 6),
        "delta_vol": round(vol_buy - vol_sell, 6),
        "cvd": round(estado["cvd"], 6),
        "bids_json": json.dumps(_niveles_limpios(bids, profundidad)) if (grabar_crudo and bids) else "",
        "asks_json": json.dumps(_niveles_limpios(asks, profundidad)) if (grabar_crudo and asks) else "",
        "pid": os.getpid(),
    }


async def main_async():
    args = sys.argv[1:]
    if args and not args[0].startswith("--"):
        coins = [c.strip().upper() for c in args[0].split(",")]
        args = args[1:]
    else:
        coins = ["BTC", "ETH"]

    cli = {"cada": None, "profundidad": None, "libro_crudo_cada": None, "ls_ratio_cada": None}
    i = 0
    while i < len(args):
        if args[i] == "--cada":
            i += 1
            cli["cada"] = float(args[i])
        elif args[i] == "--profundidad":
            i += 1
            cli["profundidad"] = int(args[i])
        elif args[i] == "--libro-crudo-cada":
            i += 1
            cli["libro_crudo_cada"] = float(args[i])
        elif args[i] == "--ls-ratio-cada":
            i += 1
            cli["ls_ratio_cada"] = float(args[i])
        i += 1

    os.makedirs(DIR_GRABADOR, exist_ok=True)
    _bloquear_instancia_unica(coins)

    params = _cargar_config()
    for clave, valor in cli.items():
        if valor is not None:
            params[clave] = valor

    _conector_ws = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    _session_ws = aiohttp.ClientSession(connector=_conector_ws)
    exchange = ccxtpro.bitget({
        "apiKey": os.getenv("BITGET_API_KEY"),
        "secret": os.getenv("BITGET_SECRET_KEY"),
        "password": os.getenv("BITGET_PASSPHRASE"),
        "enableRateLimit": True,
        "session": _session_ws,
    })

    print("Cargando mercados...")
    await exchange.load_markets()

    simbolos = {c: datos.normalizar_simbolo(c, "f")[0] for c in coins}
    estados, tareas, arch, writer = {}, {}, {}, {}
    coins_activas = set()

    for coin in coins:
        if _iniciar_coin(exchange, coin, simbolos[coin], params, estados, tareas, arch, writer):
            coins_activas.add(coin)

    if not coins_activas:
        print("ERROR: ninguna moneda pudo arrancar.")
        await exchange.close()
        await _session_ws.close()
        return

    print(f"Grabando libro/trades/funding/OI (WS) + L/S ratio (REST) de "
          f"{', '.join(sorted(coins_activas))}, fila cada {params['cada']:.0f}s "
          f"(hasta {params['profundidad']} niveles guardados).")
    print(f"L/S ratio cada {params['ls_ratio_cada']:.0f}s. Libro crudo (bids_json/asks_json) "
          f"cada {params['libro_crudo_cada']:.0f}s.")
    print(f"Ajuste en caliente / anadir-quitar-reiniciar moneda via telegram_control.py: {DIR_COMANDOS}")
    print("Ctrl+C para parar.")

    try:
        while True:
            _procesar_comandos(exchange, coins_activas, params, simbolos, estados, tareas, arch, writer)
            for coin in list(coins_activas):
                estado = estados.get(coin)
                if estado is None or coin not in writer:
                    continue
                fila = _fila(coin, estado, params)
                writer[coin].writerow(fila)
                arch[coin].flush()
                _guardar_cursor(coin, estado)
            await asyncio.sleep(params["cada"])
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nParado por el usuario.")
    finally:
        for coin in list(coins_activas):
            await _detener_coin(coin, estados, tareas, arch, writer)
        await exchange.close()
        await _session_ws.close()


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
