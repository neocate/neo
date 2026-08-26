#!/usr/bin/env python3
"""
ETH Setup Analyzer - Cross-platform version with CLI args
Registra análisis en CSV para backtesting
Compatible con Windows/Linux/NAS

Usage:
  python3 analyzer.py                              # Single run
  python3 analyzer.py --loop 60                    # Loop every 60 seconds
  python3 analyzer.py --loop 60 --coin ETH         # Specify coin
  python3 analyzer.py --mercado futuros --loop 60  # Full params
  nohup python3 -u analyzer.py --loop 60 >/dev/null 2>&1 &
"""

import pandas as pd
import json
import logging
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PATHS - Cross-platform compatible
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCRIPT_DIR = Path(__file__).parent.parent
BASE_DIR = SCRIPT_DIR.parent  # neo/
VELAS_DIR = BASE_DIR / "velas" / "ETH"
LIBRO_DIR = BASE_DIR / "libro" / "datos"
NIVELES_DIR = BASE_DIR / "niveles" / "json"

LOG_DIR = SCRIPT_DIR / "log"
DATA_DIR = SCRIPT_DIR / "datos"
CONFIG_DIR = SCRIPT_DIR / "config"

# Crear directorios si no existen
for dir_path in [LOG_DIR, DATA_DIR, CONFIG_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# Archivos
ANALYSIS_CSV = DATA_DIR / "eth_setup_log.csv"
LOG_FILE = LOG_DIR / "analyzer.log"
CONFIG_FILE = CONFIG_DIR / "config.json"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_config() -> Dict:
    """Cargar configuración"""
    default_config = {
        "candles": {
            "1m": 50,
            "5m": 30,
            "15m": 25,
            "1h": 10
        },
        "thresholds": {
            "imbalance_long": 0.2,
            "imbalance_short": -0.2,
            "delta_long": 50,
            "delta_short": -50,
            "vol_ratio_high": 0.7,
            "vol_ratio_low": 0.4
        },
        "signals": {
            "min_conditions_long": 3,
            "min_conditions_short": 3,
            "strong_conditions": 4
        }
    }

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                loaded = json.load(f)
                default_config.update(loaded)
        except Exception as e:
            logger.warning(f"Error loading config: {e}, using defaults")

    return default_config

CONFIG = load_config()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CORE FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_candles(tf: str, limit: int = 30) -> Optional[pd.DataFrame]:
    """Cargar últimas velas de un timeframe"""
    file = VELAS_DIR / f"bitget_ETH_{tf}_futuros.csv"

    if not file.exists():
        logger.error(f"Candles file not found: {file}")
        return None

    try:
        df = pd.read_csv(file)
        return df.tail(limit)
    except Exception as e:
        logger.error(f"Error loading {tf} candles: {e}")
        return None

def calculate_indicators(df: Optional[pd.DataFrame]) -> Optional[Dict]:
    """Calcular indicadores técnicos"""
    if df is None or len(df) == 0:
        return None

    try:
        closes = df['close'].astype(float).values
        highs = df['high'].astype(float).values
        lows = df['low'].astype(float).values
        volumes = df['volumen'].astype(float).values

        sma = closes.mean()
        current_price = closes[-1]
        high_20 = highs.max()
        low_20 = lows.min()
        range_20 = high_20 - low_20
        avg_vol = volumes.mean()
        current_vol = volumes[-1]
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
        trend = "UP" if current_price > sma else "DOWN"

        # RSI
        diffs = pd.Series(closes).diff()
        gains = diffs[diffs > 0].sum()
        losses = -diffs[diffs < 0].sum()
        rs = gains / losses if losses > 0 else 0
        rsi = 100 - (100 / (1 + rs)) if rs > 0 else 50

        return {
            'close': float(current_price),
            'sma': float(sma),
            'high': float(high_20),
            'low': float(low_20),
            'range': float(range_20),
            'trend': trend,
            'vol_ratio': float(vol_ratio),
            'rsi': float(rsi)
        }
    except Exception as e:
        logger.error(f"Error calculating indicators: {e}")
        return None

def get_orderbook() -> Optional[Dict]:
    """Obtener estado del libro de órdenes"""
    # Detectar archivo más reciente (puede cambiar de fecha)
    libro_files = list(LIBRO_DIR.glob("libro_ETH_futuros_*.csv"))

    if not libro_files:
        logger.error("No orderbook files found")
        return None

    # Usar el más reciente
    file = sorted(libro_files, reverse=True)[0]

    try:
        df = pd.read_csv(file)
        last = df.iloc[-1]

        return {
            'timestamp': str(last['fecha_utc']),
            'imbalance': float(last['imbalance']),
            'oi': float(last['open_interest']),
            'funding_rate': float(last['funding_rate_pct']),
            'vol_buy': float(last['vol_buy']),
            'vol_sell': float(last['vol_sell']),
            'delta': float(last['delta_vol']),
            'cvd': float(last['cvd'])
        }
    except Exception as e:
        logger.error(f"Error loading orderbook: {e}")
        return None

def get_niveles() -> Optional[Dict]:
    """Obtener niveles de soporte/resistencia"""
    file = NIVELES_DIR / "nivel_ETH_1m_futuros_k5_toques3.json"

    if not file.exists():
        logger.warning("Niveles file not found")
        return None

    try:
        with open(file, 'r') as f:
            data = json.load(f)

        if 'niveles' not in data or len(data['niveles']) == 0:
            return None

        niveles = data['niveles']
        activos = [n for n in niveles if n.get('estado') != 'flip']

        if not activos:
            return None

        return {
            'count': len(activos),
            'strongest': sorted(activos, key=lambda x: x.get('fuerza', 0), reverse=True)[0] if activos else None
        }
    except Exception as e:
        logger.error(f"Error loading niveles: {e}")
        return None

def evaluate_setup(m1: Dict, m5: Dict, m15: Dict, m1h: Dict, ob: Dict, config: Dict) -> Optional[Dict]:
    """Evaluar setup según múltiples timeframes"""

    if not all([m1, m5, m15, m1h, ob]):
        return None

    thresholds = config['thresholds']
    signals_config = config['signals']

    conditions = {'long': 0, 'short': 0}

    # LONG conditions
    if m5['close'] > m5['sma']:
        conditions['long'] += 1
    if m15['trend'] == 'UP':
        conditions['long'] += 1
    if ob['imbalance'] > thresholds['imbalance_long']:
        conditions['long'] += 1
    if ob['delta'] > thresholds['delta_long']:
        conditions['long'] += 1
    if m5['vol_ratio'] > thresholds['vol_ratio_high']:
        conditions['long'] += 1

    # SHORT conditions
    if m5['close'] < m5['sma']:
        conditions['short'] += 1
    if m15['trend'] == 'DOWN':
        conditions['short'] += 1
    if ob['imbalance'] < thresholds['imbalance_short']:
        conditions['short'] += 1
    if ob['delta'] < thresholds['delta_short']:
        conditions['short'] += 1
    if m5['vol_ratio'] < thresholds['vol_ratio_low']:
        conditions['short'] += 1

    # Determine signal
    if conditions['long'] >= signals_config['min_conditions_long']:
        signal = 'LONG'
        confidence = conditions['long'] / 5.0
        strength = 'STRONG' if conditions['long'] >= signals_config['strong_conditions'] else 'MEDIUM'
    elif conditions['short'] >= signals_config['min_conditions_short']:
        signal = 'SHORT'
        confidence = conditions['short'] / 5.0
        strength = 'STRONG' if conditions['short'] >= signals_config['strong_conditions'] else 'MEDIUM'
    else:
        signal = 'WAIT'
        confidence = 0
        strength = 'WEAK'

    return {
        'signal': signal,
        'confidence': confidence,
        'strength': strength,
        'long_conds': conditions['long'],
        'short_conds': conditions['short']
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_analysis() -> bool:
    """Ejecutar análisis y registrar en CSV"""

    logger.info("=" * 60)
    logger.info("Starting analysis...")

    # Cargar datos
    m1 = calculate_indicators(load_candles('1m', CONFIG['candles']['1m']))
    m5 = calculate_indicators(load_candles('5m', CONFIG['candles']['5m']))
    m15 = calculate_indicators(load_candles('15m', CONFIG['candles']['15m']))
    m1h = calculate_indicators(load_candles('1h', CONFIG['candles']['1h']))
    ob = get_orderbook()
    niveles = get_niveles()

    # Evaluar
    setup = evaluate_setup(m1, m5, m15, m1h, ob, CONFIG)

    if not setup:
        logger.error("Could not evaluate setup")
        return False

    # Preparar registro
    record = {
        'timestamp': datetime.now().isoformat(),
        'signal': setup['signal'],
        'confidence': round(setup['confidence'], 2),
        'strength': setup['strength'],
        'price': round(m5['close'], 2),
        'sma_5m': round(m5['sma'], 2),
        'high_5m': round(m5['high'], 2),
        'low_5m': round(m5['low'], 2),
        'trend_5m': m5['trend'],
        'trend_15m': m15['trend'],
        'vol_ratio': round(m5['vol_ratio'], 2),
        'imbalance': round(ob['imbalance'], 3),
        'delta': round(ob['delta'], 1),
        'funding_rate': round(ob['funding_rate'], 4),
        'long_conds': setup['long_conds'],
        'short_conds': setup['short_conds'],
        'rsi_5m': round(m5['rsi'], 1)
    }

    # Guardar en CSV
    try:
        if ANALYSIS_CSV.exists():
            df = pd.read_csv(ANALYSIS_CSV)
            df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
        else:
            df = pd.DataFrame([record])

        df.to_csv(ANALYSIS_CSV, index=False)
        logger.info(f"✓ {setup['signal']:5s} | Conf: {setup['confidence']:.0%} | Price: {m5['close']:.2f} | Vol: {m5['vol_ratio']:.2f}x | Imb: {ob['imbalance']:+.2f}")

        return True
    except Exception as e:
        logger.error(f"Error saving analysis: {e}")
        return False

def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description='ETH Setup Analyzer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python3 analyzer.py                              # Single run
  python3 analyzer.py --loop 60                    # Loop every 60 seconds
  nohup python3 -u analyzer.py --loop 60 >/dev/null 2>&1 &
        '''
    )

    parser.add_argument('--coin', type=str, default='ETH', help='Coin to analyze (default: ETH)')
    parser.add_argument('--mercado', type=str, default='futuros', help='Market type (default: futuros)')
    parser.add_argument('--loop', type=int, default=None, help='Loop interval in seconds (default: single run)')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info(f"Starting analyzer: {args.coin} {args.mercado}")

    if args.loop:
        logger.info(f"Loop mode: {args.loop} seconds interval")
        run_count = 0
        try:
            while True:
                run_count += 1
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"\n[{timestamp}] Run #{run_count}")
                print("=" * 60)

                success = run_analysis()

                if args.loop:
                    next_run = (datetime.now() + __import__('datetime').timedelta(seconds=args.loop)).strftime('%H:%M:%S')
                    logger.info(f"Next run: {next_run}")
                    time.sleep(args.loop)
        except KeyboardInterrupt:
            logger.info("Analyzer stopped by user")
            sys.exit(0)
    else:
        # Single run
        success = run_analysis()
        sys.exit(0 if success else 1)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
