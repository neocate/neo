# ================================================================================
# descargar_hist_bit.py - RECOLECCION DEL HISTORIAL (implementacion comun)
#
# Baja el historial COMPLETO desde el origen real del contrato. Se corre dos
# o tres veces en la vida, no en produccion: para mantener el CSV al dia
# esta descargar_bit_[mercado].py.
#
# NO SE EJECUTA DIRECTAMENTE: no sabe de que mercado hablas. Se ejecuta por
# uno de sus dos frontales:
#
#   python descargar_hist_bit_spot.py eth 15m,1h,4h,1d
#   python descargar_hist_bit_futuros.py eth 15m,1h,4h,1d
#
# La maquinaria es la misma (velas_bit.py): mismo CSV, mismo lock, mismo
# log, mismas reglas de continuidad. Lo unico que cambia es el punto de
# partida - aqui el origen del contrato, alli la ultima vela guardada.
#
# COSTE (ETH spot, origen 2018-07-24, ~2.950 dias, endpoint historico 200/peticion)
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
# El perpetuo arranca mas tarde que el spot, asi que sale mas barato. El
# origen real de cada uno lo encuentra solo (biseccion, ver origen_exchange).
#
# Por eso los TF se pasan por argumento: si no necesitas el 1m completo, te
# ahorras mas de la mitad del trabajo.
#
# REPARTO ENTRE EQUIPOS
#   Cada equipo baja monedas/TF distintos y los CSV terminados se pasan por
#   FTP. Cada CSV lo escribe un solo equipo de principio a fin, asi que es
#   continuo por construccion: no hay trozos que empalmar.
#   Al recibir un CSV por FTP hay que dejar en velas/[COIN]/ el CSV *Y SU
#   .meta*, que es lo que dice de que mercado son esas velas. Sin .meta al
#   lado no se toca el fichero: el nombre se puede equivocar al copiarlo y
#   mirando los numeros no hay forma de distinguir spot de perp -se separan
#   5 pb-. Con los dos ficheros en su sitio, arrancar descargar_bit_[mercado]
#   encima: lee la ultima vela, valida el empalme y sigue.
#   Si la transferencia se corto a medias, la ultima linea rota se detecta y
#   se recorta sola (ver _sanear_cola en velas_bit.py).
#
# REANUDABLE
#   Se escribe por lotes ya verificados. Si cortas (Ctrl+C) o se va la luz,
#   el CSV queda mas corto pero continuo: relanzar sigue donde iba, no
#   repite las 21.000 peticiones.
# ================================================================================

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ccxt

from velas_bit import (
    DIR_LOCK, DIR_LOG, DIR_VELAS, MERCADOS, TIMEFRAMES,
    Lock, _archivo, _crear_csv, _estado_vela, _extraer_moneda, _fecha, _log,
    _primer_ts_guardado, _simbolo, _ts_ultima_cerrada,
    bajar_por_lotes, origen_exchange, parsear_args, resolver_tfs, resumen,
)


