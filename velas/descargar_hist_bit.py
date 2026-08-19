# ================================================================================
# descargar_hist_bit.py - RECOLECCION DEL HISTORIAL
#
# Baja el historial COMPLETO desde el origen real del contrato. Se corre dos
# o tres veces en la vida, no en produccion: para mantener el CSV al dia
# esta descargar_bit.py.
#
# La maquinaria es la misma (velas_bit.py): mismo CSV, mismo lock, mismo
# log, mismas reglas de continuidad. Lo unico que cambia es el punto de
# partida - aqui el origen del contrato, alli la ultima vela guardada.
#
# COSTE (ETH, origen 2018-07-24, ~2.950 dias, endpoint historico 200/peticion)
#     1d      2.948 velas       15 peticiones
#     4h     17.688             89
#     1h     70.752            354
#     30m   141.504            708
#     15m   283.008          1.416
#     5m    849.024          4.246
#     3m  1.415.040          7.076
#     1m  4.245.120         21.226      <- el 60% del total, ~320 MB
#         ~7,0M velas      ~35.100 peticiones, ~525 MB, del orden de 1-2 h
#
# Por eso los TF se pasan por argumento: si no necesitas el 1m completo, te
# ahorras mas de la mitad del trabajo.
#
# REPARTO ENTRE EQUIPOS
#   Cada equipo baja monedas/TF distintos y los CSV terminados se pasan por
#   FTP. Cada CSV lo escribe un solo equipo de principio a fin, asi que es
#   continuo por construccion: no hay trozos que empalmar.
#   Al recibir un CSV por FTP basta con dejarlo en velas/[COIN]/ y arrancar
#   descargar_bit.py encima - lee la ultima vela, valida el empalme y sigue.
#   Si la transferencia se corto a medias, la ultima linea rota se detecta y
#   se recorta sola (ver _sanear_cola en velas_bit.py).
#
# REANUDABLE
#   Se escribe por lotes ya verificados. Si cortas (Ctrl+C) o se va la luz,
#   el CSV queda mas corto pero continuo: relanzar sigue donde iba, no
#   repite las 21.000 peticiones.
#
# USO
#   python descargar_hist_bit.py <coin> [TF,...]
#
#   python descargar_hist_bit.py eth              # todos los TF (lo caro)
#   python descargar_hist_bit.py eth 15m,1h,4h,1d # sin 1m/3m/5m: mucho mas rapido
#   python descargar_hist_bit.py btc 1m           # un equipo solo con el 1m de BTC
# ================================================================================

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ccxt

from velas_bit import (
    DIR_LOCK, DIR_LOG, DIR_VELAS, TIMEFRAMES,
    Lock, _archivo, _crear_csv, _estado_vela, _extraer_moneda, _fecha, _log,
    _primer_ts_guardado, _simbolo, _ts_ultima_cerrada,
    bajar_por_lotes, origen_exchange, parsear_args, resolver_tfs, resumen,
)


def _duracion(segundos):
    m, s = divmod(int(segundos), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def recolectar(coin, timeframe, rehacer=False):
    """Baja el historial completo de un TF. Devuelve velas añadidas.

    El CSV se rellena SIEMPRE hacia adelante, nunca hacia atras: si ya
    existe, se continua desde su ultima vela. Asi que un CSV que arranco
    descargar_bit.py (ultimos 10 dias) no se completa hacia atras solo -
    hay que rehacerlo. Como eso es destructivo y caro, no se hace por
    sorpresa: se avisa y se deja --rehacer para pedirlo.
    """
    inicio = time.time()
    ruta = _archivo(coin, timeframe)
    try:
        if rehacer and ruta.exists():
            _log(coin, timeframe, "[REHACER] se borra el CSV y se baja desde el origen")
            _crear_csv(ruta)

        primero = _primer_ts_guardado(ruta)
        if primero is not None:
            origen = origen_exchange(_simbolo(coin), timeframe)
            if origen is not None and primero > origen:
                _log(coin, timeframe,
                     f"[PARCIAL] el CSV arranca en {_fecha(primero)}, pero el "
                     f"contrato tiene datos desde {_fecha(origen)}. Solo se "
                     f"completa hacia adelante: para bajar lo anterior hay que "
                     f"rehacerlo con --rehacer")

        añadidas = bajar_por_lotes(coin, timeframe, _ts_ultima_cerrada(timeframe))
        estado = _estado_vela(ruta)
        marca = estado['fecha'] if estado else 'sin datos'
        _log(coin, timeframe,
             f"[FIN] +{añadidas:,} vela(s) en {_duracion(time.time() - inicio)} "
             f"-> {marca}")
        return añadidas
    except ccxt.BaseError as e:
        _log(coin, timeframe, f"[ERROR API] {e}")
    except OSError as e:
        _log(coin, timeframe, f"[ERROR IO] {e}")
    return 0


def main():
    coin, tfs_str, opciones = parsear_args(sys.argv[1:], banderas=('--rehacer',))
    if not coin:
        print(__doc__)
        print("Uso:")
        print("  python descargar_hist_bit.py <coin> [TF,...] [--rehacer]")
        print("\nEjemplos:")
        print("  python descargar_hist_bit.py eth               # todos los TF")
        print("  python descargar_hist_bit.py eth 15m,1h,4h,1d  # sin 1m/3m/5m")
        print("  python descargar_hist_bit.py btc 1m            # solo el 1m de BTC")
        print("  python descargar_hist_bit.py eth 1h --rehacer  # borra el CSV y baja todo")
        print("\n--rehacer: BORRA el CSV y lo baja entero desde el origen. Hace")
        print("falta cuando el CSV lo empezo descargar_bit.py (ultimos dias), ya")
        print("que el relleno es siempre hacia adelante, nunca hacia atras.")
        print(f"\nTF: {','.join(TIMEFRAMES)}")
        print("Para mantener al dia (no para el historial): descargar_bit.py")
        print(f"\nCSV:  {DIR_VELAS}/[COIN]/bitget_[COIN]_[TF].csv")
        print(f"Lock: {DIR_LOCK}/[COIN]_[TF].lock")
        print(f"Log:  {DIR_LOG}/[COIN]_[TF].log")
        return

    tfs = resolver_tfs(tfs_str)
    if not tfs:
        print(f"[ERROR] TF invalidos. Usa: {','.join(TIMEFRAMES)}")
        sys.exit(1)

    rehacer = opciones['--rehacer']
    moneda = _extraer_moneda(coin)
    print(f"\n{'='*60}")
    print(f"[HISTORIAL COMPLETO] {moneda} - TF: {', '.join(tfs)}")
    print(f"  Desde el origen del contrato. Reanudable: si cortas, relanza.")
    if rehacer:
        print(f"  --rehacer: se BORRA el CSV de cada TF y se baja entero")
    print(f"{'='*60}", flush=True)

    arranque = time.time()
    total = 0
    locks = []
    try:
        for tf in tfs:
            lock = Lock(coin, tf)
            lock.__enter__()
            locks.append(lock)

        for tf in tfs:
            print(f"\n[{datetime.now():%H:%M:%S}] {moneda} {tf}", flush=True)
            total += recolectar(coin, tf, rehacer=rehacer)

        resumen(coin, tfs)
        print(f"\n[FIN] {moneda}: +{total:,} vela(s) en "
              f"{_duracion(time.time() - arranque)}")
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n\n[SALIDA] {moneda} - detenido por el usuario. "
              f"El CSV queda continuo: relanza para seguir donde iba.")
    finally:
        for lock in reversed(locks):
            lock.__exit__(None, None, None)


if __name__ == "__main__":
    main()
