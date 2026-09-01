#!/usr/bin/env python3
"""
analyzer.py - ETH Setup Analyzer: producción (en vivo) + modo histórico (replay)

Modos:
  Producción (en vivo):
    python analyzer.py --tf 5m --loop 60
    python analyzer.py --tf 5m --loop 60 --niveles-tf 1h

  Histórico (replay):
    python analyzer.py --tf 5m --replay-desde 2026-08-26 --replay-hasta 2026-08-27
    python analyzer.py --tf 5m --replay-desde 2026-08-26 --replay-hasta 2026-08-27 --intervalo 15

  Detección de anomalías en histórico:
    python analyzer.py --tf 5m --replay-desde 2026-08-26 --replay-hasta 2026-08-27 --verbose
"""

import csv
import os
import sys
import time
import json
import logging
from logging.handlers import RotatingFileHandler
import argparse
import bisect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, List, Tuple

import pandas as pd

SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent  # neo/
LIBRO_DIR = BASE_DIR / "libro" / "datos"
FLUJO_DIR = LIBRO_DIR / "flujo"
VELAS_DIR = BASE_DIR / "velas"

LOG_DIR = SCRIPT_DIR / "log"
DATA_DIR = SCRIPT_DIR / "datos"
CONFIG_FILE = SCRIPT_DIR / "params.json"

for dir_path in [LOG_DIR, DATA_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR / "velas"))
sys.path.insert(0, str(BASE_DIR / "niveles"))
sys.path.insert(0, str(BASE_DIR))

import velas_bit
from persistencia import leer_ultimo
from io_velas import TIMEFRAMES, validar_coin, _parsear as _parsear_velas, _tf_a_ms
import sincronia
from indicadores import indicadores
from algoritmo_niveles import calcular as calcular_niveles
from niveles import _leer_params as _leer_params_niveles, DIR_NIVELES as NIVELES_SCRIPT_DIR, VELAS_OBJETIVO

from _analyzer_core import (
    TF_ORDER, load_config, get_tf_config, get_config_hash,
    evaluate_setup, evaluar_ruptura, evaluar_rebote,
    detectar_spike_velas, detectar_gap_libro, validar_indicador, validar_libro
)

CURRENT_TF = "5m"
_config_hashes = {}


def get_analysis_csv(tf=None):
    tf = tf or CURRENT_TF
    return DATA_DIR / f"eth_setup_log_{tf}.csv"


