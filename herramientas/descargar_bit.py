# Uso:
#   python descargar_bit.py <coin> <timeframe> [desde]
#   python descargar_bit.py --feed [coin[,coin2,...]] [--tfs 1m,5m,15m,30m,1h,4h,1d] [--dias-objetivo 90] [--cada 60]
#   python descargar_bit.py --velas <coin[,coin2,...]> [tf] [--cada segundos]
#
# Ejemplos:
#   python descargar_bit.py btc 5m 1
#   python descargar_bit.py eth 15m 2023-01-01
#   python descargar_bit.py --feed btc,eth --cada 60
#   python descargar_bit.py --velas btc
#   python descargar_bit.py --velas btc 1h
#   python descargar_bit.py --velas btc,eth --cada 60

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
from mercado import datos

DIR_LIBRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libro")
DIR_VELAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "velas")

DIAS_OBJETIVO = 90.0

TIMEFRAMES_FEED = ["1m","3m","5m", "15m", "30m", "1h", "4h", "1d"]

TIMEFRAMES_NIVELES = ["1m", "3m", "5m", "15m", "1h", "4h", "1d"]


def _simbolo(coin):
    """_simbolo(coin: str) -> str, ej: 'eth' -> 'ETH/USDT:USDT'"""
    return datos.normalizar_simbolo(coin.strip(), "f")[0]


def _archivo(coin, timeframe):
    os.makedirs(DIR_LIBRO, exist_ok=True)
    nombre = f"historico_{_simbolo(coin).split('/')[0]}_{timeframe}_bitget.csv"
    return os.path.join(DIR_LIBRO, nombre)


def _archivo_velas(coin, timeframe):
    """_archivo_velas(coin, timeframe) -> ruta en herramientas/velas/<COIN>/<TF>_bitget.csv"""
    carpeta = os.path.join(DIR_VELAS, _simbolo(coin).split('/')[0])
    os.makedirs(carpeta, exist_ok=True)
    return os.path.join(carpeta, f"{timeframe}_bitget.csv")


MAX_PASOS_BISECCION = 50


def _primera_vela_ms(cliente, simbolo, timeframe):
    """_primera_vela_ms(cliente, simbolo, timeframe) -> int(ms), 'since' mas antiguo con datos"""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    lo = cliente.parse8601('2018-01-01T00:00:00Z')
    hi = cliente.milliseconds()
    print(f"  Buscando inicio real de historico de {simbolo} {timeframe} "
          f"(sin cache previa, primera vez)...")
    lote = cliente.fetch_ohlcv(simbolo, timeframe, since=lo, limit=1)
    if lote:
        return lote[0][0] - 1
    for paso in range(1, MAX_PASOS_BISECCION + 1):
        if hi - lo <= tf_ms:
            break
        mid = (lo + hi) // 2
        lote = cliente.fetch_ohlcv(simbolo, timeframe, since=mid, limit=1)
        if lote:
            hi = lote[0][0]
        else:
            lo = mid + tf_ms
        if paso % 5 == 0:
            print(f"    ...acotando ({datetime.fromtimestamp(lo/1000, timezone.utc):%Y-%m-%d} - "
                  f"{datetime.fromtimestamp(hi/1000, timezone.utc):%Y-%m-%d})")
    else:
        print(f"  AVISO: no convergio del todo tras {MAX_PASOS_BISECCION} pasos "
              f"(Bitget respondio de forma inconsistente cerca del borde) - "
              f"se usa el punto mas ajustado confirmado con datos.")
    print(f"  Inicio real: {datetime.fromtimestamp(hi/1000, timezone.utc):%Y-%m-%d}")
    return hi - 1


def _desde_ms(desde, cliente, simbolo=None, timeframe=None):
    """_desde_ms(desde: str|int|None, cliente, simbolo=None, timeframe=None) -> int(ms)"""
    if desde is None:
        if simbolo and timeframe:
            return _primera_vela_ms(cliente, simbolo, timeframe)
        return cliente.parse8601('2019-01-01T00:00:00Z')
    if '-' in str(desde):
        return cliente.parse8601(f"{desde}T00:00:00Z")
    dias = float(desde)
    return cliente.milliseconds() - int(dias * 86_400_000)


def _hasta_ms_cerrado(cliente, timeframe):
    """_hasta_ms_cerrado(cliente, timeframe) -> int(ms), excluye la vela en curso"""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    ahora = cliente.milliseconds()
    return ahora - (ahora % tf_ms)


def _ultimo_timestamp_ms(ruta):
    """_ultimo_timestamp_ms(ruta: str) -> int(ms) de la ultima fila"""
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
    """_velas_objetivo(timeframe, dias=DIAS_OBJETIVO) -> int"""
    por_dias = math.ceil(dias * 86400 / _segundos_tf(timeframe))
    return max(por_dias, VENTANA_MAXIMA)


