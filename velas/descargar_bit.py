# ================================================================================
# descargar_bit.py - PRODUCCION (implementacion comun)
#
# Mantiene al dia los CSV de velas cerradas. Es el que corre siempre.
# La maquinaria esta en velas_bit.py (rutas, lock, log, CSV, huecos).
#
# NO SE EJECUTA DIRECTAMENTE: no sabe de que mercado hablas. Se ejecuta por
# uno de sus dos frontales, que es donde se decide:
#
#   python descargar_bit_spot.py eth --loop
#   python descargar_bit_futuros.py eth --loop
#
# Para bajar el historial completo NO es este fichero: es
# descargar_hist_bit_[mercado].py, que se corre dos o tres veces y ya.
#
#   Sin CSV      -> baja ventana inicial por TF desde ORIGEN_DIAS (suficiente
#                   para indicadores; el historial largo lo pone el otro fichero)
#   Con CSV      -> añade solo lo que falta hasta actual-1
#   Con --loop   -> daemon: cada TF se despierta tras SU propio cierre
#
# El endpoint lo elige velas_bit segun la antiguedad de cada peticion: aqui
# casi siempre sale el reciente (1000 velas/peticion). Si un equipo lleva
# mas dias parado que la ventana del endpoint reciente (1m 30 dias, 1h 60),
# ese tramo cae solo al historico sin que haya que hacer nada.
#
# CUANDO SE DESPIERTA (y por que no en el cierre)
#   Antes se despertaba en el cierre +1s. Una vela recien cerrada todavia no
#   esta sellada: Bitget sigue consolidandola unos segundos, y como el CSV es
#   append-only, ese valor provisional se quedaba escrito para siempre. Medido
#   sobre el historial: leidas a +1s salian mal el 27% de las 3m, el 15% de
#   las 5m y el 80% de las 1d. Ahora se espera MARGEN_CIERRE y ademas la vela
#   se confirma con dos lecturas (F4 en velas_bit). Si no se confirma no se
#   guarda nada y la pasada siguiente la recoge ya sellada.
# ================================================================================

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ccxt

from velas_bit import (
    CONFIRMA_SEG, DIR_LOCK, DIR_LOG, DIR_VELAS, MARGEN_CIERRE, MERCADOS,
    MS_DIA, ORIGEN_DIAS, TIMEFRAMES,
    Lock, _archivo, _cliente, _estado_vela, _extraer_moneda, _log,
    _tf_ms, _ts_actual, parsear_args, poner_al_dia, resolver_tfs, resumen,
)


def _origen_arranque(timeframe):
    """Punto de partida cuando no hay CSV: ventana en dias segun el TF.
    
    Solo es el arranque en frio de un equipo nuevo: para los indicadores sobra,
    y el historial largo lo trae descargar_hist_bit_[mercado].py.
    """
    dias = ORIGEN_DIAS.get(timeframe)
    if dias is None:
        return None
    ahora = _cliente().milliseconds()
    inicio = ahora - dias * MS_DIA
    return inicio - (inicio % _tf_ms(timeframe))


def _atender(coin, timeframe, mercado):
    """Pone al dia un TF, informando de lo que pasa."""
    try:
        añadidas = poner_al_dia(coin, timeframe, mercado,
                                origen_ts=_origen_arranque(timeframe))
        if añadidas == 0:
            estado = _estado_vela(_archivo(coin, timeframe, mercado))
            print(f"  Al dia ({estado['fecha'] if estado else 'sin datos'})", flush=True)
        return añadidas
    except ccxt.BaseError as e:
        _log(coin, timeframe, mercado, f"[ERROR API] {e}")
    except OSError as e:
        _log(coin, timeframe, mercado, f"[ERROR IO] {e}")
    except RuntimeError as e:
        # .meta que no cuadra: es un fallo de configuracion, no de red. Que se
        # vea y que no se escriba nada.
        _log(coin, timeframe, mercado, f"[ERROR] {e}")
    return 0


