
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
    dias = ORIGEN_DIAS.get(timeframe)
    if dias is None:
        return None
    ahora = _cliente().milliseconds()
    inicio = ahora - dias * MS_DIA
    return inicio - (inicio % _tf_ms(timeframe))


def _atender(coin, timeframe, mercado):
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
        _log(coin, timeframe, mercado, f"[ERROR] {e}")
    return 0


def daemon(coin, tfs, mercado):
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