def _duracion(segundos):
    m, s = divmod(int(segundos), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def recolectar(coin, timeframe, mercado, rehacer=False):
    """Baja el historial completo de un TF. Devuelve velas añadidas.

    El CSV se rellena SIEMPRE hacia adelante, nunca hacia atras: si ya
    existe, se continua desde su ultima vela. Asi que un CSV que arranco
    descargar_bit_[mercado].py (ultimos 10 dias) no se completa hacia atras
    solo - hay que rehacerlo. Como eso es destructivo y caro, no se hace por
    sorpresa: se avisa y se deja --rehacer para pedirlo.
    """
    inicio = time.time()
    ruta = _archivo(coin, timeframe, mercado)
    try:
        if rehacer and ruta.exists():
            _log(coin, timeframe, mercado,
                 "[REHACER] se borra el CSV y se baja desde el origen")
            _crear_csv(ruta, coin, timeframe, mercado)

        primero = _primer_ts_guardado(ruta)
        if primero is not None:
            origen = origen_exchange(_simbolo(coin, mercado), timeframe)
            if origen is not None and primero > origen:
                _log(coin, timeframe, mercado,
                     f"[PARCIAL] el CSV arranca en {_fecha(primero)}, pero el "
                     f"contrato tiene datos desde {_fecha(origen)}. Solo se "
                     f"completa hacia adelante: para bajar lo anterior hay que "
                     f"rehacerlo con --rehacer")

        añadidas = bajar_por_lotes(coin, timeframe, mercado,
                                   _ts_ultima_cerrada(timeframe))
        estado = _estado_vela(ruta)
        marca = estado['fecha'] if estado else 'sin datos'
        _log(coin, timeframe, mercado,
             f"[FIN] +{añadidas:,} vela(s) en {_duracion(time.time() - inicio)} "
             f"-> {marca}")
        return añadidas
    except ccxt.BaseError as e:
        _log(coin, timeframe, mercado, f"[ERROR API] {e}")
    except OSError as e:
        _log(coin, timeframe, mercado, f"[ERROR IO] {e}")
    return 0


def _ayuda(mercado, script):
    print("Uso:")
    print(f"  python {script} <coin> [TF,...] [--rehacer]")
    print("\nEjemplos:")
    print(f"  python {script} eth               # todos los TF")
    print(f"  python {script} eth 15m,1h,4h,1d  # sin 1m/3m/5m")
    print(f"  python {script} btc 1m            # solo el 1m de BTC")
    print(f"  python {script} eth 1h --rehacer  # borra el CSV y baja todo")
    print("\n--rehacer: BORRA el CSV y lo baja entero desde el origen. Hace")
    print("falta cuando el CSV lo empezo descargar_bit_[mercado].py (ultimos")
    print("dias), ya que el relleno es siempre hacia adelante, nunca hacia atras.")
    print(f"\nMercado: {mercado} ({MERCADOS[mercado].format('ETH')})")
    print(f"TF: {','.join(TIMEFRAMES)}")
    print("Ventana inicial (si no hay CSV): ORIGEN_DIAS en velas_bit.py")
    print(f"Para mantener al dia (no para el historial): descargar_bit_{mercado}.py")
    print(f"\nCSV:  {DIR_VELAS}/[COIN]/bitget_[COIN]_[TF]_{mercado}.csv")
    print(f"Lock: {DIR_LOCK}/[COIN]_[TF]_{mercado}.lock")
    print(f"Log:  {DIR_LOG}/[COIN]_[TF]_{mercado}.log")


def main(mercado, argv=None, script='descargar_hist_bit.py'):
    """Punto de entrada. 'mercado' es obligatorio y lo fija el frontal."""
    if mercado not in MERCADOS:
        raise ValueError(f"mercado invalido: {mercado!r}. Usa: {', '.join(MERCADOS)}")

    argv = sys.argv[1:] if argv is None else argv
    try:
        coin, tfs_str, opciones = parsear_args(argv, banderas=('--rehacer',))
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    if opciones['--help'] or not coin:
        _ayuda(mercado, script)
        return

    try:
        tfs = resolver_tfs(tfs_str)
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    rehacer = opciones['--rehacer']
    moneda = _extraer_moneda(coin)
    print(f"\n{'='*60}")
    print(f"[HISTORIAL COMPLETO] {moneda} {mercado} - TF: {', '.join(tfs)}")
    print(f"  Desde el origen del contrato. Reanudable: si cortas, relanza.")
    if rehacer:
        print(f"  --rehacer: se BORRA el CSV de cada TF y se baja entero")
    print(f"{'='*60}", flush=True)

    arranque = time.time()
    total = 0
    locks = []
    try:
        for tf in tfs:
            lock = Lock(coin, tf, mercado)
            lock.__enter__()
            locks.append(lock)

        for tf in tfs:
            print(f"\n[{datetime.now(timezone.utc):%H:%M:%S}] "
                  f"{moneda} {tf} {mercado}", flush=True)
            total += recolectar(coin, tf, mercado, rehacer=rehacer)

        resumen(coin, tfs, mercado)
        print(f"\n[FIN] {moneda} {mercado}: +{total:,} vela(s) en "
              f"{_duracion(time.time() - arranque)}")
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n\n[SALIDA] {moneda} {mercado} - detenido por el usuario. "
              f"El CSV queda continuo: relanza para seguir donde iba.")
    finally:
        for lock in reversed(locks):
            lock.__exit__(None, None, None)


if __name__ == "__main__":
    print(__doc__ or "")
    print("[ERROR] descargar_hist_bit.py no se ejecuta directamente: no sabe de")
    print("        que mercado hablas. Usa uno de sus dos frontales:")
    print("          python descargar_hist_bit_spot.py <coin> [TF,...]")
    print("          python descargar_hist_bit_futuros.py <coin> [TF,...]")
    sys.exit(1)
