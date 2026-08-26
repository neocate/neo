#!/usr/bin/env python3
"""
ETH Backtest - Comparar predicciones vs mercado real
Analiza eth_setup_log.csv vs velas reales
"""

import pandas as pd
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PATHS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCRIPT_DIR = Path(__file__).parent.parent
BASE_DIR = SCRIPT_DIR.parent  # neo/
VELAS_DIR = BASE_DIR / "velas" / "ETH"
DATA_DIR = SCRIPT_DIR / "datos"
LOG_DIR = SCRIPT_DIR / "log"

ANALYSIS_CSV = DATA_DIR / "eth_setup_log.csv"
BACKTEST_CSV = DATA_DIR / "eth_backtest_results.csv"
BACKTEST_LOG = LOG_DIR / "backtest.log"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(BACKTEST_LOG),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_analysis() -> pd.DataFrame:
    """Cargar registro de análisis"""
    if not ANALYSIS_CSV.exists():
        raise FileNotFoundError(f"Analysis file not found: {ANALYSIS_CSV}")

    df = pd.read_csv(ANALYSIS_CSV)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def load_price_data():
    """Cargar datos de precio de todas las velas"""
    files = {
        '5m': VELAS_DIR / "bitget_ETH_5m_futuros.csv",
    }

    data = {}
    for tf, file in files.items():
        if file.exists():
            try:
                df = pd.read_csv(file)
                df['fecha_utc'] = pd.to_datetime(df['fecha_utc'])
                data[tf] = df
            except Exception as e:
                logger.error(f"Error loading {tf}: {e}")
        else:
            logger.warning(f"Missing {tf} data: {file}")

    return data

def evaluate_prediction(row, price_data, hours_ahead=1):
    """
    Evaluar si una predicción fue correcta
    Compara precio N horas después vs precio actual
    """
    ts = row['timestamp']
    signal = row['signal']
    entry_price = row['price']

    df_5m = price_data.get('5m')
    if df_5m is None:
        return None

    # Buscar vela 5m actual
    mask = (df_5m['fecha_utc'] >= ts) & (df_5m['fecha_utc'] < ts + timedelta(minutes=5))
    current_candle = df_5m[mask]

    if current_candle.empty:
        return None

    # Buscar precio N horas después
    future_time = ts + timedelta(hours=hours_ahead)
    mask_future = (df_5m['fecha_utc'] >= future_time - timedelta(minutes=5)) & \
                  (df_5m['fecha_utc'] <= future_time + timedelta(minutes=5))
    future_candles = df_5m[mask_future]

    if future_candles.empty:
        return 'PENDING'

    future_close = future_candles.iloc[-1]['close']
    price_change = future_close - entry_price
    change_pct = (price_change / entry_price) * 100 if entry_price > 0 else 0

    # Evaluar resultado
    if signal == 'LONG':
        result = 'CORRECT' if price_change > 5 else ('PARTIAL' if price_change > 0 else 'WRONG')
        win = price_change > 0
    elif signal == 'SHORT':
        result = 'CORRECT' if price_change < -5 else ('PARTIAL' if price_change < 0 else 'WRONG')
        win = price_change < 0
    else:  # WAIT
        return 'PENDING'

    return {
        'timestamp': ts,
        'signal': signal,
        'entry_price': entry_price,
        'future_price': future_close,
        'change_pct': change_pct,
        'result': result,
        'win': win
    }

def backtest(hours_ahead=1):
    """Ejecutar backtest"""

    print("\n" + "═" * 70)
    print(f"ETH SETUP ANALYZER - BACKTEST REPORT ({hours_ahead}h ahead)")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═" * 70 + "\n")

    logger.info("Starting backtest...")

    try:
        analysis = load_analysis()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        logger.error(f"Backtest failed: {e}")
        return False

    price_data = load_price_data()

    if not price_data:
        print("❌ No price data loaded")
        logger.error("No price data available")
        return False

    print(f"Total analysis records: {len(analysis)}")
    print(f"Price data available: {', '.join(price_data.keys())}")
    print(f"\nEvaluating predictions ({hours_ahead}h ahead)...\n")

    # Evaluar predicciones
    results = []
    for idx, (_, row) in enumerate(analysis.iterrows()):
        result = evaluate_prediction(row, price_data, hours_ahead=hours_ahead)
        if result is not None and result != 'PENDING':
            results.append(result)

    if not results:
        print("❌ No completed predictions to analyze yet")
        logger.warning("No completed predictions")
        return False

    # Guardar resultados
    df_results = pd.DataFrame(results)
    df_results.to_csv(BACKTEST_CSV, index=False)
    logger.info(f"Results saved to {BACKTEST_CSV}")

    # Estadísticas por signal
    print("RESULTS BY SIGNAL:")
    print("-" * 70)

    for signal in ['LONG', 'SHORT']:
        mask = df_results['signal'] == signal
        subset = df_results[mask]

        if len(subset) == 0:
            continue

        completed = subset[subset['result'].isin(['CORRECT', 'WRONG'])]
        if len(completed) == 0:
            continue

        wins = completed['win'].sum()
        total = len(completed)
        win_rate = (wins / total) * 100 if total > 0 else 0
        avg_change = completed['change_pct'].mean()
        max_win = completed['change_pct'].max()
        max_loss = completed['change_pct'].min()

        print(f"\n{signal:6s} | Trades: {len(subset):3d} | Completed: {total:3d}")
        print(f"       | Win Rate: {win_rate:6.1f}% | Avg Change: {avg_change:+7.2f}%")
        print(f"       | Best: {max_win:+7.2f}% | Worst: {max_loss:+7.2f}%")

    # Overall stats
    print("\n" + "=" * 70)
    print("OVERALL STATISTICS:")
    print("-" * 70)

    completed = df_results[df_results['result'].isin(['CORRECT', 'WRONG'])]
    if len(completed) > 0:
        win_rate = (completed['win'].sum() / len(completed)) * 100
        avg_pnl = completed['change_pct'].mean()
        max_win = completed['change_pct'].max()
        max_loss = completed['change_pct'].min()
        total_change = completed['change_pct'].sum()

        print(f"Total Trades:          {len(completed):3d}")
        print(f"Win Rate:              {win_rate:6.1f}%")
        print(f"Avg P&L:               {avg_pnl:+7.2f}%")
        print(f"Total P&L:             {total_change:+7.2f}%")
        print(f"Best Trade:            {max_win:+7.2f}%")
        print(f"Worst Trade:           {max_loss:+7.2f}%")
        print(f"Pending:               {len(df_results[df_results['result']=='PENDING']):3d}")

    print("\n" + "=" * 70 + "\n")
    logger.info("Backtest completed successfully")

    return True

if __name__ == "__main__":
    try:
        hours = 1
        if len(sys.argv) > 1:
            hours = int(sys.argv[1])

        success = backtest(hours_ahead=hours)
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)
