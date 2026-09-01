#!/usr/bin/env python3
"""
sentimiento.py - Contexto de mercado (order book) alrededor de cada
operacion ejecutada, comparando ganadoras vs perdedoras.

Usa el backtest dinamico (eth_backtest_results_<tf>_dynamic.csv) como
fuente de operaciones con resultado conocido (win/loss), no el log de
señales crudo: no toda señal se convierte en trade (backtest.py descarta
las solapadas con una posicion ya abierta y las que siguen pendientes).

Para cada operacion toma una ventana relativa a su timestamp de señal
(antes/despues configurables, default 30min/60min) y promedia CVD, funding,
ratio long/short e imbalance del libro, mas el cambio de precio, dentro de
esa ventana. Compara despues el promedio de cada metrica entre operaciones
ganadoras y perdedoras.

Uso:
  python sentimiento.py                              # TF 1h, ETH, futuros
  python sentimiento.py --tf 15m
  python sentimiento.py --tf 5m --antes-min 15 --despues-min 30
"""

import argparse
import glob
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent  # neo/
DATOS_DIR = SCRIPT_DIR / "datos"
LIBRO_DIR = BASE_DIR / "libro" / "datos"

METRICAS = ['cvd_delta', 'funding_mean', 'long_short_mean', 'imbalance_mean', 'price_change_pct']


def _velas_dir(coin: str) -> Path:
    return BASE_DIR / "velas" / coin.upper()


def cargar_libro(coin: str, mercado: str) -> pd.DataFrame:
    patron = str(LIBRO_DIR / f"libro_{coin.upper()}_{mercado}_*.csv")
    archivos = sorted(glob.glob(patron))
    if not archivos:
        return pd.DataFrame()

    libro = pd.concat([pd.read_csv(f) for f in archivos], ignore_index=True)
    libro['fecha_utc'] = pd.to_datetime(libro['fecha_utc'], utc=True)
    for col in ('cvd', 'funding_rate_pct', 'long_short_ratio', 'imbalance'):
        if col in libro.columns:
            libro[col] = pd.to_numeric(libro[col], errors='coerce')
    return libro.sort_values('fecha_utc').reset_index(drop=True)


def cargar_precio(coin: str, mercado: str, ref_tf: str) -> pd.DataFrame:
    ruta = _velas_dir(coin) / f"bitget_{coin.upper()}_{ref_tf}_{mercado}.csv"
    if not ruta.exists():
        return pd.DataFrame()

    velas = pd.read_csv(ruta)
    velas['fecha_utc'] = pd.to_datetime(velas['fecha_utc'], utc=True)
    velas['close'] = pd.to_numeric(velas['close'], errors='coerce')
    return velas.sort_values('fecha_utc').reset_index(drop=True)


def contexto_ventana(ts, libro: pd.DataFrame, velas: pd.DataFrame,
                      antes: pd.Timedelta, despues: pd.Timedelta) -> dict:
    """Resume el contexto de mercado en [ts-antes, ts+despues]. Cada campo
    queda en None si no hay snapshots/velas suficientes en la ventana, en
    vez de forzar un 0 que se confundiria con un valor real observado."""
    ini, fin = ts - antes, ts + despues
    fila = {m: None for m in METRICAS}

    if not libro.empty:
        l = libro[(libro['fecha_utc'] >= ini) & (libro['fecha_utc'] <= fin)]
        # cvd se reinicia a 0 en cada rotacion de dia UTC de libro.py (nuevo
        # session_id): si la ventana cruza ese reset, iloc[-1]-iloc[0] resta
        # un cvd ya reiniciado contra uno de la sesion anterior y da un salto
        # que no es flujo real. Se descarta en vez de devolver un valor
        # erroneo (misma logica que el resto de la funcion: None > 0 falso).
        if (len(l) > 1 and 'cvd' in l.columns and 'session_id' in l.columns
                and l['session_id'].nunique() == 1):
            fila['cvd_delta'] = l['cvd'].iloc[-1] - l['cvd'].iloc[0]
        if 'funding_rate_pct' in l.columns and l['funding_rate_pct'].notna().any():
            fila['funding_mean'] = l['funding_rate_pct'].mean()
        if 'long_short_ratio' in l.columns and l['long_short_ratio'].notna().any():
            fila['long_short_mean'] = l['long_short_ratio'].mean()
        if 'imbalance' in l.columns and l['imbalance'].notna().any():
            fila['imbalance_mean'] = l['imbalance'].mean()

    if not velas.empty:
        v = velas[(velas['fecha_utc'] >= ini) & (velas['fecha_utc'] <= fin)]
        if len(v) > 1 and v['close'].iloc[0]:
            p0, p1 = v['close'].iloc[0], v['close'].iloc[-1]
            fila['price_change_pct'] = (p1 / p0 - 1) * 100

    return fila


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tf', type=str, default='1h', help='TF del backtest dinamico (default: 1h)')
    parser.add_argument('--coin', type=str, default='ETH', help='Moneda (default: ETH)')
    parser.add_argument('--mercado', type=str, default='futuros', help='Mercado (default: futuros)')
    parser.add_argument('--ref-tf', type=str, default='5m',
                       help='TF de velas para el precio de la ventana (default: 5m)')
    parser.add_argument('--antes-min', type=int, default=30, help='Minutos antes de la señal (default: 30)')
    parser.add_argument('--despues-min', type=int, default=60, help='Minutos despues de la señal (default: 60)')
    args = parser.parse_args()

    backtest_csv = DATOS_DIR / f"eth_backtest_results_{args.tf}_dynamic.csv"
    if not backtest_csv.exists():
        print(f"No hay backtest dinamico para TF {args.tf}: {backtest_csv} "
              f"(ejecutar 'python backtest.py --tf {args.tf}' primero)")
        return 1

    trades = pd.read_csv(backtest_csv)
    if trades.empty:
        print(f"Backtest vacio: {backtest_csv}")
        return 1
    trades['timestamp'] = pd.to_datetime(trades['timestamp'], format='mixed', utc=True)

    libro = cargar_libro(args.coin, args.mercado)
    velas = cargar_precio(args.coin, args.mercado, args.ref_tf)
    if libro.empty:
        print(f"[AVISO] Libro: sin ficheros para {args.coin} {args.mercado} en {LIBRO_DIR}")
    if velas.empty:
        print(f"[AVISO] Precio {args.ref_tf}: sin fichero de velas")

    antes = pd.Timedelta(minutes=args.antes_min)
    despues = pd.Timedelta(minutes=args.despues_min)

    filas = []
    for _, t in trades.iterrows():
        ctx = contexto_ventana(t['timestamp'], libro, velas, antes, despues)
        ctx['win'] = bool(t['win'])
        filas.append(ctx)
    df = pd.DataFrame(filas)

    print(f"\nContexto por operacion: -{args.antes_min}min / +{args.despues_min}min "
          f"alrededor de la señal ({len(df)} operaciones, TF {args.tf})\n")

    for grupo, etiqueta in ((True, 'GANADORAS'), (False, 'PERDEDORAS')):
        sub = df[df['win'] == grupo]
        print(f"-- {etiqueta} (n={len(sub)}) --")
        if sub.empty:
            print("   sin operaciones\n")
            continue
        for m in METRICAS:
            valores = sub[m].dropna()
            if valores.empty:
                print(f"   {m:18s}: sin datos suficientes en ventana")
            else:
                print(f"   {m:18s}: {valores.mean():+.4f}  (n={len(valores)})")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
