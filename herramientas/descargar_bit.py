# ---------------------------------------------------------------
# descargar_bit.py - Baja/actualiza historico de velas de Bitget (futuros) a CSV
#
# Mismo símbolo/mercado que consulta monitor.py en vivo (datos.velas() con
# normalizar_simbolo(coin, "f") -> "<COIN>/USDT:USDT", futuros USDT-M). Menos
# histórico que Binance, pero son las velas EXACTAS que vio el monitor al
# decidir cada operación - para contrastar una sesión real usar este script,
# no descargar_bin.py (Binance es otro exchange, su OHLCV puede diferir por
# tener un volumen de negocio mucho más amplio).
#
# Pagina hasta cubrir el rango pedido (el límite por request lo impone
# Bitget, normalmente menor que el de Binance - por eso el corte de página
# se decide por "no llegaron velas nuevas", no por "llegaron menos de las
# pedidas", que con paginas mas pequeñas cortaria demasiado pronto). Nunca
# incluye la vela EN CURSO (todavía sin cerrar) - solo velas cerradas, igual
# que el resto del sistema (ver anotaciones.md: señales sobre velas[-2]).
#
# El CSV sale en el MISMO formato que descargar_bin.py:
#     timestamp,fecha_utc,open,high,low,close,volumen
# y con el nombre  historico_<COIN>_<TF>_bitget.csv, en herramientas/libro/
# (mismo sitio que flujo_*.csv de grabador_libro.py - todo lo de una sesion
# de captura en vivo junto, para las pruebas en frio despues).
#
# Tres formas de usarlo:
#   descargar(coin, tf, desde=...)  - SIEMPRE reescribe el fichero entero
#                                      desde 'desde' (o todo el historico).
#                                      Uso manual/puntual.
#   actualizar(coin, tf)            - si no hay fichero previo, baja
#                                      exactamente las velas de 'dias_objetivo'
#                                      dias (no de mas); si ya existe, lee la ultima
#                                      vela guardada y solo pide/AÑADE lo que
#                                      falta (append, sin reescribir), y
#                                      recorta el fichero si se pasa del cap
#                                      por margen de holgura. Pensado para
#                                      refrescar seguido sin bajar todo cada
#                                      vez.
#   --feed                          - modo daemon: corre sin parar en el NAS
#                                      (independiente de grabador_libro.py,
#                                      que solo graba libro/OI/funding/CVD -
#                                      ver su cabecera) llamando a
#                                      actualizar() por cada coin/tf cada
#                                      '--cada' segundos. Unico dueño de
#                                      mantener al dia herramientas/libro/
#                                      historico_<COIN>_<TF>_bitget.csv - el
#                                      resto de modulos (niveles_soporte.py,
#                                      monitor_niveles.py) son lectores puros.
#
# El cap de velas guardadas se calcula por DIAS_OBJETIVO (90 dias, igual
# para todos los TF), no por un numero fijo de velas. Se probo lo contrario
# primero (VELAS_OBJETIVO=500 velas flat, "un nivel de hace 90 dias en 1m
# ya esta roto o irrelevante") y se revirtio el 2026-08-11: al re-validar
# el backtest de senales.REFINADAS_CONFIRMADAS con la profundidad real que
# eso daba en vivo (~21d en 1h, ~5d en 15m en vez de 90), el edge se
# rompia de verdad (ruptura_baja_en_soporte pasaba a negativo en 15m) -
# ver mirar.md. Los 90 dias SI hacen falta, la intuicion de "irrelevante
# en TF finos" no se sostuvo con datos. max() contra senales.VENTANA_MAXIMA
# por si 90 dias dieran menos velas que eso (le pasa a 1d: 90 dias = 90
# velas, menos que las 500 que pediria un --tf-macro 1d).
#
# Uso:
#   python descargar_bit.py <coin> <timeframe> [desde]
#     coin:       eth, btc, icp, sol...  (o símbolo completo ETH/USDT:USDT)
#     timeframe:  1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d...
#     desde:      opcional. 'YYYY-MM-DD'  o  número de días hacia atrás.
#                 Si se omite, baja TODO el histórico disponible (bastante
#                 menos profundo que Binance).
#   python descargar_bit.py --feed [coin[,coin2,...]] [--tfs 1m,5m,15m,30m,1h,4h,1d]
#                            [--dias-objetivo 90] [--cada 60]
#
# Ejemplos:
#   python descargar_bit.py btc 5m 1
#   python descargar_bit.py eth 15m 2023-01-01
#   python descargar_bit.py --feed btc,eth --cada 60
# ---------------------------------------------------------------

