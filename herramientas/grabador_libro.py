# ---------------------------------------------------------------
# grabador_libro.py - Graba en CSV lo UNICO que no se puede reconstruir
# despues a partir de velas: el libro de ordenes COMPLETO (resumen +
# crudo nivel a nivel), el open interest, el funding rate, el trade flow
# (CVD) y el long/short ratio agregado del mercado.
#
# REESCRITO 2026-08-13: de polling REST sincrono a WebSocket (Bitget v2,
# via ccxt.pro) - motivado por dos bugs reales de CVD en la version REST
# (cursor de timestamp perdiendo trades del mismo milisegundo, luego un
# KeyError en el resume tras reinicio) que venian de la complejidad de
# mantener un cursor de paginacion sobre fetch_trades. Con WS cada trade
# llega empujado UNA vez, sin paginar - se elimina esa complejidad en el
# caso normal (ver _procesar_trade). Ademas: libro/trades/funding/OI viven
# ahora en una UNICA conexion consolidada que cubre TODAS las monedas del
# proceso (antes: una llamada REST independiente por dato y por moneda,
# cada `--cada` segundos).
#
# Decisiones tomadas tras una prueba en vivo (ver plan de la sesion, y
# herramientas/_prueba_ws_bitget.py que la hizo):
#   - canal `books50` da error 30016 "Param error" en USDT-FUTURES (solo
#     existe para spot) - se usa el canal `books` incremental SIN limite,
#     cuyo checksum CRC32 gestiona ccxt.pro por dentro (no hay que
#     implementar nada a mano). Da MUCHOS mas niveles de los esperados
#     (500 en BTC/ETH, 200 en ICP en la prueba) - se guardan TODOS por
#     defecto (Fran, 2026-08-13: "guardemos todo ya que lo tenemos").
#   - funding rate y open interest van por el canal `ticker` (antes REST,
#     cacheado cada `--funding-cada` - ese parametro YA NO EXISTE, el
#     ticker empuja solo, no hay nada que cachear/pedir).
#   - long/short ratio SIGUE por REST (Bitget no lo transmite en directo,
#     es un calculo periodico) - unico dato que sigue siendo poll, ahora
#     via loop.run_in_executor para no bloquear el event loop.
#   - UN SOLO PROCESO/conexion para todas las monedas (antes: un proceso
#     por moneda con lock propio) - Bitget soporta nativamente suscribir
#     varias monedas en un mismo mensaje y anadir/quitar en caliente sobre
#     la misma conexion. El aislamiento que antes daba gratis el SO (un
#     fallo de una moneda no tumbaba las demas) se reconstruye aqui a mano:
#     cada moneda tiene sus propias tareas asyncio con su propio
#     try/except (ver _watch_book/_watch_trades/_watch_ticker) - un fallo
#     en una no toca las de otra.
#   - Recuperacion acotada de huecos: al detectar (via el "latido" del
#     libro, que en la prueba actualizaba decenas de veces por segundo)
#     que ha pasado demasiado tiempo sin actualizacion, se asume un corte
#     y se intenta rellenar los trades perdidos por REST (misma funcion
#     mercado.datos.trades() ya probada) - acotado a 5 minutos y a un
#     numero maximo de llamadas, para no fiarse a ciegas de un historico
#     REST que ya sabemos que no es profundo en Bitget (ver
#     mercado/datos.py.trades()). Se registra SIEMPRE en
#     huecos_<COIN>.csv, se haya podido recuperar o no - para poder
#     consultar en operaciones "¿ha habido algun hueco sin recuperar
#     recientemente?" antes de fiarse de una señal (Fran, 2026-08-13).
#
# Cada script escribe ahora en su PROPIA carpeta dentro de herramientas/
# (Fran, 2026-08-13) - este en herramientas/grabador_libro/, ya NO en el
# herramientas/libro/ compartido con descargar_bit.py y los monitores. Los
# consumidores externos (monitor_comun.py._localizar_csv_libro/
# _requerir_grabador_libro, herramientas/verificar_flujo.py) se han
# actualizado para buscar aqui.
#
# Velas (y por tanto RSI/EMA/ATR/todo lo que sale de ellas) SI tienen
# historico en el exchange - un reinicio de monitor.py no las pierde, las
# vuelve a pedir. El libro (liquidez en reposo) y el open interest NO tienen
# historico en NINGUN exchange (ni Bitget ni Binance) - si no se graban EN
# VIVO en el momento, se pierden para siempre (ver anotaciones.md
# 2026-08-03: "lo que SI carece de historico y obliga a grabar en vivo es
# solo el libro de ordenes").
#
# CVD (Cumulative Volume Delta): suma acumulada de (volumen comprador -
# volumen vendedor) de trades YA EJECUTADOS, por el lado del AGRESOR (quien
# cruzo el spread, no quien puso la orden pasiva) - distinto del libro, que
# es liquidez EN REPOSO todavia sin ejecutar. Un CVD subiendo con precio
# lateral/bajando (o viceversa) es la divergencia clasica que se usa como
# señal de agotamiento.
#
# Al arrancar, si ya existe flujo_<COIN>.csv de esa moneda (reinicio, no la
# primera vez), retoma el CVD desde la ultima fila YA GRABADA en vez de
# resetear a 0 (ver _ultimo_cvd) - el valor esta ahi precisamente porque se
# guarda para no perderlo. Solo empieza en 0 cuando de verdad no hay nada
# previo (fichero nuevo).
#
# El libro se graba DOBLE: el resumen (bid/ask/spread/mid/microprecio/
# imbalance, para leer rapido sin parsear JSON) Y el crudo completo
# (bids_json/asks_json, hasta --profundidad niveles, cada
# --libro-crudo-cada segundos) - el resumen fija niveles=10 para el
# imbalance a proposito; el crudo permite recalcularlo despues con otro
# 'niveles', u otra metrica que ni se nos ocurrio todavia.
#
# Proceso SEPARADO de cualquier logica de trading/señales (2026-08-06,
# origen: separado en su dia de monitor.py, el bot multi-posicion que vive
# solo en la rama master - no existe en senales-vela, ver anotaciones.md):
# esos procesos se reinician con frecuencia (ajustes de parametros,
# calibracion de señales), y cada reinicio cortaba esta serie sin motivo
# real. Este proceso no tiene ninguna logica de trading, solo escribe.
#
# El historico de velas de Bitget (herramientas/libro/historico_*_bitget.csv)
# NO lo mantiene este proceso - lo mantiene descargar_bit.py --feed, tambien
# siempre corriendo en el NAS pero como proceso independiente.
#
# Uso (desde la raiz del repo):
#   python herramientas/grabador_libro.py [coin[,coin2,...]] [--cada 15]
#                             [--profundidad 1000]
#                             [--libro-crudo-cada 60] [--ls-ratio-cada 300]
#   coin por defecto: btc,eth
#
# Ajuste en caliente y anadir/quitar/reiniciar una moneda SIN reiniciar el
# proceso: dejar caer un JSON en herramientas/grabador_libro/comandos_grabador/
# (ver _procesar_comandos) - pensado para telegram_control.py, pero vale
# tambien a mano.
#
# Ejemplos:
#   python herramientas/grabador_libro.py
#   python herramientas/grabador_libro.py btc,eth,icp
#   python herramientas/grabador_libro.py btc,eth --cada 30
# ---------------------------------------------------------------

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