def _recortar_si_hace_falta(ruta, cap, margen=0.10):
    """_recortar_si_hace_falta(ruta, cap: int, margen: float = 0.10) -> None"""
    with open(ruta, 'r', newline='') as f:
        total = sum(1 for _ in f) - 1
    if total <= cap * (1 + margen):
        return
    tmp = ruta + ".tmp"
    with open(ruta, 'r', newline='') as fin, open(tmp, 'w', newline='') as fout:
        fout.write(fin.readline())
        a_saltar = total - cap
        for i, linea in enumerate(fin):
            if i >= a_saltar:
                fout.write(linea)
    os.replace(tmp, ruta)


@contextmanager
def _con_lock(ruta, timeout=1800.0, espera=2.0):
    """_con_lock(ruta, timeout=1800.0, espera=2.0) -> context manager, lock de fichero por '<ruta>.lock'"""
    ruta_lock = ruta + ".lock"
    inicio = time.monotonic()
    avisado = False
    while True:
        try:
            fd = os.open(ruta_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if not avisado:
                print(f"  Esperando lock de {ruta_lock} (¿otra descarga en curso, o restos de "
                      f"una anterior sin terminar limpio? maximo {timeout:.0f}s antes de fallar)...")
                avisado = True
            if time.monotonic() - inicio > timeout:
                raise TimeoutError(f"lock de {ruta} no liberado tras {timeout:.0f}s")
            time.sleep(espera)
    try:
        yield
    finally:
        os.remove(ruta_lock)


def _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req=200):
    """_descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req=200) -> list[vela]"""
    tf_ms = cliente.parse_timeframe(timeframe) * 1000
    velas = []
    vistos = set()
    pagina = 0
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
        since = lote[-1][0]
        pagina += 1
        if pagina % 10 == 0:
            print(f"  {len(velas)} velas... "
                  f"({datetime.fromtimestamp(lote[-1][0]/1000, timezone.utc):%Y-%m-%d})")
        if nuevos == 0:
            break
    if velas:
        print(f"  {len(velas)} velas descargadas "
              f"({datetime.fromtimestamp(velas[-1][0]/1000, timezone.utc):%Y-%m-%d}).")
    velas.sort(key=lambda v: v[0])
    return velas


def _escribir_filas(f, velas):
    w = csv.writer(f)
    for t, o, h, l, c, vol in velas:
        fecha = datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        w.writerow([t, fecha, o, h, l, c, vol])


def _reemplazar_ultima_fila(ruta, vela):
    """_reemplazar_ultima_fila(ruta, vela) -> None"""
    with open(ruta, 'rb') as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 65536))
        inicio_bloque = f.tell()
        cola = f.read()
    contenido = cola.rstrip(b'\r\n')
    salto_previo = contenido.rfind(b'\n')
    offset = inicio_bloque + (salto_previo + 1 if salto_previo != -1 else 0)
    with open(ruta, 'r+', newline='') as f:
        f.seek(offset)
        f.truncate()
        _escribir_filas(f, [vela])


def _anexar_nuevas(ruta, nuevas, ultimo_ts):
    """_anexar_nuevas(ruta, nuevas: list[vela], ultimo_ts: int) -> (velas_nuevas: list, corregida: bool)"""
    corregida = False
    if nuevas and nuevas[0][0] <= ultimo_ts:
        _reemplazar_ultima_fila(ruta, nuevas[0])
        nuevas = nuevas[1:]
        corregida = True
    if nuevas:
        with open(ruta, 'a', newline='') as f:
            _escribir_filas(f, nuevas)
    return nuevas, corregida


def descargar(coin, timeframe, desde=None, limite_req=200):
    """descargar(coin: str, timeframe: str, desde: str|int|None = None, limite_req=200) -> str|None, ruta del CSV"""
    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    since = _desde_ms(desde, cliente, simbolo, timeframe)
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
    """actualizar(coin, timeframe, dias_objetivo=DIAS_OBJETIVO, limite_req=200) -> str, ruta del CSV"""
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
        since = ultimo_ts
        if ultimo_ts + tf_ms < hasta_ms:
            nuevas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
            if nuevas:
                restantes, corregida = _anexar_nuevas(ruta, nuevas, ultimo_ts)
                sufijo = " (+ ultima vela corregida, seguia en curso)" if corregida else ""
                if restantes:
                    print(f"  [OK] {simbolo} {timeframe}: +{len(restantes)} velas{sufijo} "
                          f"(hasta {datetime.fromtimestamp(restantes[-1][0]/1000, timezone.utc):%Y-%m-%d %H:%M} UTC)")
                elif corregida:
                    print(f"  [OK] {simbolo} {timeframe}: ultima vela corregida (seguia en curso).")

    _recortar_si_hace_falta(ruta, velas_objetivo)
    return ruta


