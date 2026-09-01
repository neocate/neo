#!/usr/bin/env python3
"""
desglose_ls.py - Desglose LONG vs SHORT del backtest dinamico (margen
aislado) de uno o varios TF, separando automaticamente "antes" y "despues"
del ultimo cambio de params.json detectado en config_changes.log para ese TF.

Regenera eth_backtest_results_<tf>_dynamic.csv llamando a
backtest_tf_dynamic() antes de desglosar, asi que siempre lee resultados
frescos.

Uso:
  python desglose_ls.py                  # 5m, 15m y 1h
  python desglose_ls.py --tf 15m
  python desglose_ls.py --tf 15m --hours 2
  python desglose_ls.py --sin-horizonte  # solo stop/tp/reversal, sin cierre por tiempo
"""

import argparse
import re
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "datos"
CONFIG_LOG = DATA_DIR / "config_changes.log"

import sys
sys.path.insert(0, str(SCRIPT_DIR))
import backtest as bt


def ultimo_cambio_config(tf: str):
    """Timestamp UTC (tz-aware) del ultimo cambio de config detectado para
    ese TF en config_changes.log (lo escribe analyzer.py cada vez que el
    hash de su config cambia), o None si no hay ninguno registrado."""
    if not CONFIG_LOG.exists():
        return None

    texto = CONFIG_LOG.read_text(encoding='utf-8')
    ultimo = None
    for bloque in texto.split('=' * 60):
        m_ts = re.search(r'Timestamp:\s*(\S+)', bloque)
        m_tf = re.search(r'TF:\s*(\S+)', bloque)
        if m_ts and m_tf and m_tf.group(1) == tf:
            ts = pd.to_datetime(m_ts.group(1), utc=True)
            if ultimo is None or ts > ultimo:
                ultimo = ts
    return ultimo


def _tabla(df: pd.DataFrame, etiqueta: str):
    print(f"  -- {etiqueta} ({len(df)} señales) --")
    for signal in ('LONG', 'SHORT'):
        sub = df[df['signal'] == signal]
        completas = sub[sub['result'].isin(['CORRECT', 'WRONG'])]
        if len(completas) == 0:
            print(f"     {signal}: sin trades completos")
            continue
        wins = completas['win'].sum()
        n = len(completas)
        wr = wins / n * 100
        total = completas['pnl_escalado'].sum()
        avg = completas['pnl_escalado'].mean()
        print(f"     {signal}: n={n:4d}  win_rate={wr:5.1f}%  total_pnl={total:9.2f}  avg={avg:+7.2f}")


def desglosar(tf: str, hours_ahead):
    print(f"\n=== {tf} ===")
    if not bt.backtest_tf_dynamic(tf, hours_ahead=hours_ahead):
        return

    csv_file = DATA_DIR / f"eth_backtest_results_{tf}_dynamic.csv"
    df = pd.read_csv(csv_file)
    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', utc=True)

    corte = ultimo_cambio_config(tf)
    if corte is None:
        print(f"  Sin cambios de config registrados todavia para {tf} en {CONFIG_LOG.name}")
        _tabla(df, 'TODO EL HISTORICO')
        return

    print(f"  Ultimo cambio de params.json detectado: {corte}")
    _tabla(df[df['timestamp'] < corte], 'ANTES del cambio')
    _tabla(df[df['timestamp'] >= corte], 'DESPUES del cambio')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tf', type=str, choices=['5m', '15m', '1h', '4h'], default=None,
                       help='TF especifico (default: 5m, 15m y 1h)')
    parser.add_argument('--hours', type=int, default=1,
                       help='Horizonte del backtest en horas (default: 1). Ignorado si se pasa --sin-horizonte')
    parser.add_argument('--sin-horizonte', action='store_true',
                       help='Desactiva el cierre forzado por horizonte: solo sale por stop-loss, '
                            'take-profit o reversal')
    args = parser.parse_args()

    hours_ahead = None if args.sin_horizonte else args.hours
    for tf in ([args.tf] if args.tf else ['5m', '15m', '1h']):
        desglosar(tf, hours_ahead)


if __name__ == "__main__":
    main()
