# ================================================================================
# descargar_bit.py - PRODUCCION
#
# Mantiene al dia los CSV de velas cerradas. Es el que corre siempre.
# La maquinaria esta en velas_bit.py (rutas, lock, log, CSV, huecos).
#
# Para bajar el historial completo NO es este fichero: es
# descargar_hist_bit.py, que se corre dos o tres veces y ya.
#
#   Sin CSV      -> baja los ultimos DIAS_DEFECTO dias (suficiente para
#                   indicadores; el historial largo lo pone el otro fichero)
#   Con CSV      -> añade solo lo que falta hasta actual-1
#   Con --loop   -> daemon: cada TF se despierta en SU propio cierre +1s
#
# El endpoint lo elige velas_bit segun la antiguedad de cada peticion: aqui
# casi siempre sale el reciente (1000 velas/peticion). Si un equipo lleva
# mas dias parado que la ventana del endpoint reciente (1m 30 dias, 1h 60),
# ese tramo cae solo al historico sin que haya que hacer nada.
#
# USO
#   python descargar_bit.py <coin> [TF,...] [--loop]
#
#   python descargar_bit.py eth                  # todos los TF, sale al dia
#   python descargar_bit.py eth 5m,15m,1h        # solo esos TF
#   python descargar_bit.py eth --loop           # daemon, todos los TF
#   python descargar_bit.py btc 4h,1d --loop     # daemon, solo 4h y 1d
# ================================================================================

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ccxt

from velas_bit import (
    DIR_LOCK, DIR_LOG, DIR_VELAS, MS_DIA, TIMEFRAMES,
    Lock, _archivo, _cliente, _estado_vela, _extraer_moneda, _log,
    _tf_ms, _ts_actual, parsear_args, poner_al_dia, resolver_tfs, resumen,
)

# Dias que se bajan cuando no hay CSV. Solo es el arranque en frio de un
# equipo nuevo: para los indicadores sobra, y el historial largo lo trae
# descargar_hist_bit.py.
DIAS_DEFECTO = 10


def _origen_arranque(timeframe):
    """Punto de partida cuando no hay CSV: hace DIAS_DEFECTO dias."""
    ahora = _cliente().milliseconds()
    inicio = ahora - DIAS_DEFECTO * MS_DIA
    return inicio - (inicio % _tf_ms(timeframe))


def _atender(coin, timeframe):
    """Pone al dia un TF, informando de lo que pasa."""
    try:
        añadidas = poner_al_dia(coin, timeframe, origen_ts=_origen_arranque(timeframe))
        if añadidas == 0:
            estado = _estado_vela(_archivo(coin, timeframe))
            print(f"  Al dia ({estado['fecha'] if estado else 'sin datos'})", flush=True)
        return añadidas
    except ccxt.BaseError as e:
        _log(coin, timeframe, f"[ERROR API] {e}")
    except OSError as e:
        _log(coin, timeframe, f"[ERROR IO] {e}")
    return 0


def daemon(coin, tfs):
    """Cada TF con su propio reloj: se duerme hasta el cierre mas proximo
    +1s (margen para que el exchange publique la vela) y se atienden SOLO
    los TF que han cerrado."""
    print(f"\n{'='*60}")
    print(f"[DAEMON] {_extraer_moneda(coin)} - TF: {', '.join(tfs)}")
    print(f"  Cada TF se despierta en su propio cierre (+1s de margen)")
    print(f"{'='*60}", flush=True)

    while True:
        ahora_ms = _cliente().milliseconds()
        proximos = {tf: _ts_actual(tf, ahora_ms) + _tf_ms(tf) for tf in tfs}
        objetivo = min(proximos.values())
        vencen = [tf for tf in tfs if proximos[tf] == objetivo]
        espera = (objetivo - ahora_ms) / 1000 + 1

        print(f"\n[{datetime.now():%H:%M:%S}] Proximo cierre: "
              f"{', '.join(vencen)} en {espera:.0f}s", flush=True)
        time.sleep(max(0, espera))

        for tf in vencen:
            _atender(coin, tf)


def main():
    coin, tfs_str, opciones = parsear_args(sys.argv[1:], banderas=('--loop',))
    if not coin:
        print(__doc__)
        print("Uso:")
        print("  python descargar_bit.py <coin> [TF,...] [--loop]")
        print("\nEjemplos:")
        print("  python descargar_bit.py eth                # todos los TF, sale al dia")
        print("  python descargar_bit.py eth 5m,15m,1h      # solo esos TF")
        print("  python descargar_bit.py eth --loop         # daemon, todos los TF")
        print("  python descargar_bit.py btc 4h,1d --loop   # daemon, solo 4h y 1d")
        print(f"\nTF: {','.join(TIMEFRAMES)}")
        print(f"Sin CSV baja los ultimos {DIAS_DEFECTO} dias. Para el historial")
        print("completo: python descargar_hist_bit.py <coin> [TF,...]")
        print(f"\nCSV:  {DIR_VELAS}/[COIN]/bitget_[COIN]_[TF].csv")
        print(f"Lock: {DIR_LOCK}/[COIN]_[TF].lock")
        print(f"Log:  {DIR_LOG}/[COIN]_[TF].log")
        return

    loop = opciones['--loop']
    tfs = resolver_tfs(tfs_str)
    if not tfs:
        print(f"[ERROR] TF invalidos. Usa: {','.join(TIMEFRAMES)}")
        sys.exit(1)

    print(f"\n[{_extraer_moneda(coin)}] TF: {', '.join(tfs)}"
          f"{' - daemon' if loop else ' - salida al estar al dia'}")

    locks = []
    try:
        for tf in tfs:
            lock = Lock(coin, tf)
            lock.__enter__()
            locks.append(lock)

        for tf in tfs:
            print(f"\n[{datetime.now():%H:%M:%S}] {_extraer_moneda(coin)} {tf}", flush=True)
            _atender(coin, tf)

        if loop:
            daemon(coin, tfs)
        else:
            resumen(coin, tfs)
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n\n[SALIDA] {_extraer_moneda(coin)} - detenido por el usuario")
    finally:
        for lock in reversed(locks):
            lock.__exit__(None, None, None)


if __name__ == "__main__":
    main()