def actualizar_velas(coin, timeframe, limite_req=200):
    """actualizar_velas(coin, timeframe, limite_req=200) -> str, ruta del CSV"""
    cliente = ccxt.bitget({'enableRateLimit': True})
    simbolo = _simbolo(coin)
    ruta = _archivo_velas(coin, timeframe)
    hasta_ms = _hasta_ms_cerrado(cliente, timeframe)

    with _con_lock(ruta):
        if not os.path.exists(ruta):
            since = _desde_ms(None, cliente, simbolo, timeframe)
            print(f"Descargando {simbolo} {timeframe}: historico completo ...")
            velas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
            if not velas:
                print("  No se descargó nada (¿símbolo o timeframe inválido?).")
                return ruta
            with open(ruta, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['timestamp', 'fecha_utc', 'open', 'high', 'low', 'close', 'volumen'])
                _escribir_filas(f, velas)
            print(f"  [OK] {simbolo} {timeframe}: {len(velas)} velas guardadas en {ruta}")
            print(f"       {datetime.fromtimestamp(velas[0][0]/1000, timezone.utc):%Y-%m-%d} "
                  f"-> {datetime.fromtimestamp(velas[-1][0]/1000, timezone.utc):%Y-%m-%d}")
        else:
            tf_ms = cliente.parse_timeframe(timeframe) * 1000
            ultimo_ts = _ultimo_timestamp_ms(ruta)
            since = ultimo_ts
            if ultimo_ts + tf_ms >= hasta_ms:
                print(f"  {simbolo} {timeframe} ya esta al dia.")
                return ruta
            nuevas = _descargar_rango(cliente, simbolo, timeframe, since, hasta_ms, limite_req)
            if not nuevas:
                print(f"  {simbolo} {timeframe}: no hay velas nuevas todavia.")
                return ruta
            restantes, corregida = _anexar_nuevas(ruta, nuevas, ultimo_ts)
            sufijo = " (+ ultima vela corregida, seguia en curso)" if corregida else ""
            if restantes:
                print(f"  [OK] {simbolo} {timeframe}: +{len(restantes)} velas{sufijo} "
                      f"(hasta {datetime.fromtimestamp(restantes[-1][0]/1000, timezone.utc):%Y-%m-%d %H:%M} UTC)")
            elif corregida:
                print(f"  [OK] {simbolo} {timeframe}: ultima vela corregida (seguia en curso).")
    return ruta


def _feed(coins, tfs, dias_objetivo, cada):
    """_feed(coins: list[str], tfs: list[str], dias_objetivo: float, cada: float) -> None, daemon"""
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


def _feed_velas(coins, tfs, cada):
    """_feed_velas(coins: list[str], tfs: list[str], cada: float) -> None, daemon"""
    print(f"Feed de velas permanentes: {', '.join(coins)} / {', '.join(tfs)} "
          f"(cada={cada:.0f}s). Ctrl+C para parar.")
    try:
        while True:
            for coin in coins:
                for tf in tfs:
                    try:
                        actualizar_velas(coin, tf)
                    except Exception as e:
                        print(f"  (aviso) {coin} {tf}: {e}")
            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--velas":
        args = args[1:]
        if not args:
            print("Uso: python descargar_bit.py --velas <coin[,coin2,...]> [tf] [--cada segundos]")
            return
        coins = [c.strip().upper() for c in args[0].split(",")]
        resto = args[1:]
        tf_unico = None
        if resto and not resto[0].startswith("--"):
            tf_unico = resto[0]
            resto = resto[1:]
        cada = None
        i = 0
        while i < len(resto):
            if resto[i] == "--cada":
                i += 1; cada = float(resto[i])
            i += 1
        tfs = [tf_unico] if tf_unico else TIMEFRAMES_NIVELES

        if cada is None:
            for coin in coins:
                for tf in tfs:
                    actualizar_velas(coin, tf)
        else:
            _feed_velas(coins, tfs, cada)
        return

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
        print("\nHistorico permanente para niveles.py (ver cabecera):")
        print("  python descargar_bit.py --velas <coin[,coin2,...]> [tf] [--cada segundos]")
        return
    coin = sys.argv[1]
    timeframe = sys.argv[2]
    desde = sys.argv[3] if len(sys.argv) > 3 else None
    descargar(coin, timeframe, desde)


if __name__ == "__main__":
    main()