# 2026-08-13: carpeta propia, ya no comparte herramientas/libro/ con
# descargar_bit.py/los monitores (nota de Fran: "cada py que lanzamos
# escriba en una carpeta").
DIR_GRABADOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grabador_libro")

# Sin "coin": un fichero por moneda, ya no hace falta distinguir de que
# moneda es cada fila dentro del propio CSV - lo dice el nombre del
# fichero (flujo_<COIN>.csv).
CAMPOS_CSV = [
    "timestamp_ms", "fecha_utc",
    "bid", "ask", "spread_bps", "mid", "microprecio",
    "imbalance", "imbalance_niveles",
    "open_interest", "funding_rate_pct", "long_short_ratio",
    "n_trades", "vol_buy", "vol_sell", "delta_vol", "cvd",
    "bids_json", "asks_json",
    "pid",  # PID del proceso que escribio la fila.
]

# Registro de huecos de conexion WS detectados (ver _recuperar_hueco) - una
# fila por hueco, se recupere o no, para poder consultar en operaciones si
# hay motivo de duda reciente sobre los datos.
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
    """flujo_<COIN>.csv en herramientas/grabador_libro/ - UN fichero POR
    MONEDA. SIN fecha en el nombre: un reinicio debe seguir escribiendo el
    MISMO fichero para poder retomar el CVD desde la ultima fila ya
    grabada (ver _ultimo_cvd) en vez de perderlo cada vez que se relanza
    el proceso."""
    os.makedirs(DIR_GRABADOR, exist_ok=True)
    return os.path.join(DIR_GRABADOR, f"flujo_{coin.upper()}.csv")


