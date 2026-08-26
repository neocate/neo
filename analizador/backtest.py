#!/usr/bin/env python3
"""
ETH Backtest - Comparar predicciones vs mercado real
Analiza eth_setup_log.csv vs velas reales con P&L realista
Incluye comisiones, slippage y datos reales del contrato
"""

import pandas as pd
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PATHS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent  # neo/
VELAS_DIR = BASE_DIR / "velas" / "ETH"
DATA_DIR = SCRIPT_DIR / "datos"
LOG_DIR = SCRIPT_DIR / "log"
MERCADO_DIR = BASE_DIR / "mercado"

ANALYSIS_CSV = DATA_DIR / "eth_setup_log.csv"
BACKTEST_CSV = DATA_DIR / "eth_backtest_results.csv"
BACKTEST_LOG = LOG_DIR / "backtest.log"

# Agregar mercado al path
sys.path.insert(0, str(MERCADO_DIR))

try:
    from contrato import obtener_contrato
    CONTRATO_DISPONIBLE = True
except ImportError:
    CONTRATO_DISPONIBLE = False
    logger_temp = logging.getLogger(__name__)
    logger_temp.warning("contrato.py no disponible, usando valores por defecto")

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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONTRATO Y COMISIONES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_fees():
    """Obtener comisiones reales del contrato o usar defaults"""
    if not CONTRATO_DISPONIBLE:
        # Defaults Bitget futuros ETH
        return {
            'taker': 0.0006,  # 0.06%
            'maker': 0.0002,  # 0.02%
            'slippage': 0.0003  # 0.03% promedio de slippage
        }

    try:
        contrato = obtener_contrato('ETH/USDT:USDT')
        return {
            'taker': contrato['comision_taker'],
            'maker': contrato['comision_maker'],
            'slippage': 0.0003  # Estimado
        }
    except Exception as e:
        logger.warning(f"Error leyendo contrato: {e}, usando defaults")
        return {
            'taker': 0.0006,
            'maker': 0.0002,
            'slippage': 0.0003
        }

def calculate_pnl(signal, entry_price, exit_price, size=1.0, fees=None):
    """
    Calcula P&L realista incluyendo comisiones y slippage
    
    Args:
        signal: 'LONG' o 'SHORT'
        entry_price: Precio de entrada
        exit_price: Precio de salida
        size: Cantidad de contratos (default 1)
        fees: Dict con comisiones {'taker': 0.0006, 'slippage': 0.0003}
    
    Returns:
        dict con {
            'entry_price': float,
            'exit_price': float,
            'price_change_pct': float (sin comisiones),
            'pnl_gross': float (ganancia bruta),
            'comisiones': float (total comisiones),
            'pnl_neto': float (ganancia neta),
            'pnl_pct': float (% neto)
        }
    """
    if fees is None:
        fees = get_fees()

    # Comisiones totales (entry taker + exit taker + slippage)
    total_fees = fees['taker'] * 2 + fees['slippage']

    if signal == 'LONG':
        # LONG: compra en entry, vende en exit
        price_change = exit_price - entry_price
        price_change_pct = (price_change / entry_price) * 100 if entry_price > 0 else 0

        # P&L bruto (sin comisiones)
        pnl_gross = price_change * size

        # Comisiones en USDT
        comisiones = (entry_price * size * total_fees)

        # P&L neto
        pnl_neto = pnl_gross - comisiones

        # P&L %
        pnl_pct = (pnl_neto / (entry_price * size)) * 100 if entry_price > 0 else 0

    elif signal == 'SHORT':
        # SHORT: vende en entry, compra en exit
        price_change = entry_price - exit_price
        price_change_pct = (price_change / entry_price) * 100 if entry_price > 0 else 0

        # P&L bruto (sin comisiones)
        pnl_gross = price_change * size

        # Comisiones en USDT
        comisiones = (entry_price * size * total_fees)

        # P&L neto
        pnl_neto = pnl_gross - comisiones

        # P&L %
        pnl_pct = (pnl_neto / (entry_price * size)) * 100 if entry_price > 0 else 0

    else:
        # WAIT - sin posición
        return None

    return {
        'entry_price': entry_price,
        'exit_price': exit_price,
        'price_change_pct': price_change_pct,
        'pnl_gross': pnl_gross,
        'comisiones': comisiones,
        'pnl_neto': pnl_neto,
        'pnl_pct': pnl_pct
    }