def daemon(coin, tfs, mercado):
    """Cada TF con su propio reloj: se duerme hasta MARGEN_CIERRE despues del
    cierre mas proximo y se atienden SOLO los TF que han cerrado."""
    print(f"\n{'='*60}")
    print(f"[DAEMON] {_extraer_moneda(coin)} {mercado} - TF: {', '.join(tfs)}")
    print(f"  Cada TF se despierta {MARGEN_CIERRE}s despues de su cierre,")
    print(f"  y la vela se confirma con dos lecturas separadas {CONFIRMA_SEG}s")
    print(f"{'='*60}", flush=True)

    while True:
        ahora_ms = _cliente().milliseconds()
        proximos = {tf: _ts_actual(tf, ahora_ms) + _tf_ms(tf) for tf in tfs}
        objetivo = min(proximos.values())
        vencen = [tf for tf in tfs if proximos[tf] == objetivo]
        espera = (objetivo - ahora_ms) / 1000 + MARGEN_CIERRE

        print(f"\n[{datetime.now(timezone.utc):%H:%M:%S}] Proximo cierre: "
              f"{', '.join(vencen)} en {espera:.0f}s", flush=True)
        time.sleep(max(0, espera))

        for tf in vencen:
            _atender(coin, tf, mercado)


def _ayuda(mercado, script):
    print("Uso:")
    print(f"  python {script} <coin> [TF,...] [--loop]")
    print("\nEjemplos:")
    print(f"  python {script} eth                # todos los TF, sale al dia")
    print(f"  python {script} eth 5m,15m,1h      # solo esos TF")
    print(f"  python {script} eth --loop         # daemon, todos los TF")
    print(f"  python {script} btc 4h,1d --loop   # daemon, solo 4h y 1d")
    print(f"\nMercado: {mercado} ({MERCADOS[mercado].format('ETH')})")
    print(f"TF: {','.join(TIMEFRAMES)}")
    print("Sin CSV baja ventana inicial por TF (ver ORIGEN_DIAS en velas_bit.py).")
    print(f"Para el historial completo: python descargar_hist_bit_{mercado}.py <coin> [TF,...]")
    print(f"\nCSV:  {DIR_VELAS}/[COIN]/bitget_[COIN]_[TF]_{mercado}.csv")
    print(f"Lock: {DIR_LOCK}/[COIN]_[TF]_{mercado}.lock")
    print(f"Log:  {DIR_LOG}/[COIN]_[TF]_{mercado}.log")


def main(mercado, argv=None, script='descargar_bit.py'):
    """Punto de entrada. 'mercado' es obligatorio y lo fija el frontal."""
    if mercado not in MERCADOS:
        raise ValueError(f"mercado invalido: {mercado!r}. Usa: {', '.join(MERCADOS)}")

    argv = sys.argv[1:] if argv is None else argv
    try:
        coin, tfs_str, opciones = parsear_args(argv, banderas=('--loop',))
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    if opciones['--help'] or not coin:
        _ayuda(mercado, script)
        return

    loop = opciones['--loop']
    try:
        tfs = resolver_tfs(tfs_str)
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print(f"\n[{_extraer_moneda(coin)} {mercado}] TF: {', '.join(tfs)}"
          f"{' - daemon' if loop else ' - salida al estar al dia'}")

    locks = []
    try:
        for tf in tfs:
            lock = Lock(coin, tf, mercado)
            lock.__enter__()
            locks.append(lock)

        for tf in tfs:
            print(f"\n[{datetime.now(timezone.utc):%H:%M:%S}] "
                  f"{_extraer_moneda(coin)} {tf} {mercado}", flush=True)
            _atender(coin, tf, mercado)

        if loop:
            daemon(coin, tfs, mercado)
        else:
            resumen(coin, tfs, mercado)
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n\n[SALIDA] {_extraer_moneda(coin)} {mercado} - detenido por el usuario")
    finally:
        for lock in reversed(locks):
            lock.__exit__(None, None, None)


if __name__ == "__main__":
    print(__doc__ or "")
    print("[ERROR] descargar_bit.py no se ejecuta directamente: no sabe de que")
    print("        mercado hablas. Usa uno de sus dos frontales:")
    print("          python descargar_bit_spot.py <coin> [TF,...] [--loop]")
    print("          python descargar_bit_futuros.py <coin> [TF,...] [--loop]")
    sys.exit(1)
