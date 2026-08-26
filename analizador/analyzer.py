#!/usr/bin/env python3
"""
ETH Setup Analyzer - indicadores + niveles + libro combinados correctamente
Registra análisis en CSV para backtesting

Usage:
  python3 analyzer.py                                    # Single run, TF 5m
  python3 analyzer.py --tf 15m --loop 60                 # Loop 15m every 60s
  python3 analyzer.py --tf 5m --niveles-tf 1h             # niveles de otro TF
  nohup python3 -u analyzer.py --loop 60 >/dev/null 2>&1 &
"""

import csv
import os
import sys
import json
import time
import logging
import argparse
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent  # neo/
LIBRO_DIR = BASE_DIR / "libro" / "datos"

LOG_DIR = SCRIPT_DIR / "log"
DATA_DIR = SCRIPT_DIR / "datos"
CONFIG_FILE = SCRIPT_DIR / "params.json"

for dir_path in [LOG_DIR, DATA_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR / "velas"))
sys.path.insert(0, str(BASE_DIR / "niveles"))
sys.path.insert(0, str(BASE_DIR))
import velas_bit
import niveles
import sincronia
from indicadores import indicadores

CURRENT_TF = "5m"


def get_analysis_csv(tf=None):
    tf = tf or CURRENT_TF
    return DATA_DIR / f"eth_setup_log_{tf}.csv"


def get_log_file(tf=None):
    tf = tf or CURRENT_TF
    return LOG_DIR / f"analyzer_{tf}.log"