def get_log_file(tf=None):
    tf = tf or CURRENT_TF
    return LOG_DIR / f"analyzer_{tf}.log"


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        # Rotacion: en modo loop a 60s este log crecia sin limite (47.435
        # lineas en 4 dias). 5 MB x 3 backups acota el disco sin perder el
        # historial reciente.
        RotatingFileHandler(get_log_file(), maxBytes=5 * 1024 * 1024,
                            backupCount=3, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MODO PRODUCCIÓN: LECTURA EN VIVO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cargar_indicadores_tf_vivo(coin: str, mercado: str, tf: str, n: int) -> Optional[Dict]:
    """Lee las N últimas velas via velas_bit (optimizado para producción)."""
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


def _leer_ultima_fila_csv(ruta: Path, n_bytes: int = 65536) -> Optional[Dict]:
    """Lee solo la última fila del CSV (eficiente, O(1) en tamaño de archivo)."""
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


def _libro_es_reciente(fecha_utc_str: str, ruta_nombre: str, max_edad_seg: float) -> bool:
    """Verifica que el snapshot del libro no esté desactualizado."""
    try:
        ts = datetime.strptime(fecha_utc_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        logger.error(f"Libro {ruta_nombre}: fecha_utc invalida ({fecha_utc_str!r})")
        return False

    edad_seg = (datetime.now(timezone.utc) - ts).total_seconds()
    if edad_seg > max_edad_seg:
        logger.warning(f"Libro {ruta_nombre} desactualizado ({edad_seg:.0f}s > "
                        f"{max_edad_seg:.0f}s), se ignora - el grabador puede estar parado")
        return False
    return True


def _ultima_fila_fresca(directorio, patron: str, max_edad_seg: float,
                        etiqueta: str) -> Optional[Dict]:
    """Ultima fila del CSV mas reciente que case con el patron, si es fresca."""
    archivos = sorted(directorio.glob(patron), reverse=True)
    if not archivos:
        logger.error(f"No hay ficheros de {etiqueta} en {directorio} ({patron})")
        return None

    ruta = archivos[0]
    try:
        fila = _leer_ultima_fila_csv(ruta)
    except (OSError, csv.Error, StopIteration) as e:
        logger.error(f"Error leyendo {etiqueta} {ruta.name}: {e}")
        return None
    if fila is None:
        logger.error(f"{etiqueta.capitalize()} vacio: {ruta.name}")
        return None
    if not _libro_es_reciente(fila.get('fecha_utc', ''), ruta.name, max_edad_seg):
        return None
    return fila


def _opcional(fila: Dict, nombre: str) -> Optional[float]:
    try:
        return float(fila[nombre])
    except (ValueError, KeyError, TypeError):
        return None


def cargar_orderbook_vivo(coin: str, mercado: str, max_edad_seg: float,
                          flujo_max_edad_seg: float) -> Optional[Dict]:
    """Estado del mercado en vivo, de DOS fuentes con relojes distintos:

      libro_*.csv  (libro.py, ~900s) -> imbalance, imbalance_amplio, OI, funding
      flujo_*.csv  (flujo.py,  ~60s) -> delta, vol_buy, vol_sell, cvd

    Se separaron porque solo el libro y el OI son irrecuperables: el tape se
    puede repedir 7 dias y por eso lo graba flujo.py, mucho mas fino. Cada uno
    lleva su propio umbral de frescura; un flujo de hace 25 minutos no es
    'del momento' aunque el libro de esa misma edad si valga."""
    fila = _ultima_fila_fresca(LIBRO_DIR, f"libro_{coin.upper()}_{mercado}_*.csv",
                               max_edad_seg, "libro")
    if fila is None:
        return None

    flujo = _ultima_fila_fresca(FLUJO_DIR, f"flujo_{coin.upper()}_{mercado}_*.csv",
                                flujo_max_edad_seg, "flujo")
    if flujo is None:
        return None

    try:
        return {
            'timestamp': fila.get('fecha_utc', ''),
            'timestamp_flujo': flujo.get('fecha_utc', ''),
            'imbalance': float(fila['imbalance']),
            'imbalance_amplio': _opcional(fila, 'imbalance_amplio'),
            'delta': float(flujo['delta_vol']),
            'vol_buy': float(flujo['vol_buy']),
            'vol_sell': float(flujo['vol_sell']),
            'cvd': float(flujo['cvd']),
            'oi': _opcional(fila, 'open_interest'),
            'funding_rate': _opcional(fila, 'funding_rate_pct'),
        }
    except (ValueError, KeyError) as e:
        logger.error(f"Libro/flujo {coin}: campo obligatorio invalido ({e})")
        return None


def cargar_niveles_vivo(coin: str, niveles_tf: str, mercado: str) -> Optional[Dict]:
    """Lee niveles desde JSON snapshot más reciente (modo producción)."""
    datos = leer_ultimo(coin, niveles_tf, mercado)
    if datos is None:
        logger.warning(f"Sin JSON de niveles para {coin} {niveles_tf} {mercado}")
        return None

    ts = datos.get('ts_ultima_vela')
    if ts is None or not sincronia.es_reciente(ts, niveles_tf):
        logger.warning(f"Niveles de {niveles_tf} desactualizados, se ignoran "
                        f"(ruptura/rebote quedan en False)")
        return None

    return datos


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MODO HISTÓRICO: LECTURA CON REPLAY TEMPORAL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_velas_cache_hist: Dict[Tuple[str, str, str], Tuple[List[int], List[list]]] = {}
_niveles_cfg_cache_hist: Dict[Tuple[str, str], Dict] = {}
_niveles_calc_cache_hist: Dict[str, Tuple[int, list, dict]] = {}
_libro_cache_hist: Dict[Tuple[str, str], pd.DataFrame] = {}


def _velas_para_hist(coin: str, tf: str, mercado: str) -> Tuple[List[int], List[list]]:
    """Carga CSV de velas completo (una sola vez, cacheado)."""
    key = (coin.upper(), tf, mercado)
    if key not in _velas_cache_hist:
        ruta = VELAS_DIR / coin.upper() / f"bitget_{coin.upper()}_{tf}_{mercado}.csv"
        if not ruta.exists():
            raise FileNotFoundError(f"No hay velas de {coin} {tf} {mercado}: {ruta}")
        with open(ruta, newline='') as f:
            velas = _parsear_velas(f)
        _velas_cache_hist[key] = ([v[0] for v in velas], velas)
    return _velas_cache_hist[key]


def cargar_indicadores_tf_hist(coin: str, mercado: str, tf: str, n: int, as_of_ms: int) -> Optional[Dict]:
    """Lee velas hasta 'as_of_ms' sin look-ahead (modo histórico)."""
    ts_list, velas = _velas_para_hist(coin, tf, mercado)
    corte_ms = as_of_ms - _tf_a_ms(tf)  # Sin look-ahead
    idx_fin = bisect.bisect_right(ts_list, corte_ms)
    ventana = velas[max(0, idx_fin - n):idx_fin]
    if len(ventana) < 2:
        return None

    closes = [v[4] for v in ventana]
    highs = [v[2] for v in ventana]
    lows = [v[3] for v in ventana]
    volumes = [v[5] for v in ventana]

    sma = indicadores.sma(closes, periodo=len(closes))[-1]
    rsi_periodo = min(14, len(closes) - 1)
    rsi = indicadores.ultimo(indicadores.rsi(closes, periodo=rsi_periodo))

    current_price = closes[-1]
    high_n = max(highs)
    low_n = min(lows)
    avg_vol = sum(volumes) / len(volumes)
    current_vol = volumes[-1]

    return {
        'close': float(current_price), 'sma': float(sma),
        'high': float(high_n), 'low': float(low_n), 'range': float(high_n - low_n),
        'trend': "UP" if current_price > sma else "DOWN",
        'vol_ratio': float(current_vol / avg_vol) if avg_vol > 0 else 0.0,
        'rsi': float(rsi),
    }


def _concat_hist(directorio, patron: str, etiqueta: str) -> pd.DataFrame:
    archivos = sorted(directorio.glob(patron))
    if not archivos:
        raise FileNotFoundError(f"No hay CSV de {etiqueta} en {directorio} ({patron})")
    df = pd.concat([pd.read_csv(f) for f in archivos], ignore_index=True)
    df['fecha_utc'] = pd.to_datetime(df['fecha_utc'], utc=True)
    return df.sort_values('fecha_utc').reset_index(drop=True)


def cargar_libro_completo_hist(coin: str, mercado: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Historico de libro y de flujo (cacheado). Son dos series con cadencias
    distintas (~900s y ~60s), asi que se guardan separadas y se juntan por
    as-of join en cargar_orderbook_hist, no por indice."""
    key = (coin.upper(), mercado)
    if key not in _libro_cache_hist:
        libro = _concat_hist(LIBRO_DIR, f"libro_{coin.upper()}_{mercado}_*.csv", "libro")
        flujo = _concat_hist(FLUJO_DIR, f"flujo_{coin.upper()}_{mercado}_*.csv", "flujo")
        _libro_cache_hist[key] = (libro, flujo)
    return _libro_cache_hist[key]


def _as_of(df: pd.DataFrame, as_of: pd.Timestamp, max_edad_seg: float):
    """Ultima fila con fecha <= as_of, si no es mas vieja que max_edad_seg."""
    idx = df['fecha_utc'].searchsorted(as_of, side='right') - 1
    if idx < 0:
        return None
    fila = df.iloc[idx]
    if (as_of - fila['fecha_utc']).total_seconds() > max_edad_seg:
        return None
    return fila


def cargar_orderbook_hist(libro: pd.DataFrame, flujo: pd.DataFrame, as_of: pd.Timestamp,
                          max_edad_seg: float, flujo_max_edad_seg: float) -> Optional[Dict]:
    """Estado en 'as_of', juntando libro y flujo cada uno con su propio umbral.

    Antes el delta salia del CSV de libro, que se grababa truncado (17-20% del
    tape y el signo cambiado en 4 de 7 dias medidos). Ahora sale de flujo.py,
    que cubre la ventana entera. Los backtests anteriores a este cambio se
    corrieron sobre ese delta corrompido."""
    fila = _as_of(libro, as_of, max_edad_seg)
    if fila is None:
        return None
    fila_flujo = _as_of(flujo, as_of, flujo_max_edad_seg)
    if fila_flujo is None:
        return None

    def val(f, nombre):
        return float(f[nombre]) if pd.notna(f.get(nombre)) else None

    try:
        return {
            'imbalance': float(fila['imbalance']),
            'imbalance_amplio': val(fila, 'imbalance_amplio'),
            'delta': float(fila_flujo['delta_vol']),
            'vol_buy': float(fila_flujo['vol_buy']),
            'vol_sell': float(fila_flujo['vol_sell']),
            'cvd': float(fila_flujo['cvd']),
            'oi': val(fila, 'open_interest'),
            'funding_rate': val(fila, 'funding_rate_pct'),
        }
    except (ValueError, KeyError):
        return None


def _cfg_niveles_hist(coin: str, tf: str) -> Dict:
    """Carga config de niveles (cacheado)."""
    key = (coin.lower(), tf)
    if key not in _niveles_cfg_cache_hist:
        params_file = str(NIVELES_SCRIPT_DIR / f"params_{coin.lower()}_{tf}.json")
        cfg, _ = _leer_params_niveles(params_file)
        _niveles_cfg_cache_hist[key] = cfg
    return _niveles_cfg_cache_hist[key]


def cargar_niveles_hist(coin: str, mercado: str, tf: str, as_of_ms: int) -> Optional[Dict]:
    """Recalcula niveles hasta 'as_of_ms' (modo histórico)."""
    cfg = dict(_cfg_niveles_hist(coin, tf))
    if cfg['desde_dias'] is None:
        cfg['desde_dias'] = VELAS_OBJETIVO * _tf_a_ms(tf) / 86400000.0

    ts_list, velas = _velas_para_hist(coin, tf, mercado)
    corte_disponible_ms = as_of_ms - _tf_a_ms(tf)
    idx_fin = bisect.bisect_right(ts_list, corte_disponible_ms)
    corte_ventana_ms = as_of_ms - int(cfg['desde_dias'] * 86400000)
    idx_ini = bisect.bisect_left(ts_list, corte_ventana_ms)
    ventana = velas[idx_ini:idx_fin]
    if len(ventana) < 50:
        return None

    ultima_vela_ts = ventana[-1][0]
    cacheado = _niveles_calc_cache_hist.get(tf)
    if cacheado is not None and cacheado[0] == ultima_vela_ts:
        niveles, meta = cacheado[1], cacheado[2]
    else:
        niveles, meta = calcular_niveles(ventana, cfg)
        _niveles_calc_cache_hist[tf] = (ultima_vela_ts, niveles, meta)

    return {
        'niveles': niveles,
        'precio_actual': meta['precio_actual'],
        'tolerancia_actual': meta['tolerancia_actual'],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ANÁLISIS UNIFICADO (ambos modos)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_config_changes(tf: str, config: Dict):
    """Detecta cambios en config (solo en modo vivo)."""
    new_hash = get_config_hash(config)
    old_hash = _config_hashes.get(tf)
    if old_hash != new_hash:
        logger.info(f"Config change detected for {tf}: {old_hash or 'NEW'} -> {new_hash}")
        log_path = DATA_DIR / "config_changes.log"
        with open(log_path, 'a') as f:
            timestamp = datetime.now(timezone.utc).isoformat()
            config_json = json.dumps(config, indent=2)
            f.write(f"\n{'='*60}\n")
            f.write(f"Timestamp: {timestamp}\n")
            f.write(f"TF: {tf}\n")
            f.write(f"Config:\n{config_json}\n")
        _config_hashes[tf] = new_hash


def run_analysis_vivo(coin: str, mercado: str, tf: str, niveles_tf: str = None) -> bool:
    """Análisis en modo producción (tiempo real)."""
    niveles_tf = niveles_tf or tf
    logger.info("=" * 60)
    logger.info(f"Starting live analysis for TF: {tf} (niveles: {niveles_tf})")

    config = load_config(CONFIG_FILE)
    tf_config = get_tf_config(tf, config)
    check_config_changes(tf, tf_config)
    niveles_cfg = config['niveles']

    tf_index = TF_ORDER.index(tf)
    tf_mayor1 = TF_ORDER[min(tf_index + 1, len(TF_ORDER) - 1)]

    principal = cargar_indicadores_tf_vivo(coin, mercado, tf, config['candles'].get(tf, 30))
    confirmacion = cargar_indicadores_tf_vivo(coin, mercado, tf_mayor1, config['candles'].get(tf_mayor1, 20))
    ob = cargar_orderbook_vivo(coin, mercado, config['libro_max_edad_seg'],
                               config.get('flujo_max_edad_seg', 150))
    niveles_datos = cargar_niveles_vivo(coin, niveles_tf, mercado)

    # Validar datos
    valido_principal, msg_principal = validar_indicador(principal, 'Principal')
    valido_confirmacion, msg_confirmacion = validar_indicador(confirmacion, 'Confirmacion')
    valido_libro, msg_libro = validar_libro(ob, 'Libro')

    if not all([valido_principal, valido_confirmacion, valido_libro]):
        logger.error(f"Datos insuficientes: {[m for m in [msg_principal, msg_confirmacion, msg_libro] if m]}")
        return False

    ruptura = evaluar_ruptura(niveles_datos, niveles_cfg['ruptura_max_dias'])
    rebote = evaluar_rebote(niveles_datos, niveles_cfg['proximidad_tolerancias'])

    setup = evaluate_setup(principal, confirmacion, ob, ruptura, rebote, tf_config)
    if not setup:
        logger.error("Could not evaluate setup")
        return False

    record = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'tf': tf,
        'signal': setup['signal'],
        # La señal se sigue calculando y registrando (hace falta para poder
        # investigar sobre ella), pero 'operable' dice si esta VALIDADA.
        # Medido sobre 605 evaluaciones del 25/08 al 01/09/2026: acierto BRUTO
        # 47,3% y profit factor 0,962 ANTES de costes, o sea indistinguible de
        # una moneda al aire. Y ninguna de las 9 variables candidatas mostro
        # poder predictivo a 1/5/15/30/60/240 min (max |t| = 2,04 en 54
        # pruebas; umbral Bonferroni 3,20). Ver analizador/ic.py.
        # Poner senales_validadas=true en params.json SOLO cuando ic.py
        # encuentre algo estable entre mitades y con |t| sobre el umbral.
        'operable': bool(config.get('senales_validadas', False)),
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


def run_analysis_hist(coin: str, mercado: str, tf: str, as_of: pd.Timestamp,
                     niveles_tf: str, config: Dict, libro: pd.DataFrame,
                     flujo: pd.DataFrame, verbose: bool = False) -> Optional[Dict]:
    """Análisis en modo histórico (replay)."""
    tf_config = get_tf_config(tf, config)
    niveles_cfg = config['niveles']

    tf_index = TF_ORDER.index(tf)
    tf_mayor1 = TF_ORDER[min(tf_index + 1, len(TF_ORDER) - 1)]
    as_of_ms = int(as_of.timestamp() * 1000)

    principal = cargar_indicadores_tf_hist(coin, mercado, tf, config['candles'].get(tf, 30), as_of_ms)
    confirmacion = cargar_indicadores_tf_hist(coin, mercado, tf_mayor1, config['candles'].get(tf_mayor1, 20), as_of_ms)
    ob = cargar_orderbook_hist(libro, flujo, as_of, config['libro_max_edad_seg'],
                               config.get('flujo_max_edad_seg', 150))
    niveles_datos = cargar_niveles_hist(coin, mercado, niveles_tf, as_of_ms)

    # Validar datos
    valido_principal, msg_principal = validar_indicador(principal, 'Principal')
    valido_confirmacion, msg_confirmacion = validar_indicador(confirmacion, 'Confirmacion')
    valido_libro, msg_libro = validar_libro(ob, 'Libro')

    if not all([valido_principal, valido_confirmacion, valido_libro]):
        if verbose:
            msgs = [m for m in [msg_principal, msg_confirmacion, msg_libro] if m]
            logger.warning(f"{as_of.isoformat()}: Saltado - {msgs}")
        return None

    ruptura = evaluar_ruptura(niveles_datos, niveles_cfg['ruptura_max_dias'])
    rebote = evaluar_rebote(niveles_datos, niveles_cfg['proximidad_tolerancias'])

    setup = evaluate_setup(principal, confirmacion, ob, ruptura, rebote, tf_config)
    if not setup:
        if verbose:
            logger.warning(f"{as_of.isoformat()}: evaluate_setup retorno None")
        return None

    return {
        'timestamp': as_of.isoformat(),
        'tf': tf,
        'signal': setup['signal'],
        # La señal se sigue calculando y registrando (hace falta para poder
        # investigar sobre ella), pero 'operable' dice si esta VALIDADA.
        # Medido sobre 605 evaluaciones del 25/08 al 01/09/2026: acierto BRUTO
        # 47,3% y profit factor 0,962 ANTES de costes, o sea indistinguible de
        # una moneda al aire. Y ninguna de las 9 variables candidatas mostro
        # poder predictivo a 1/5/15/30/60/240 min (max |t| = 2,04 en 54
        # pruebas; umbral Bonferroni 3,20). Ver analizador/ic.py.
        # Poner senales_validadas=true en params.json SOLO cuando ic.py
        # encuentre algo estable entre mitades y con |t| sobre el umbral.
        'operable': bool(config.get('senales_validadas', False)),
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _coin_type(valor):
    try:
        return validar_coin(valor)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _entero_positivo(valor):
    n = int(valor)
    if n <= 0:
        raise argparse.ArgumentTypeError("debe ser un entero positivo")
    return n


def main():
    parser = argparse.ArgumentParser(
        description='ETH Setup Analyzer - Producción + Modo Histórico',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)

    parser.add_argument('--tf', type=str, default='5m', choices=TF_ORDER,
                       help='Timeframe principal (default: 5m)')
    parser.add_argument('--niveles-tf', type=str, default=None, choices=TIMEFRAMES,
                       help='TF de niveles a usar (default: igual que --tf)')
    parser.add_argument('--coin', type=_coin_type, default='ETH', help='Coin (default: ETH)')
    parser.add_argument('--mercado', type=str, default='futuros', choices=['spot', 'futuros'],
                       help='Market (default: futuros)')

    # Grupo: modo producción
    grupo_vivo = parser.add_argument_group('Modo Producción (en vivo)')
    grupo_vivo.add_argument('--loop', type=_entero_positivo, default=None,
                           help='Loop cada N segundos (default: single run)')

    # Grupo: modo histórico
    grupo_hist = parser.add_argument_group('Modo Histórico (replay)')
    grupo_hist.add_argument('--replay-desde', type=str, default=None,
                           help='Inicio: YYYY-MM-DD[ HH:MM] UTC')
    grupo_hist.add_argument('--replay-hasta', type=str, default=None,
                           help='Fin: YYYY-MM-DD[ HH:MM] UTC')
    grupo_hist.add_argument('--intervalo', type=_entero_positivo, default=5,
                           help='Minutos entre evaluaciones (default: 5)')

    parser.add_argument('--verbose', action='store_true', help='Verbose output')

    args = parser.parse_args()
    niveles_tf = args.niveles_tf or args.tf

    global CURRENT_TF
    CURRENT_TF = args.tf

    # Reconfigura logging
    log_file = get_log_file(args.tf)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024,
                                backupCount=3, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ],
        force=True
    )

    logger.info(f"Analyzer: {args.coin} {args.mercado} | TF: {args.tf} | niveles: {niveles_tf}")
    if not load_config(CONFIG_FILE).get('senales_validadas', False):
        logger.warning(
            "SENALES NO VALIDADAS (operable=false en el log). Medido sobre datos "
            "de flujo correctos: acierto bruto 47,3%, profit factor 0,962 antes "
            "de costes; ninguna variable con poder predictivo (max |t| 2,04 en 54 "
            "pruebas, umbral 3,20). No operar. Ver analizador/ic.py.")

    # ========== MODO HISTÓRICO ==========
    if args.replay_desde or args.replay_hasta:
        logger.info("=== MODO HISTÓRICO (REPLAY) ===")
        try:
            libro, flujo = cargar_libro_completo_hist(args.coin, args.mercado)
            logger.warning(
                "REPLAY SOBRE SENALES NO VALIDADAS. Los umbrales de params.json se "
                "fijaron contra el tape truncado de libro.py y nunca se recalibraron, "
                "pero el problema es anterior: medido con el tape ya correcto, el "
                "acierto BRUTO es 47,3% y el profit factor 0,962 antes de costes. "
                "Barrer umbrales sobre esto es sobreajustar ruido. Ver analizador/ic.py.")
            desde = pd.Timestamp(args.replay_desde, tz='utc') if args.replay_desde else libro['fecha_utc'].min()
            hasta = pd.Timestamp(args.replay_hasta, tz='utc') if args.replay_hasta else libro['fecha_utc'].max()

            if desde < libro['fecha_utc'].min() or hasta > libro['fecha_utc'].max():
                logger.warning(f"Rango [{desde}, {hasta}] excede libro disponible "
                             f"[{libro['fecha_utc'].min()}, {libro['fecha_utc'].max()}] - "
                             f"fuera de ese margen no habra senal (ob=None)")

            # Detectar gaps en libro
            gaps = detectar_gap_libro(libro['fecha_utc'].tolist(), max_gap_seg=3600)
            if gaps:
                logger.warning(f"[AVISO] Detectados {len(gaps)} gaps en libro > 1 hora:")
                for idx, fecha1, fecha2 in gaps[:5]:  # Mostrar solo los primeros 5
                    logger.warning(f"  [{idx}] {fecha1} -> {fecha2}")

            config = load_config(CONFIG_FILE)
            logger.info(f"Replay {args.coin} {args.mercado} TF={args.tf} (niveles {niveles_tf}) "
                       f"[{desde} -> {hasta}] cada {args.intervalo}min")

            filas = []
            t = desde
            paso = timedelta(minutes=args.intervalo)
            while t <= hasta:
                fila = run_analysis_hist(args.coin, args.mercado, args.tf, t, niveles_tf,
                                         config, libro, flujo, args.verbose)
                if fila is not None:
                    filas.append(fila)
                t += paso

            if not filas:
                logger.error("Sin senales evaluables en el rango pedido")
                sys.exit(1)

            df = pd.DataFrame(filas)
            out_csv = DATA_DIR / f"eth_setup_hist_log_{args.tf}.csv"
            df.to_csv(out_csv, index=False)

            conteo = df['signal'].value_counts()
            logger.info(f"{len(df)} filas evaluadas -> {out_csv}")
            logger.info(f"  LONG={conteo.get('LONG', 0)}  SHORT={conteo.get('SHORT', 0)}  WAIT={conteo.get('WAIT', 0)}")

        except Exception as e:
            logger.error(f"Error en modo historico: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    # ========== MODO PRODUCCIÓN ==========
    else:
        if args.loop:
            logger.info(f"Loop mode: {args.loop} seconds interval")
            run_count = 0
            try:
                while True:
                    run_count += 1
                    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    print(f"\n[{timestamp} UTC] Run #{run_count}")
                    print("=" * 60)

                    run_analysis_vivo(args.coin, args.mercado, args.tf, niveles_tf)

                    next_run = (datetime.now(timezone.utc) + timedelta(seconds=args.loop)).strftime('%H:%M:%S')
                    logger.info(f"Next run: {next_run} UTC")
                    time.sleep(args.loop)
            except KeyboardInterrupt:
                logger.info("Analyzer stopped by user")
                sys.exit(0)
        else:
            success = run_analysis_vivo(args.coin, args.mercado, args.tf, niveles_tf)
            sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
