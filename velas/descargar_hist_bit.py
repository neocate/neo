
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