import csv
import math
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import ccxt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado.senales import VENTANA_MAXIMA

DIR_LIBRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libro")

DIAS_OBJETIVO = 90.0  # confirmado por backtest (2026-08-11, ver mirar.md) -
                       # menos dias rompe el edge de REFINADAS_CONFIRMADAS.

TIMEFRAMES_FEED = ["1m","3m","5m", "15m", "30m", "1h", "4h", "1d"]


def _simbolo(coin):
    """'eth' -> 'ETH/USDT:USDT' (futuros USDT-M); deja intactos los que ya traen '/'."""
    coin = coin.strip()
    if '/' in coin:
        return coin.upper()
    return f"{coin.upper()}/USDT:USDT"


def _archivo(coin, timeframe):
    os.makedirs(DIR_LIBRO, exist_ok=True)
    nombre = f"historico_{_simbolo(coin).split('/')[0]}_{timeframe}_bitget.csv"
    return os.path.join(DIR_LIBRO, nombre)


def _desde_ms(desde, cliente):
    """Interpreta el arg 'desde': fecha ISO, nº de días, o None (todo)."""
    if desde is None:
        return cliente.parse8601('2019-01-01T00:00:00Z')  # Bitget futuros, margen de sobra
    if '-' in str(desde):
        return cliente.parse8601(f"{desde}T00:00:00Z")
    # número de días hacia atrás
    dias = float(desde)
    return cliente.milliseconds() - int(dias * 86_400_000)


def _hasta_ms_cerrado(cliente, timeframe):
    """Excluye la vela EN CURSO - el corte va justo al inicio de la vela que
    todavia se esta formando."""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    ahora = cliente.milliseconds()
    return ahora - (ahora % tf_ms)


def _ultimo_timestamp_ms(ruta):
    """Timestamp (ms) de la ULTIMA fila, leyendo solo los ultimos 64KB (igual
    que descargar_bin.py - no tiene sentido cargar el fichero entero solo
    para saber donde se quedo)."""
    with open(ruta, 'rb') as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 65536))
        cola = f.read()
    lineas = [l for l in cola.split(b'\n') if l.strip()]
    return int(lineas[-1].split(b',')[0])


def _segundos_tf(timeframe):
    mult = {"m": 60, "h": 3600, "d": 86400}
    return int(timeframe[:-1]) * mult[timeframe[-1]]


def _velas_objetivo(timeframe, dias=DIAS_OBJETIVO):
    """Nº de velas para cubrir 'dias' de historico en este TF - escala solo
    (mas velas en TF finos, menos en TF gruesos) para llegar siempre a la
    MISMA profundidad en tiempo, no a un nº de velas fijo (ver cabecera)."""
    por_dias = math.ceil(dias * 86400 / _segundos_tf(timeframe))
    return max(por_dias, VENTANA_MAXIMA)


def _recortar_si_hace_falta(ruta, cap, margen=0.10):
    """Recorta 'ruta' a las ultimas 'cap' velas, pero SOLO si se pasa del cap
    por un margen de holgura (10% por defecto) - con cap=500 esto reescribe
    el fichero cada ~50 velas nuevas, no en cada vuelta del feed. Reescritura
    en streaming (sin cargar todo en memoria) + os.replace() atomico, para
    que ningun lector en vivo vea nunca el fichero a medio escribir."""
    with open(ruta, 'r', newline='') as f:
        total = sum(1 for _ in f) - 1  # menos cabecera
    if total <= cap * (1 + margen):
        return
    tmp = ruta + ".tmp"
    with open(ruta, 'r', newline='') as fin, open(tmp, 'w', newline='') as fout:
        fout.write(fin.readline())  # cabecera
        a_saltar = total - cap
        for i, linea in enumerate(fin):
            if i >= a_saltar:
                fout.write(linea)
    os.replace(tmp, ruta)


