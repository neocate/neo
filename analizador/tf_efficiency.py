#!/usr/bin/env python3
"""
tf_efficiency.py - Ranking de timeframes basado en el backtest dinamico
(expectancy neta, profit factor, drawdown, costes, LONG/SHORT por
separado), no en flip_rate de niveles.

El flip_rate y el "noise_ratio" de niveles se mantienen como seccion
exploratoria al final, pero ya no deciden el TF recomendado: flip_rate no
equivale a rentabilidad, y price_range/avg_volume mezcla unidades sin ser
una medida de ruido solida.

No hace walk-forward ni validacion fuera de muestra (queda pendiente, ver
docstring de metricas_backtest): compara el historico completo de cada TF
partido en dos mitades temporales como proxy simple de estabilidad. Tampoco
imprime una recomendacion automatica de "usar este TF": el ranking es un
insumo para decidir, no la decision.

Uso:
  python tf_efficiency.py
  python tf_efficiency.py --tf 15m
  python tf_efficiency.py --hours 2 --min-trades 30
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent  # neo/
NIVELES_DIR = BASE_DIR / "niveles" / "json"
DATA_DIR = SCRIPT_DIR / "datos"

sys.path.insert(0, str(SCRIPT_DIR))
import backtest as bt

TFS_DEFAULT = ['5m', '15m', '1h']


def metricas_backtest(tf: str, hours_ahead: int, coin: str, mercado: str,
                       exec_tf: str, sl_pct: float, tp_pct: float,
                       capital: float, margin_pct: float):
    """Regenera (via backtest_tf_dynamic) y lee el backtest dinamico de un
    TF. Calcula expectancy neta, profit factor, drawdown maximo, costes
    totales, desglose LONG/SHORT y una estabilidad simple (primera vs
    segunda mitad del historico, no walk-forward real). Devuelve None si no
    hay resultados."""
    if not bt.backtest_tf_dynamic(tf, hours_ahead, capital, margin_pct,
                                   coin, mercado, exec_tf, sl_pct, tp_pct):
        return None

    csv_file = DATA_DIR / f"eth_backtest_results_{tf}_dynamic.csv"
    df = pd.read_csv(csv_file)
    if df.empty:
        return None
    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', utc=True)
    df = df.sort_values('timestamp').reset_index(drop=True)

    n = len(df)
    expectancy = df['pnl_escalado'].mean()

    ganancias = df.loc[df['pnl_escalado'] > 0, 'pnl_escalado'].sum()
    perdidas = -df.loc[df['pnl_escalado'] < 0, 'pnl_escalado'].sum()
    profit_factor = (ganancias / perdidas) if perdidas > 0 else float('inf')

    # Drawdown sobre la serie de capital que ya registra el backtest.
    capital_serie = df['capital_despues']
    maximo_acumulado = capital_serie.cummax()
    drawdown = maximo_acumulado - capital_serie
    max_dd_usdt = drawdown.max()
    max_dd_pct = (drawdown / maximo_acumulado).max()

    por_lado = {}
    for signal in ('LONG', 'SHORT'):
        sub = df[df['signal'] == signal]
        por_lado[signal] = None if len(sub) == 0 else {
            'n': len(sub),
            'win_rate': sub['win'].sum() / len(sub),
            'expectancy': sub['pnl_escalado'].mean(),
            'total_pnl': sub['pnl_escalado'].sum(),
        }

    # Estabilidad temporal: primera vs segunda mitad del historico (proxy
    # simple; falta walk-forward/out-of-sample real, ver docstring del
    # modulo).
    mitad = n // 2
    exp_primera = df.iloc[:mitad]['pnl_escalado'].mean() if mitad > 0 else None
    exp_segunda = df.iloc[mitad:]['pnl_escalado'].mean() if (n - mitad) > 0 else None
    estable = (exp_primera is not None and exp_segunda is not None
               and (exp_primera > 0) == (exp_segunda > 0))

    return {
        'n_trades': n,
        'win_rate': df['win'].sum() / n if n else 0.0,
        'expectancy': expectancy,
        'profit_factor': profit_factor,
        'total_pnl': df['pnl_escalado'].sum(),
        'max_drawdown_usdt': max_dd_usdt,
        'max_drawdown_pct': max_dd_pct,
        'total_fees': df['comisiones'].sum(),
        'por_lado': por_lado,
        'exp_primera_mitad': exp_primera,
        'exp_segunda_mitad': exp_segunda,
        'estable': estable,
    }


def metricas_niveles(tf: str):
    """Estadisticas exploratorias de niveles (flip_rate, toques, fuerza).
    Informativas: ya no se usan para decidir el TF (ver docstring)."""
    file = NIVELES_DIR / f"nivel_ETH_{tf}_futuros_k5_toques3.json"
    if not file.exists():
        return None

    with open(file, 'r') as f:
        niveles = json.load(f).get('niveles', [])
    if not niveles:
        return None

    total = len(niveles)
    flipped = [n for n in niveles if n.get('estado') == 'flip']
    return {
        'total_niveles': total,
        'flip_rate': len(flipped) / total,
        'avg_toques': sum(n.get('toques', 0) for n in niveles) / total,
        'avg_fuerza': sum(n.get('fuerza', 0) for n in niveles) / total,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tf', type=str, choices=TFS_DEFAULT, default=None,
                       help='TF especifico (default: 5m, 15m y 1h)')
    parser.add_argument('--hours', type=int, default=1, help='Horizonte del backtest en horas (default: 1)')
    parser.add_argument('--coin', type=str, default='ETH', help='Moneda (default: ETH)')
    parser.add_argument('--mercado', type=str, default='futuros', help='Mercado (default: futuros)')
    parser.add_argument('--exec-tf', type=str, default='5m', help='Vela de ejecucion (default: 5m)')
    parser.add_argument('--stop-pct', type=float, default=0.03, help='Stop-loss, fraccion (default: 0.03)')
    parser.add_argument('--take-profit-pct', type=float, default=0.10, help='Take-profit, fraccion (default: 0.10)')
    parser.add_argument('--capital', type=float, default=25, help='Margen inicial, USDT (default: 25)')
    parser.add_argument('--margin-pct', type=float, default=0.10, help='Margen aislado, fraccion (default: 0.10)')
    parser.add_argument('--min-trades', type=int, default=20,
                       help='Minimo de operaciones para entrar en el ranking (default: 20)')
    args = parser.parse_args()

    tfs = [args.tf] if args.tf else TFS_DEFAULT

    print("\n" + "=" * 70)
    print("TIMEFRAME EFFICIENCY (basado en backtest dinamico)")
    print("=" * 70)

    resultados = {}
    for tf in tfs:
        print(f"\nBacktest {tf}...")
        m = metricas_backtest(tf, args.hours, args.coin, args.mercado, args.exec_tf,
                               args.stop_pct, args.take_profit_pct, args.capital, args.margin_pct)
        if m is None:
            print(f"  [AVISO] Sin resultados de backtest para {tf}")
            continue
        resultados[tf] = m

        print(f"  Trades: {m['n_trades']:4d}  Win Rate: {m['win_rate']:6.1%}  "
              f"Expectancy: {m['expectancy']:+8.3f} USDT  Profit Factor: {m['profit_factor']:.2f}")
        print(f"  Drawdown max: {m['max_drawdown_usdt']:8.2f} USDT ({m['max_drawdown_pct']:.1%})  "
              f"Fees totales: {m['total_fees']:8.2f} USDT")
        for signal in ('LONG', 'SHORT'):
            lado = m['por_lado'][signal]
            if lado is None:
                print(f"    {signal}: sin trades")
            else:
                print(f"    {signal}: n={lado['n']:4d}  win_rate={lado['win_rate']:6.1%}  "
                      f"expectancy={lado['expectancy']:+8.3f}  total_pnl={lado['total_pnl']:+9.2f}")
        if m['exp_primera_mitad'] is not None and m['exp_segunda_mitad'] is not None:
            print(f"  Estabilidad (1a vs 2a mitad): {m['exp_primera_mitad']:+.3f} vs "
                  f"{m['exp_segunda_mitad']:+.3f} ({'estable' if m['estable'] else 'inestable'})")
        else:
            print("  Estabilidad: datos insuficientes")

    if not resultados:
        print("\nSin datos suficientes: ningun TF tuvo backtest con resultados.")
        return

    print("\n" + "=" * 70)
    print(f"RANKING (por expectancy neta, minimo {args.min_trades} operaciones)")
    print("-" * 70)

    elegibles = {tf: m for tf, m in resultados.items() if m['n_trades'] >= args.min_trades}
    excluidos = sorted(set(resultados) - set(elegibles))
    if excluidos:
        print(f"Excluidos por muestra insuficiente (<{args.min_trades} trades): {', '.join(excluidos)}")

    ranking = sorted(elegibles.items(), key=lambda kv: kv[1]['expectancy'], reverse=True)
    for i, (tf, m) in enumerate(ranking, 1):
        print(f"{i}. {tf.upper():4s}  expectancy={m['expectancy']:+8.3f}  "
              f"profit_factor={m['profit_factor']:.2f}  n={m['n_trades']:4d}  "
              f"drawdown={m['max_drawdown_pct']:.1%}  {'estable' if m['estable'] else 'inestable'}")

    if ranking:
        print("\nEsto es un ranking sobre el historico disponible, no una validacion "
              "fuera de muestra: falta walk-forward antes de fijar un TF definitivo.")
    else:
        print("Ningun TF alcanza el minimo de operaciones para figurar en el ranking.")

    # Niveles: exploratorio, no entra en el ranking (ver docstring).
    print("\n" + "=" * 70)
    print("NIVELES (exploratorio, no decide el TF)")
    print("-" * 70)
    for tf in tfs:
        nv = metricas_niveles(tf)
        if nv is None:
            print(f"  {tf}: sin datos de niveles")
            continue
        print(f"  {tf}: {nv['total_niveles']:3d} niveles | flip_rate={nv['flip_rate']:.1%} | "
              f"toques_prom={nv['avg_toques']:.1f} | fuerza_prom={nv['avg_fuerza']:.2f}")


if __name__ == "__main__":
    main()
