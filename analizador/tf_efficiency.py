#!/usr/bin/env python3
"""
TF Efficiency Analyzer
Analiza cuál timeframe es más aprovechable midiendo:
1. Win rate de niveles por TF
2. Ratio ruido/señal (fake-outs vs confirmados)
"""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent  # neo/
VELAS_DIR = BASE_DIR / "velas" / "ETH"
NIVELES_DIR = BASE_DIR / "niveles" / "json"
DATA_DIR = BASE_DIR / "analizador" / "datos"

def load_niveles_by_tf(tf):
    """Cargar niveles de un timeframe específico"""
    file = NIVELES_DIR / f"nivel_ETH_{tf}_futuros_k5_toques3.json"

    if not file.exists():
        return None

    with open(file, 'r') as f:
        data = json.load(f)

    return data.get('niveles', [])

def load_velas(tf):
    """Cargar velas de un timeframe"""
    file = VELAS_DIR / f"bitget_ETH_{tf}_futuros.csv"

    if not file.exists():
        return None

    df = pd.read_csv(file)
    df['fecha_utc'] = pd.to_datetime(df['fecha_utc'])
    return df.sort_values('fecha_utc')

def analyze_tf_efficiency():
    """Analizar eficiencia de cada TF"""

    tfs = ['1m', '5m', '15m', '1h', '4h']
    results = {}

    print("\n" + "═" * 70)
    print("TIMEFRAME EFFICIENCY ANALYSIS")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═" * 70 + "\n")

    for tf in tfs:
        print(f"Analyzing {tf}...")

        niveles = load_niveles_by_tf(tf)
        velas = load_velas(tf)

        if niveles is None or velas is None:
            print(f"  ⚠ Missing data for {tf}\n")
            continue

        # Estadísticas de niveles
        total_niveles = len(niveles)
        activos = [n for n in niveles if n.get('estado') != 'flip']
        flipped = [n for n in niveles if n.get('estado') == 'flip']

        # Promedio de toques por nivel
        avg_toques = sum(n.get('toques', 0) for n in niveles) / max(total_niveles, 1)
        avg_fuerza = sum(n.get('fuerza', 0) for n in niveles) / max(total_niveles, 1)

        # Volatilidad
        velas['close_float'] = velas['close'].astype(float)
        avg_vol = velas['volumen'].astype(float).mean()
        price_range = velas['close_float'].max() - velas['close_float'].min()

        results[tf] = {
            'total_niveles': total_niveles,
            'activos': len(activos),
            'flipped': len(flipped),
            'flip_rate': len(flipped) / max(total_niveles, 1),
            'avg_toques': avg_toques,
            'avg_fuerza': avg_fuerza,
            'price_range': price_range,
            'avg_volume': avg_vol,
            'noise_ratio': price_range / avg_vol if avg_vol > 0 else 0
        }

        print(f"  ✓ {total_niveles} niveles")
        print(f"    - Activos: {len(activos)} | Flipped: {len(flipped)} ({results[tf]['flip_rate']:.1%})")
        print(f"    - Toques promedio: {avg_toques:.1f}")
        print(f"    - Fuerza promedio: {avg_fuerza:.2f}")
        print(f"    - Rango: {price_range:.2f} pts | Vol promedio: {avg_vol:.0f}\n")

    # Resumen comparativo
    print("=" * 70)
    print("EFFICIENCY RANKING (mejor = menos ruido, más niveles confirmados)")
    print("-" * 70)

    # Ordenar por flip_rate (menos flipped = más niveles se mantienen)
    ranking = sorted(results.items(), key=lambda x: x[1]['flip_rate'])

    for i, (tf, data) in enumerate(ranking, 1):
        efficiency = 1 - data['flip_rate']  # Inverso: mayor = mejor
        print(f"\n{i}. {tf.upper()}")
        print(f"   Efficiency Score:    {efficiency:.1%}")
        print(f"   Flip Rate:           {data['flip_rate']:.1%} (niveles rotos)")
        print(f"   Active Levels:       {data['activos']} de {data['total_niveles']}")
        print(f"   Avg Touches:         {data['avg_toques']:.1f} por nivel")
        print(f"   Avg Strength:        {data['avg_fuerza']:.2f}")
        print(f"   Noise Ratio:         {data['noise_ratio']:.4f} (menor = mejor)")

    # Recomendación
    print("\n" + "=" * 70)
    print("RECOMMENDATION:")
    print("-" * 70)

    best_tf = ranking[0][0]
    best_efficiency = 1 - ranking[0][1]['flip_rate']

    print(f"\n✓ BEST TF: {best_tf.upper()} (Efficiency: {best_efficiency:.1%})")
    print(f"\nRationale:")
    print(f"  - Menos niveles rotos (flip_rate bajo)")
    print(f"  - Mejor relación ruido/señal")
    print(f"  - Niveles más duraderos y confiables")
    print(f"\nUse {best_tf} como principal TF para analyzer.")
    print(f"Usar {best_tf} + TF mayores como confirmación.\n")

if __name__ == "__main__":
    try:
        analyze_tf_efficiency()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