@contextmanager
def _con_lock(ruta, timeout=1800.0, espera=2.0):
    """Lock de fichero por creacion atomica (O_CREAT|O_EXCL, '<ruta>.lock')
    para que dos escritores (el feed y una ejecucion manual puntual) no
    toquen el mismo historico a la vez - el peor momento para pisarse es
    durante un recorte (_recortar_si_hace_falta reescribe el fichero
    entero). Los lectores (niveles_soporte.py, monitor_niveles.py) NO
    necesitan este lock: ya tratan una fila a medio escribir al final del
    fichero como no consumida todavia (mismo patron que _tail_csv usa para
    flujo_*.csv), y _recortar_si_hace_falta usa os.replace() atomico, asi
    que un lector con el fichero ya abierto sigue viendo el contenido viejo
    completo hasta que lo reabra."""
    ruta_lock = ruta + ".lock"
    inicio = time.monotonic()
    while True:
        try:
            fd = os.open(ruta_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() - inicio > timeout:
                raise TimeoutError(f"lock de {ruta} no liberado tras {timeout:.0f}s")
            time.sleep(espera)
    try:
        yield
    finally:
        os.remove(ruta_lock)


def _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req=200):
    """Pagina fetch_ohlcv desde 'since' hasta 'hasta_ms' (exclusive).
    Devuelve velas ordenadas y sin duplicados."""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    velas = []
    vistos = set()
    while since < hasta_ms:
        try:
            lote = cliente.fetch_ohlcv(simbolo, timeframe, since=since, limit=limite_req)
        except ccxt.BaseError as e:
            print(f"  [reintento] {e}")
            time.sleep(2)
            continue
        if not lote:
            break
        nuevos = 0
        for v in lote:
            if v[0] < hasta_ms and v[0] not in vistos:
                vistos.add(v[0])
                velas.append(v)
                nuevos += 1
        since = lote[-1][0] + tf_ms
        if len(velas) % 20000 < limite_req:
            print(f"  {len(velas)} velas... "
                  f"({datetime.fromtimestamp(lote[-1][0]/1000, timezone.utc):%Y-%m-%d})")
        if nuevos == 0:
            break
    velas.sort(key=lambda v: v[0])
    return velas


def _escribir_filas(f, velas):
    w = csv.writer(f)
    for t, o, h, l, c, vol in velas:
        fecha = datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        w.writerow([t, fecha, o, h, l, c, vol])


def descargar(coin, timeframe, desde=None, limite_req=200):
    """Descarga completa (o desde 'desde'), SIEMPRE reescribe el fichero
    entero. Para refrescar sin perder lo ya bajado usar actualizar()."""
    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    since = _desde_ms(desde, cliente)
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    print(f"Descargando {simbolo} {timeframe} desde "
          f"{datetime.fromtimestamp(since/1000, timezone.utc):%Y-%m-%d} ...")
    velas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
    if not velas:
        print("No se descargó nada (¿símbolo o timeframe inválido?).")
        return None

    nombre = _archivo(coin, timeframe)
    with open(nombre, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])
        _escribir_filas(f, velas)

    print(f"\n[OK] {len(velas)} velas guardadas en {nombre}")
    print(f"     {datetime.fromtimestamp(velas[0][0]/1000, timezone.utc):%Y-%m-%d} "
          f"-> {datetime.fromtimestamp(velas[-1][0]/1000, timezone.utc):%Y-%m-%d}")
    return nombre