ANALYSIS_CSV = get_analysis_csv()
LOG_FILE = get_log_file()

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
    default_config = {
        "candles": {
            "1m": 50,
            "5m": 30,
            "15m": 25,
            "1h": 10,
            "4h": 20,
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
        },
        "niveles": {
            "ruptura_max_dias": 1.0,
            "proximidad_tolerancias": 2.0
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


def get_tf_config(tf: str) -> Dict:
    """Config del TF especifico (relectura en tiempo real para cambios en caliente)"""
    config = load_config()
    if 'by_tf' in config and tf in config['by_tf']:
        return config['by_tf'][tf]
    return {
        'thresholds': config.get('thresholds', {}),
        'signals': config.get('signals', {})
    }


_config_hashes = {}


def get_config_hash(config_dict: Dict) -> str:
    config_json = json.dumps(config_dict, sort_keys=True)
    return hashlib.sha256(config_json.encode()).hexdigest()[:12]


def log_config_change(tf: str, config: Dict):
    log_path = DATA_DIR / "config_changes.log"
    with open(log_path, 'a') as f:
        timestamp = datetime.now().isoformat()
        config_json = json.dumps(config, indent=2)
        f.write(f"\n{'='*60}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"TF: {tf}\n")
        f.write(f"Config:\n{config_json}\n")


def check_config_changes(tf: str, config: Dict):
    new_hash = get_config_hash(config)
    old_hash = _config_hashes.get(tf)
    if old_hash != new_hash:
        logger.info(f"Config change detected for {tf}: {old_hash or 'NEW'} -> {new_hash}")
        log_config_change(tf, config)
        _config_hashes[tf] = new_hash

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VELAS + INDICADORES (del propio TF, via velas_bit para lectura eficiente)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cargar_indicadores_tf(coin: str, mercado: str, tf: str, n: int) -> Optional[Dict]:
    velas = velas_bit.ultimas_velas(coin, tf, mercado, n)
    if len(velas) < 2:
        logger.error(f"Velas insuficientes de {coin} {tf} {mercado}: {len(velas)}")
        return None

    closes = [v['close'] for v in velas]
    highs = [v['high'] for v in velas]
    lows = [v['low'] for v in velas]
    volumes = [v['vol'] for v in velas]

    sma = indicadores.sma(closes, periodo=len(closes))[-1]
    rsi_periodo = min(14, len(closes) - 1)
    rsi = indicadores.ultimo(indicadores.rsi(closes, periodo=rsi_periodo))

    current_price = closes[-1]
    high_n = max(highs)
    low_n = min(lows)
    avg_vol = sum(volumes) / len(volumes)
    current_vol = volumes[-1]

    return {
        'close': float(current_price),
        'sma': float(sma),
        'high': float(high_n),
        'low': float(low_n),
        'range': float(high_n - low_n),
        'trend': "UP" if current_price > sma else "DOWN",
        'vol_ratio': float(current_vol / avg_vol) if avg_vol > 0 else 0.0,
        'rsi': float(rsi),
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LIBRO (solo la ultima fila, sin cargar el CSV del dia entero)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _leer_ultima_fila_csv(ruta: Path, n_bytes: int = 65536) -> Optional[Dict]:
    with open(ruta, 'rb') as f:
        cabecera_bytes = f.readline()
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        leidos = min(n_bytes, tam)
        f.seek(max(0, tam - leidos))
        cola = f.read()

    cabecera = next(csv.reader([cabecera_bytes.decode('utf-8', 'replace')]))
    lineas = cola.decode('utf-8', 'replace').split('\n')
    if leidos < tam:
        lineas = lineas[1:]
    lineas = [l for l in lineas if l.strip()]
    if not lineas:
        return None

    fila = next(csv.reader([lineas[-1]]))
    return dict(zip(cabecera, fila))


def cargar_orderbook(coin: str, mercado: str) -> Optional[Dict]:
    patron = f"libro_{coin.upper()}_{mercado}_*.csv"
    archivos = sorted(LIBRO_DIR.glob(patron), reverse=True)
    if not archivos:
        logger.error(f"No hay ficheros de libro para {coin} {mercado} en {LIBRO_DIR}")
        return None

    ruta = archivos[0]
    try:
        fila = _leer_ultima_fila_csv(ruta)
    except (OSError, csv.Error, StopIteration) as e:
        logger.error(f"Error leyendo libro {ruta.name}: {e}")
        return None
    if fila is None:
        logger.error(f"Libro vacio: {ruta.name}")
        return None

    def opcional(nombre):
        try:
            return float(fila[nombre])
        except (ValueError, KeyError, TypeError):
            return None

    try:
        return {
            'timestamp': fila.get('fecha_utc', ''),
            'imbalance': float(fila['imbalance']),
            'delta': float(fila['delta_vol']),
            'vol_buy': float(fila['vol_buy']),
            'vol_sell': float(fila['vol_sell']),
            'cvd': float(fila['cvd']),
            'oi': opcional('open_interest'),
            'funding_rate': opcional('funding_rate_pct'),
        }
    except (ValueError, KeyError) as e:
        logger.error(f"Libro {ruta.name}: campo obligatorio invalido ({e})")
        return None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NIVELES (ruptura + rebote, usando 'vigente' y 'dist_pct' del JSON)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cargar_niveles(coin: str, niveles_tf: str, mercado: str) -> Optional[Dict]:
    datos = niveles.leer_ultimo(coin, niveles_tf, mercado)
    if datos is None:
        logger.warning(f"Sin JSON de niveles para {coin} {niveles_tf} {mercado}")
        return None

    ts = datos.get('ts_ultima_vela')
    if ts is None or not sincronia.es_reciente(ts, niveles_tf):
        logger.warning(f"Niveles de {niveles_tf} desactualizados, se ignoran "
                        f"(ruptura/rebote quedan en False)")
        return None

    return datos


def evaluar_ruptura(niveles_datos: Optional[Dict], max_dias: float) -> Dict[str, bool]:
    resultado = {'long': False, 'short': False}
    if not niveles_datos:
        return resultado

    for niv in niveles_datos.get('niveles', []):
        dias = niv.get('dias_desde_rotura')
        if dias is None or dias > max_dias:
            continue
        if niv['tipo'] == 'techo' and niv.get('vigente') is False and niv.get('dist_pct', 0) < 0:
            resultado['long'] = True
        elif niv['tipo'] == 'suelo' and niv.get('vigente') is False and niv.get('dist_pct', 0) > 0:
            resultado['short'] = True

    return resultado


def evaluar_rebote(niveles_datos: Optional[Dict], proximidad_tolerancias: float) -> Dict[str, bool]:
    resultado = {'long': False, 'short': False}
    if not niveles_datos:
        return resultado

    precio_actual = niveles_datos.get('precio_actual')
    tolerancia = niveles_datos.get('tolerancia_actual')
    if precio_actual is None or tolerancia is None:
        return resultado

    margen = tolerancia * proximidad_tolerancias
    for niv in niveles_datos.get('niveles', []):
        if not niv.get('vigente'):
            continue
        if niv['tipo'] == 'suelo' and 0 <= (precio_actual - niv['precio']) <= margen:
            resultado['long'] = True
        elif niv['tipo'] == 'techo' and 0 <= (niv['precio'] - precio_actual) <= margen:
            resultado['short'] = True

    return resultado

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SETUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def evaluate_setup(principal: Dict, confirmacion: Dict, ob: Dict,
                    ruptura: Dict, rebote: Dict, config: Dict) -> Optional[Dict]:
    if not all([principal, confirmacion, ob]):
        return None

    thresholds = config['thresholds']
    signals_config = config['signals']

    condiciones_long = [
        principal['close'] > principal['sma'],
        confirmacion['trend'] == 'UP',
        ob['imbalance'] > thresholds['imbalance_long'],
        ob['delta'] > thresholds['delta_long'],
        principal['vol_ratio'] > thresholds['vol_ratio_high'],
        ruptura['long'],
        rebote['long'],
    ]
    condiciones_short = [
        principal['close'] < principal['sma'],
        confirmacion['trend'] == 'DOWN',
        ob['imbalance'] < thresholds['imbalance_short'],
        ob['delta'] < thresholds['delta_short'],
        principal['vol_ratio'] < thresholds['vol_ratio_low'],
        ruptura['short'],
        rebote['short'],
    ]

    total = len(condiciones_long)
    n_long = sum(condiciones_long)
    n_short = sum(condiciones_short)

    if n_long >= signals_config['min_conditions_long']:
        signal = 'LONG'
        confidence = n_long / total
        strength = 'STRONG' if n_long >= signals_config['strong_conditions'] else 'MEDIUM'
    elif n_short >= signals_config['min_conditions_short']:
        signal = 'SHORT'
        confidence = n_short / total
        strength = 'STRONG' if n_short >= signals_config['strong_conditions'] else 'MEDIUM'
    else:
        signal = 'WAIT'
        confidence = 0
        strength = 'WEAK'

    return {
        'signal': signal,
        'confidence': confidence,
        'strength': strength,
        'long_conds': n_long,
        'short_conds': n_short,
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TF_ORDER = ['1m', '5m', '15m', '1h', '4h']


def run_analysis(coin: str, mercado: str, tf: str, niveles_tf: str = None) -> bool:
    niveles_tf = niveles_tf or tf

    logger.info("=" * 60)
    logger.info(f"Starting analysis for TF: {tf} (niveles: {niveles_tf})")

    config = load_config()
    tf_config = get_tf_config(tf)
    check_config_changes(tf, tf_config)
    niveles_cfg = config['niveles']

    tf_index = TF_ORDER.index(tf)
    tf_mayor1 = TF_ORDER[min(tf_index + 1, len(TF_ORDER) - 1)]

    principal = cargar_indicadores_tf(coin, mercado, tf, config['candles'].get(tf, 30))
    confirmacion = cargar_indicadores_tf(coin, mercado, tf_mayor1, config['candles'].get(tf_mayor1, 20))
    ob = cargar_orderbook(coin, mercado)

    niveles_datos = cargar_niveles(coin, niveles_tf, mercado)
    ruptura = evaluar_ruptura(niveles_datos, niveles_cfg['ruptura_max_dias'])
    rebote = evaluar_rebote(niveles_datos, niveles_cfg['proximidad_tolerancias'])

    setup = evaluate_setup(principal, confirmacion, ob, ruptura, rebote, tf_config)

    if not setup:
        logger.error("Could not evaluate setup (faltan velas u orderbook)")
        return False

    record = {
        'timestamp': datetime.now().isoformat(),
        'tf': tf,
        'signal': setup['signal'],
        'confidence': round(setup['confidence'], 2),
        'strength': setup['strength'],
        'price': round(principal['close'], 2),
        'sma': round(principal['sma'], 2),
        'high': round(principal['high'], 2),
        'low': round(principal['low'], 2),
        'trend': principal['trend'],
        'trend_mayor': confirmacion['trend'],
        'vol_ratio': round(principal['vol_ratio'], 2),
        'imbalance': round(ob['imbalance'], 3),
        'delta': round(ob['delta'], 1),
        'funding_rate': round(ob['funding_rate'], 4) if ob['funding_rate'] is not None else '',
        'long_conds': setup['long_conds'],
        'short_conds': setup['short_conds'],
        'rsi': round(principal['rsi'], 1),
        'niveles_tf': niveles_tf,
        'ruptura_long': ruptura['long'],
        'ruptura_short': ruptura['short'],
        'rebote_long': rebote['long'],
        'rebote_short': rebote['short'],
    }

    try:
        analysis_csv = get_analysis_csv(tf)
        if analysis_csv.exists():
            df = pd.read_csv(analysis_csv)
            df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
        else:
            df = pd.DataFrame([record])
        df.to_csv(analysis_csv, index=False)

        logger.info(f"{setup['signal']:5s} | Conf: {setup['confidence']:.0%} | "
                    f"Price: {principal['close']:.2f} | Vol: {principal['vol_ratio']:.2f}x | "
                    f"Imb: {ob['imbalance']:+.2f} | L{setup['long_conds']}/S{setup['short_conds']} "
                    f"| ruptura(L={ruptura['long']},S={ruptura['short']}) "
                    f"rebote(L={rebote['long']},S={rebote['short']}) [niveles {niveles_tf}]")
        return True
    except Exception as e:
        logger.error(f"Error saving analysis: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='ETH Setup Analyzer - indicadores + niveles + libro',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python3 analyzer.py                                   # Single run, TF 5m
  python3 analyzer.py --tf 15m --loop 60                # Loop 15m every 60s
  python3 analyzer.py --tf 5m --niveles-tf 1h --loop 60 # niveles de otro TF

  # Run multiple TF in parallel:
  nohup python3 -u analyzer.py --tf 5m --loop 60 >/dev/null 2>&1 &
  nohup python3 -u analyzer.py --tf 15m --loop 60 >/dev/null 2>&1 &
  nohup python3 -u analyzer.py --tf 1h --loop 120 >/dev/null 2>&1 &
        '''
    )

    parser.add_argument('--tf', type=str, default='5m', choices=TF_ORDER,
                       help='Timeframe to analyze (default: 5m)')
    parser.add_argument('--niveles-tf', type=str, default=None, choices=niveles.TIMEFRAMES,
                       help='TF de niveles a usar (default: igual que --tf)')
    parser.add_argument('--coin', type=str, default='ETH', help='Coin to analyze (default: ETH)')
    parser.add_argument('--mercado', type=str, default='futuros', help='Market type (default: futuros)')
    parser.add_argument('--loop', type=int, default=None, help='Loop interval in seconds (default: single run)')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')

    args = parser.parse_args()
    niveles_tf = args.niveles_tf or args.tf

    global CURRENT_TF, ANALYSIS_CSV, LOG_FILE
    CURRENT_TF = args.tf
    ANALYSIS_CSV = get_analysis_csv(args.tf)
    LOG_FILE = get_log_file(args.tf)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout)
        ]
    )

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info(f"Starting analyzer: {args.coin} {args.mercado} | TF: {args.tf} | niveles: {niveles_tf}")

    if args.loop:
        logger.info(f"Loop mode: {args.loop} seconds interval")
        run_count = 0
        try:
            while True:
                run_count += 1
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"\n[{timestamp}] Run #{run_count}")
                print("=" * 60)

                run_analysis(args.coin, args.mercado, args.tf, niveles_tf)

                next_run = (datetime.now() + timedelta(seconds=args.loop)).strftime('%H:%M:%S')
                logger.info(f"Next run: {next_run}")
                time.sleep(args.loop)
        except KeyboardInterrupt:
            logger.info("Analyzer stopped by user")
            sys.exit(0)
    else:
        success = run_analysis(args.coin, args.mercado, args.tf, niveles_tf)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