def evaluate_prediction(row, price_data, hours_ahead=1, fees=None):
    """
    Evaluar si una predicción fue correcta con P&L realista
    Compara precio N horas después vs precio actual
    Incluye comisiones, slippage y fees reales del contrato
    """
    if fees is None:
        fees = get_fees()

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

    # Calcular P&L realista con comisiones
    pnl_data = calculate_pnl(signal, entry_price, future_close, size=1.0, fees=fees)

    if pnl_data is None:
        return 'PENDING'

    # Evaluar resultado (CORRECT si P&L neto positivo)
    if pnl_data['pnl_neto'] > 0:
        result = 'CORRECT'
        win = True
    elif pnl_data['pnl_neto'] < -5:
        result = 'WRONG'
        win = False
    else:
        result = 'PARTIAL'
        win = None

    return {
        'timestamp': ts,
        'signal': signal,
        'entry_price': entry_price,
        'exit_price': future_close,
        'price_change_pct': pnl_data['price_change_pct'],
        'pnl_gross': pnl_data['pnl_gross'],
        'pnl_neto': pnl_data['pnl_neto'],
        'pnl_pct': pnl_data['pnl_pct'],
        'comisiones': pnl_data['comisiones'],
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

    # Cargar comisiones reales
    fees = get_fees()
    print(f"\nComisiones (desde contrato.py):")
    print(f"  Taker:    {fees['taker']*100:.4f}%")
    print(f"  Slippage: {fees['slippage']*100:.4f}%")
    print(f"  Total:    {(fees['taker']*2 + fees['slippage'])*100:.4f}% por operación\n")

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

    # Evaluar predicciones con comisiones reales
    results = []
    for idx, (_, row) in enumerate(analysis.iterrows()):
        result = evaluate_prediction(row, price_data, hours_ahead=hours_ahead, fees=fees)
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

    # Estadísticas por signal (con P&L realista)
    print("RESULTS BY SIGNAL (con comisiones reales):")
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
        
        # P&L realista (neto después de comisiones)
        avg_pnl_neto = completed['pnl_neto'].mean()
        avg_pnl_pct = completed['pnl_pct'].mean()
        max_win_pnl = completed['pnl_neto'].max()
        max_loss_pnl = completed['pnl_neto'].min()
        total_comisiones = completed['comisiones'].sum()

        print(f"\n{signal:6s} | Trades: {len(subset):3d} | Completed: {total:3d}")
        print(f"       | Win Rate: {win_rate:6.1f}%")
        print(f"       | Avg P&L: {avg_pnl_neto:+8.2f} USDT ({avg_pnl_pct:+6.2f}%)")
        print(f"       | Best Trade: {max_win_pnl:+8.2f} USDT | Worst: {max_loss_pnl:+8.2f} USDT")
        print(f"       | Total Fees: {total_comisiones:8.2f} USDT")

    # Overall stats (P&L realista con comisiones)
    print("\n" + "=" * 70)
    print("OVERALL STATISTICS (P&L neto con comisiones):")
    print("-" * 70)

    completed = df_results[df_results['result'].isin(['CORRECT', 'WRONG'])]
    if len(completed) > 0:
        win_rate = (completed['win'].sum() / len(completed)) * 100
        
        # P&L neto (después de comisiones)
        avg_pnl_neto = completed['pnl_neto'].mean()
        avg_pnl_pct = completed['pnl_pct'].mean()
        total_pnl_neto = completed['pnl_neto'].sum()
        total_pnl_pct = (total_pnl_neto / (completed['entry_price'].sum())) * 100 if completed['entry_price'].sum() > 0 else 0
        
        max_win_pnl = completed['pnl_neto'].max()
        max_loss_pnl = completed['pnl_neto'].min()
        total_comisiones = completed['comisiones'].sum()
        
        # P&L bruto (para comparación)
        avg_pnl_bruto = completed['pnl_gross'].mean()
        total_pnl_bruto = completed['pnl_gross'].sum()

        print(f"Total Trades:          {len(completed):3d}")
        print(f"Win Rate:              {win_rate:6.1f}%")
        print(f"\nP&L Neto (después comisiones):")
        print(f"  Avg per trade:       {avg_pnl_neto:+8.2f} USDT ({avg_pnl_pct:+6.2f}%)")
        print(f"  Total:               {total_pnl_neto:+8.2f} USDT ({total_pnl_pct:+6.2f}%)")
        print(f"  Best Trade:          {max_win_pnl:+8.2f} USDT")
        print(f"  Worst Trade:         {max_loss_pnl:+8.2f} USDT")
        print(f"\nComisiones y Costos:")
        print(f"  Total Fees:          {total_comisiones:8.2f} USDT")
        print(f"  Avg Fee per trade:   {total_comisiones/len(completed):8.2f} USDT")
        print(f"\nP&L Bruto (sin comisiones, referencia):")
        print(f"  Total:               {total_pnl_bruto:+8.2f} USDT")
        print(f"  Avg per trade:       {avg_pnl_bruto:+8.2f} USDT")
        print(f"\nPending:               {len(df_results[df_results['result']=='PENDING']):3d}")

    print("\n" + "=" * 70 + "\n")
    logger.info("Backtest completed successfully")

    return True

def backtest_tf(tf, hours_ahead=1):
    """Backtest para un TF específico"""
    csv_file = DATA_DIR / f"eth_setup_log_{tf}.csv"
    
    if not csv_file.exists():
        print(f"⚠ No data for {tf}")
        return False
    
    # Cargar análisis
    try:
        analysis = pd.read_csv(csv_file)
        analysis['timestamp'] = pd.to_datetime(analysis['timestamp'])
    except Exception as e:
        print(f"❌ Error loading {tf}: {e}")
        return False
    
    price_data = load_price_data()
    
    if not price_data:
        print(f"❌ No price data for {tf}")
        return False
    
    # Evaluar predicciones
    results = []
    fees = get_fees()
    
    for _, row in analysis.iterrows():
        result = evaluate_prediction(row, price_data, hours_ahead=hours_ahead, fees=fees)
        if result is not None and result != 'PENDING':
            results.append(result)
    
    if not results:
        print(f"❌ No completed predictions for {tf}")
        return False
    
    # Mostrar resultados
    df_results = pd.DataFrame(results)
    
    # Guardar resultados
    backtest_csv = DATA_DIR / f"eth_backtest_results_{tf}.csv"
    df_results.to_csv(backtest_csv, index=False)
    
    print(f"Backtest Results for {tf.upper()} ({hours_ahead}h ahead):")
    print(f"Total trades: {len(df_results)}")
    
    completed = df_results[df_results['result'].isin(['CORRECT', 'WRONG'])]
    if len(completed) > 0:
        win_rate = (completed['win'].sum() / len(completed)) * 100
        avg_pnl = completed['pnl_neto'].mean()
        total_pnl = completed['pnl_neto'].sum()
        
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Avg P&L: {avg_pnl:+.2f} USDT")
        print(f"Total P&L: {total_pnl:+.2f} USDT")
        print(f"Results saved to {backtest_csv}\n")
        return True
    
    return False

def backtest_tf_dynamic(tf, hours_ahead=1, initial_capital=250):
    """Backtest con capital dinámico - cada trade usa todo el capital disponible"""
    csv_file = DATA_DIR / f"eth_setup_log_{tf}.csv"
    
    if not csv_file.exists():
        print(f"⚠ No data for {tf}")
        return False
    
    # Cargar análisis
    try:
        analysis = pd.read_csv(csv_file)
        analysis['timestamp'] = pd.to_datetime(analysis['timestamp'])
    except Exception as e:
        print(f"❌ Error loading {tf}: {e}")
        return False
    
    price_data = load_price_data()
    if not price_data:
        print(f"❌ No price data for {tf}")
        return False
    
    fees = get_fees()
    capital = initial_capital
    capital_high = initial_capital
    trades_executed = 0
    results = []
    
    for _, row in analysis.iterrows():
        result = evaluate_prediction(row, price_data, hours_ahead=hours_ahead, fees=fees)
        if result is None or result == 'PENDING':
            continue
        
        # Ejecutar trade con capital dinámico
        entry_price = result['entry_price']
        pnl_neto = result['pnl_neto']
        
        # Escalar P&L según capital real usado vs 1 lote (2500 USDT)
        capital_pct = capital / 2500.0
        pnl_escalado = pnl_neto * capital_pct
        
        # Actualizar capital
        capital += pnl_escalado
        trades_executed += 1
        
        # Track máximo capital
        if capital > capital_high:
            capital_high = capital
        
        # Registrar trade
        result['capital_antes'] = capital - pnl_escalado
        result['capital_despues'] = capital
        result['pnl_escalado'] = pnl_escalado
        results.append(result)
        
        # Detener si ruina
        if capital <= 0:
            print(f"💥 RUINA en trade {trades_executed}: capital = {capital:.2f} USDT")
            break
    
    if not results:
        print(f"❌ No completed predictions for {tf}")
        return False
    
    # Guardar resultados
    df_results = pd.DataFrame(results)
    backtest_csv = DATA_DIR / f"eth_backtest_results_{tf}_dynamic.csv"
    df_results.to_csv(backtest_csv, index=False)
    
    # Estadísticas
    completed = df_results[df_results['result'].isin(['CORRECT', 'WRONG'])]
    wins = completed['win'].sum() if len(completed) > 0 else 0
    
    print(f"\n{'='*70}")
    print(f"BACKTEST DINÁMICO: {tf.upper()} ({hours_ahead}h ahead)")
    print(f"Capital Inicial:   {initial_capital:>10.2f} USDT")
    print(f"Capital Final:     {capital:>10.2f} USDT ({(capital-initial_capital):+.2f} USDT)")
    print(f"ROI:               {((capital-initial_capital)/initial_capital*100):>10.1f}%")
    print(f"Capital Máximo:    {capital_high:>10.2f} USDT")
    print(f"Máx Reducción:     {(capital_high-capital):>10.2f} USDT")
    print(f"\nOperaciones:")
    print(f"Total Trades:      {trades_executed:>10d}")
    print(f"Completadas:       {len(completed):>10d}")
    if len(completed) > 0:
        win_rate = (wins / len(completed)) * 100
        print(f"Win Rate:          {win_rate:>10.1f}%")
        print(f"Ganadores:         {wins:>10d}")
        print(f"Perdedores:        {len(completed) - wins:>10d}")
        print(f"Avg P&L/Trade:     {completed['pnl_escalado'].mean():>+10.2f} USDT")
    print(f"\nResultados guardados: {backtest_csv}")
    print(f"{'='*70}\n")
    
    return capital > 0

def main():
    """CLI entry point for backtest"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='ETH Analyzer Backtest',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python3 backtest.py                    # Backtest all TFs
  python3 backtest.py --tf 5m            # Backtest 5m only
  python3 backtest.py --tf 15m --hours 3 # Backtest 15m, look 3 hours ahead
  python3 backtest.py --compare          # Compare all TFs side by side
        '''
    )
    
    parser.add_argument('--tf', type=str, choices=['5m', '15m', '1h', '4h'],
                       help='Specific timeframe to backtest')
    parser.add_argument('--hours', type=int, default=1, 
                       help='Look ahead hours (default: 1)')
    parser.add_argument('--compare', action='store_true',
                       help='Compare all timeframes')
    parser.add_argument('--dynamic', action='store_true',
                       help='Use dynamic capital sizing (250 USDT inicial)')
    parser.add_argument('--capital', type=float, default=250,
                       help='Initial capital for dynamic backtest (default: 250)')
    
    args = parser.parse_args()
    
    try:
        if args.dynamic:
            # Backtest dinámico
            if args.compare:
                print("\n" + "═" * 70)
                print(f"BACKTEST DINÁMICO - COMPARAR TFs (Capital: {args.capital} USDT)")
                print("═" * 70 + "\n")
                
                for tf in ['5m', '15m', '1h']:
                    backtest_tf_dynamic(tf, args.hours, args.capital)
            
            elif args.tf:
                backtest_tf_dynamic(args.tf, args.hours, args.capital)
            
            else:
                print("\n" + "═" * 70)
                print(f"BACKTEST DINÁMICO - TODOS LOS TFs (Capital: {args.capital} USDT)")
                print("═" * 70 + "\n")
                
                for tf in ['5m', '15m', '1h']:
                    backtest_tf_dynamic(tf, args.hours, args.capital)
        
        else:
            # Backtest normal (asume capital infinito)
            if args.compare:
                print("\n" + "═" * 70)
                print("COMPARING ALL TIMEFRAMES")
                print("═" * 70 + "\n")
                
                for tf in ['5m', '15m', '1h']:
                    backtest_tf(tf, args.hours)
            
            elif args.tf:
                backtest_tf(args.tf, args.hours)
            
            else:
                print("\n" + "═" * 70)
                print("BACKTEST - ALL AVAILABLE TIMEFRAMES")
                print("═" * 70 + "\n")
                
                for tf in ['5m', '15m', '1h']:
                    backtest_tf(tf, args.hours)
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