def _ruta_compatible(ruta):
    """Si ya existe con OTRA cabecera, no se reescribe encima - se abre
    _v2/_v3..."""
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
    """True si el proceso 'pid' sigue vivo, SIN arriesgarse a matarlo -
    misma logica que monitor_comun._pid_vivo (duplicada, no importada: ese
    modulo ya importa DE grabador_libro.py, importar en el otro sentido
    crearia un ciclo).

    En POSIX (el NAS, donde corre esto normalmente), os.kill(pid, 0) es el
    patron estandar: la señal 0 no se entrega, solo prueba existencia. En
    Windows, os.kill() con cualquier señal que no sea CTRL_C_EVENT/
    CTRL_BREAK_EVENT llama de verdad a TerminateProcess() - si el PID
    leido del .lock coincidiera por casualidad con un proceso vivo en la
    maquina Windows, esto lo mataria en vez de solo comprobarlo. Se evita
    abriendo el proceso con permisos de SOLO CONSULTA
    (PROCESS_QUERY_LIMITED_INFORMATION) en vez de con os.kill (ver
    anotaciones.md 2026-08-12)."""
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
    # restype/argtypes explicitos: sin esto ctypes asume que OpenProcess
    # devuelve un 'int' de 32 bits, pero un HANDLE de Windows es de 64 bits
    # en sistemas x64.
    abrir.restype = wt.HANDLE
    abrir.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    handle = abrir(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _lock_libre_o_huerfano(coin):
    """True si no hay lock para 'coin', o si lo hay pero el PID de dentro
    ya no existe (huerfano, se puede pisar). Solo consulta, no escribe."""
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
    """Escribe el lock de 'coin' con el PID de ESTE proceso. Con el
    proceso consolidado (2026-08-13, una conexion para varias monedas)
    todos los locks activos de un mismo proceso llevan el MISMO pid - es
    lo esperado, ya no significa "un proceso por moneda"."""
    with open(_ruta_lock(coin), "w") as f:
        f.write(str(os.getpid()))


def _desbloquear_coin(coin):
    try:
        os.remove(_ruta_lock(coin))
    except OSError:
        pass


def _bloquear_instancia_unica(coins):
    """Arranque: si CUALQUIERA de las monedas pedidas ya tiene un lock
    vivo, aborta ANTES de tocar ningun fichero - un arranque parcial
    silencioso confundiria mas de lo que ayuda.

    Solo COMPRUEBA, NO escribe ningun lock - eso lo hace _iniciar_coin
    (unico sitio que adquiere el lock de una moneda, tanto al arrancar
    como al anadir/reiniciar en caliente). Si esta funcion tambien
    escribiera el lock, el _lock_libre_o_huerfano() de _iniciar_coin veria
    su PROPIO lock recien escrito por este mismo proceso y se negaria a
    arrancar - bug real, encontrado 2026-08-13 en la primera prueba en
    vivo: las 3 monedas se rechazaban a si mismas con "ya hay un
    grabador_libro.py corriendo"."""
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
    """CVD de la ultima fila ya grabada en 'ruta' - lee solo la cola del
    fichero en vez de cargarlo entero. None si 'ruta' no existe todavia, o
    no hay ninguna fila valida (solo cabecera)."""
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
    """Persiste el ultimo trade_ts (y los ids de trades EN ese ts, para
    proteger el empate de milisegundo) cada vez que se escribe una fila -
    misma cadencia que el CVD, para que ambos queden siempre consistentes
    entre si. Es lo que permite que _recuperar_al_arrancar() trate un
    reinicio del proceso igual que _recuperar_hueco() ya trata un corte de
    WS en caliente: sin esto, el tramo entre la ULTIMA fila escrita y el
    momento real de parar el proceso (hasta --cada segundos, incluso en un
    apagado limpio) se perderia siempre en cada reinicio, planificado o
    no."""
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
    """(ultimo_trade_ts, {ids ya contados en ese ts}) del cursor persistido,
    o (None, set()) si no existe/esta corrupto (primera vez, o un reinicio
    tan viejo que nunca llego a escribir ninguna fila)."""
    ruta = _ruta_cursor(coin)
    if not os.path.exists(ruta):
        return None, set()
    try:
        with open(ruta) as f:
            data = json.load(f)
        return data.get("ultimo_trade_ts"), set(data.get("ids_en_ultimo_ts", []))
    except (OSError, ValueError):
        return None, set()


# ---------------------------------------------------------------- ajuste en caliente

PARAMS_DEFECTO = {
    "cada": 15.0, "profundidad": 1000,
    "libro_crudo_cada": 60.0, "ls_ratio_cada": 300.0,
}
# "funding_cada" YA NO EXISTE (2026-08-13): con REST habia que cachear el
# poll; con el canal 'ticker' de WS el funding/OI llega empujado solo, no
# hay nada que espaciar.

LIMITES_PARAMS = {
    "cada": (1.0, 300.0), "profundidad": (1, 5000),
    "libro_crudo_cada": (10.0, 86400.0), "ls_ratio_cada": (10.0, 86400.0),
}

DIR_COMANDOS = os.path.join(DIR_GRABADOR, "comandos_grabador")


def _ruta_config():
    """Un unico config de PROCESO (2026-08-13) - antes se guardaba
    keyed a la primera moneda del proceso, pero con el proceso consolidado
    cubriendo varias monedas dinamicas (anadir_coin/quitar_coin) ya no hay
    una moneda "representante" clara. Los 5(4) parametros siguen siendo
    de PROCESO, no por moneda individual."""
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


# ---------------------------------------------------------------- estado en memoria

def _crear_estado(cvd_previo):
    return {
        "cvd": cvd_previo if cvd_previo is not None else 0.0,
        "libro": None, "libro_ts": None,
        "funding": None, "oi": None, "ticker_ts": None,
        "ls_ratio": None,
        "ultimo_trade_ts": None,
        "ids_en_ultimo_ts": set(),  # ids de trades EN 'ultimo_trade_ts' (puede haber varios
                                     # en el mismo ms) - persistido en cursor_<COIN>.json para
                                     # que un reinicio pueda sembrar 'ids_recientes' y no
                                     # perder la proteccion de empate que ya tiene el hueco en
                                     # vivo (ver _guardar_cursor/_recuperar_al_arrancar).
        "ids_recientes": {},  # {trade_id: monotonic al verlo} - dedup/red de seguridad
        "n_trades_fila": 0, "vol_buy_fila": 0.0, "vol_sell_fila": 0.0,
        "libro_crudo_ultimo": 0.0,
    }


def _procesar_trade(estado, t):
    """Aplica un trade al CVD/contadores de la fila en curso, con dedup por
    id (red de seguridad frente a redelivery en el borde de una
    reconexion o de una recuperacion de hueco - ver cabecera del
    archivo). Devuelve True si se conto, False si ya se habia visto."""
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
    """[precio, cantidad] de los primeros 'n' niveles - ccxt.pro guarda
    internamente un TERCER elemento por nivel (el par crudo en string,
    usado para el checksum CRC32 del canal incremental, ver
    ccxt/pro/bitget.py.handle_delta) que no interesa persistir: infla el
    JSON casi al doble sin aportar nada que _mejor/_volumen de
    mercado/flujo.py no lean ya por indice [0]/[1]."""
    return [[nivel[0], nivel[1]] for nivel in niveles[:n]]


def _toca_libro_crudo(estado, libro_crudo_cada):
    ahora = time.monotonic()
    if ahora - estado["libro_crudo_ultimo"] < libro_crudo_cada:
        return False
    estado["libro_crudo_ultimo"] = ahora
    return True


# ---------------------------------------------------------------- recuperacion de huecos

UMBRAL_HUECO_SEG = 15.0  # sin actualizacion de libro en mas de esto, se asume corte
TOPE_HUECO_SEG = 600.0  # huecos mayores de 10 min no se intentan recuperar por REST -
                         # subido de 300 (2026-08-17): un hueco de 347.8s en ICP tras
                         # un relanzamiento quedo sin intentar por los pelos, y Bitget
                         # SI tenia los trades disponibles (comprobado y recuperado a
                         # mano, ver anotaciones.md) - 300s era mas conservador de lo
                         # necesario para el caso tipico (reinicio del proceso), sigue
                         # habiendo un tope para no fiarse de un historico REST que en
                         # general no es profundo (ver mercado/datos.py.trades()).
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
    """Se llama cuando _watch_book detecta que ha pasado demasiado tiempo
    sin actualizacion de libro (UMBRAL_HUECO_SEG) - se asume un corte de
    WS y se intenta rellenar los trades perdidos con
    mercado.datos.trades(desde=...) (REST, la misma funcion ya probada en
    produccion). Acotado a TOPE_HUECO_SEG: Bitget "No tiene historico
    profundo garantizado" (ver mercado/datos.py.trades()) - para un hueco
    largo no merece la pena ni es fiable intentarlo, se registra como no
    recuperado y el CVD sigue desde donde esta (mismo criterio honesto que
    ya tenia la version REST durante un corte largo: hueco visible, no
    dato inventado).

    SIEMPRE se registra en huecos_<COIN>.csv, se recupere o no - para
    poder consultar en operaciones si hay motivo de duda reciente."""
    ts_previo = estado.get("ultimo_trade_ts")
    if ts_previo is None:
        return  # todavia no se ha visto ni un trade, nada que recuperar

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
                # < estricto, NO <=: Bitget devuelve 'since' inclusive, y
                # puede haber varios trades reales en el MISMO milisegundo
                # que ts_previo (documentado: hasta 37 en un solo ms) - a
                # igualdad de timestamp, _procesar_trade() decide por id ya
                # visto, igual que en el ingest normal (ver anotaciones.md
                # 2026-08-12). Con <= se descartaban TODOS los del ms
                # frontera sin comprobar id, perdiendo en silencio los que
                # de verdad eran nuevos - mismo bug que ya se arreglo ahi,
                # reintroducido aqui en la reescritura WS del 2026-08-13.
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
    """Trata un reinicio del proceso (planificado o no, kill/crash/reinicio
    del NAS) igual que _recuperar_hueco() ya trata un corte de WS en
    caliente - antes SOLO se disparaba desde _watch_book() con el proceso
    YA corriendo, asi que cualquier reinicio perdia el tramo en silencio,
    sin pasar por huecos_<COIN>.csv ni intentar rellenarlo por REST.

    'estado' ya viene sembrado en _iniciar_coin() con el cursor persistido
    (_cargar_cursor) - mismo 'ultimo_trade_ts' + 'ids_recientes' que
    tendria si el proceso nunca se hubiera parado, asi que _recuperar_hueco
    puede reusarse tal cual: mismo tope de 5 minutos, mismo registro en
    huecos_<COIN>.csv, misma proteccion de empate de milisegundo por id
    (la razon de persistir los ids en _guardar_cursor en vez de solo el
    timestamp)."""
    if estado["ultimo_trade_ts"] is None:
        return  # sin cursor previo (primera vez, o nunca llego a escribir una fila)
    await _recuperar_hueco(coin, simbolo, estado)


# ---------------------------------------------------------------- tareas WS por moneda

async def _watch_book(exchange, coin, simbolo, estado):
    while True:
        try:
            ob = await exchange.watch_order_book(simbolo)
            bids, asks = ob["bids"], ob["asks"]
            if bids and asks and bids[0][0] > asks[0][0]:
                # Libro cruzado: el estado interno que mantiene ccxt.pro se
                # desincronizo (visto en vivo 2026-08-16: bid congelado en un
                # unico valor entre 1h35 y ~13h mientras ask seguia
                # actualizandose con normalidad - ver anotaciones.md). El
                # propio checksum de ccxt (bitget.py/handle_order_book) tarda
                # demasiado o no siempre dispara su resuscripcion solo, asi
                # que se fuerza aqui: se descarta esta lectura (NO se guarda
                # en estado, para que _fila() la trate como libro
                # desactualizado via libro_ts en vez de escribir bid/ask
                # cruzados) y se pide a ccxt que tire su copia local del
                # libro y vuelva a suscribirse desde cero (snapshot limpio).
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
    """Unico dato que sigue siendo REST poll (Bitget no transmite L/S en
    directo) - via run_in_executor para no bloquear el event loop de las
    demas monedas mientras espera la respuesta HTTP.

    Si Bitget no tiene long/short ratio para 'simbolo' (datos.SinDatoParaSimbolo,
    visto en vivo con ICP: error 40054, "sin datos" es estructural, no un
    corte momentaneo) esta tarea termina en vez de seguir reintentando cada
    ls_ratio_cada para siempre - "long_short_ratio" queda en blanco en el
    CSV de esa moneda (mismo criterio de "blanco si no hay dato" que ya usa
    el resto de columnas), pero sin repetir el mismo aviso indefinidamente.
    Se vuelve a intentar una vez si el proceso se reinicia o la moneda se
    quita/anade en caliente - por si Bitget empieza a publicarlo mas
    adelante (ej. si sube el volumen/interes abierto de esa moneda)."""
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


# ---------------------------------------------------------------- ciclo de vida por moneda

def _iniciar_coin(exchange, coin, simbolo, params, estados, tareas, arch, writer):
    """Arranca todo lo necesario para 'coin': lock, fichero CSV (retomando
    CVD si ya existia) y las 4 tareas async. Usado tanto en el arranque
    normal como por el comando 'anadir_coin'/'reiniciar_coin' en caliente.
    False si ya habia un lock vivo para esta moneda (no aborta el
    proceso entero como en el arranque - solo esta moneda no se anade)."""
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
    # Cursor persistido (ver _guardar_cursor) - siembra ultimo_trade_ts/
    # ids_recientes ANTES de arrancar las tareas, para que
    # _recuperar_al_arrancar() pueda tratar el tramo hasta ahora igual que
    # un hueco de WS en caliente (ver su cabecera). None/vacio si es la
    # primera vez o no habia cursor previo - no rompe el arranque normal.
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
    """Cancela las tareas de 'coin', espera a que terminen de verdad,
    cierra su CSV y libera su lock - usado por 'quitar_coin' y como mitad
    de 'reiniciar_coin'."""
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


# ---------------------------------------------------------------- comandos en caliente

def _procesar_comandos(exchange, coins_activas, params, simbolos, estados, tareas, arch, writer):
    """Revisa DIR_COMANDOS por comandos pendientes - ajuste de los 4
    parametros de proceso, reset a valores por defecto, y (2026-08-13,
    nuevo con el proceso consolidado) anadir/quitar/reiniciar una moneda
    SIN reiniciar el proceso entero (antes, con un proceso por moneda,
    esto se hacia con kill+relanzar; ahora eso tumbaria TODAS las monedas
    de golpe)."""
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
            # comando de ajuste de parametro/reset para una moneda que
            # este proceso no cubre - no es para nosotros, se deja intacto
            # por si lo recoge otro proceso.
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


# ---------------------------------------------------------------- fila del CSV

def _fila(coin, estado, params):
    """Una fila del CSV a partir del estado en memoria (mantenido por las
    tareas WS) - NO pide nada activamente, solo muestrea lo que ya hay.
    Si el libro/ticker no se ha actualizado en mas de UMBRAL_HUECO_SEG (o
    2x --cada, lo que sea mayor), se deja en blanco en vez de repetir el
    ultimo valor conocido - mismo contrato honesto que ya tenia la version
    REST durante un corte (Fran, 2026-08-13: preferido a fingir datos
    frescos que no lo son)."""
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


# ---------------------------------------------------------------- main

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

    # session con ThreadedResolver (2026-08-14, ver anotaciones.md): sin
    # esto, aiohttp usa aiodns por defecto si esta instalado - en Windows
    # aiodns falla con "Could not contact DNS servers" en la primera
    # llamada real (confirmado en vivo: load_markets() de ESTA misma
    # construccion, sin session propia, contra Windows real de Fran).
    # ThreadedResolver no depende de aiodns/pycares, usa getaddrinfo()
    # estandar - funciona igual en Linux, donde ya corre este proceso hoy.
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