def actualizar(coin, timeframe, dias_objetivo=DIAS_OBJETIVO, limite_req=200):
    """Si NO hay fichero previo para esta coin/tf: descarga exactamente las
    velas necesarias para cubrir 'dias_objetivo' (calculado directamente
    por tiempo, sin bajar de mas para luego recortar). Si YA existe: lee la
    ultima vela guardada y solo pide/AÑADE lo que falta, sin reescribir el
    fichero entero. En ambos casos, al final recorta el fichero si se pasa
    del cap por margen de holgura (ver _recortar_si_hace_falta)."""
    velas_objetivo = _velas_objetivo(timeframe, dias_objetivo)
    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    ruta = _archivo(coin, timeframe)
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    if not os.path.exists(ruta):
        since = hasta_ms - velas_objetivo * _segundos_tf(timeframe) * 1000
        print(f"Descargando {simbolo} {timeframe}: {velas_objetivo:.0f} velas ...")
        velas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
        if not velas:
            print("No se descargó nada (¿símbolo o timeframe inválido?).")
            return ruta
        with open(ruta, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])
            _escribir_filas(f, velas)
        print(f"  [OK] {simbolo} {timeframe}: {len(velas)} velas guardadas en {ruta}")
    else:
        tf_ms = cliente.parse_timeframe(timeframe) * 1000
        ultimo_ts = _ultimo_timestamp_ms(ruta)
        since = ultimo_ts + tf_ms
        if since < hasta_ms:
            nuevas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
            if nuevas:
                with open(ruta, 'a', newline='') as f:
                    _escribir_filas(f, nuevas)
                print(f"  [OK] {simbolo} {timeframe}: +{len(nuevas)} velas "
                      f"(hasta {datetime.fromtimestamp(nuevas[-1][0]/1000, timezone.utc):%Y-%m-%d %H:%M} UTC)")

    _recortar_si_hace_falta(ruta, velas_objetivo)
    return ruta


def _feed(coins, tfs, dias_objetivo, cada):
    """Modo daemon: mantiene al dia (y acotado a 'dias_objetivo' dias) el
    historico de cada coin/tf, sin parar, con lock para no pisarse con una
    ejecucion manual concurrente (ver _con_lock). Un fallo puntual de una
    coin/tf (red, símbolo) no debe tumbar el proceso - se avisa y se sigue
    con el resto, misma filosofia de resiliencia que grabador_libro.py."""
    print(f"Feed de velas Bitget: {', '.join(coins)} / {', '.join(tfs)} "
          f"(dias_objetivo={dias_objetivo:.0f}, cada={cada:.0f}s). Ctrl+C para parar.")
    try:
        while True:
            for coin in coins:
                for tf in tfs:
                    try:
                        with _con_lock(_archivo(coin, tf)):
                            actualizar(coin, tf, dias_objetivo=dias_objetivo)
                    except Exception as e:
                        print(f"  (aviso) {coin} {tf}: {e}")
            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--feed":
        args = args[1:]
        if args and not args[0].startswith("--"):
            coins = [c.strip().upper() for c in args[0].split(",")]
            args = args[1:]
        else:
            coins = ["BTC", "ETH"]
        tfs = TIMEFRAMES_FEED
        dias_objetivo = DIAS_OBJETIVO
        cada = 60.0
        i = 0
        while i < len(args):
            if args[i] == "--tfs":
                i += 1; tfs = [t.strip() for t in args[i].split(",")]
            elif args[i] == "--dias-objetivo":
                i += 1; dias_objetivo = float(args[i])
            elif args[i] == "--cada":
                i += 1; cada = float(args[i])
            i += 1
        _feed(coins, tfs, dias_objetivo, cada)
        return

    if len(sys.argv) < 3:
        print("Uso: python descargar_bit.py <coin> <timeframe> [desde]")
        print("  coin:      eth, btc, icp, sol...  (o ETH/USDT:USDT)")
        print("  timeframe: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d...")
        print("  desde:     'YYYY-MM-DD' o nº de días atrás (opcional; si no, todo)")
        print("\nEjemplos:")
        print("  python descargar_bit.py btc 5m 1")
        print("  python descargar_bit.py eth 15m 2023-01-01")
        print("\nModo daemon (siempre corriendo, ver cabecera del fichero):")
        print("  python descargar_bit.py --feed [coin[,coin2,...]] [--tfs 1m,5m,15m,30m,1h,4h,1d]")
        print("                           [--dias-objetivo 90] [--cada 60]")
        return
    coin = sys.argv[1]
    timeframe = sys.argv[2]
    desde = sys.argv[3] if len(sys.argv) > 3 else None
    descargar(coin, timeframe, desde)


if __name__ == "__main__":
    main()
