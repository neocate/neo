#!/usr/bin/env python3
"""
_analyzer_core.py - Lógica compartida entre analyzer.py (producción) y modo histórico.

Exporta:
  - Carga/validación de config
  - evaluate_setup() - la única fuente de verdad en evaluación de señales
  - evaluar_ruptura(), evaluar_rebote() - lógica de niveles
  - Detección de anomalías en datos
  - TF_ORDER y constantes
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)

TF_ORDER = ['1m', '5m', '15m', '1h', '4h']


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Combina 'override' sobre 'base' recursivamente sin borrar defaults."""
    resultado = dict(base)
    for clave, valor in override.items():
        if isinstance(valor, dict) and isinstance(resultado.get(clave), dict):
            resultado[clave] = _deep_merge(resultado[clave], valor)
        else:
            resultado[clave] = valor
    return resultado


def _validar_config(config: Dict) -> Dict:
    """Valida tipos/rangos tras deep-merge."""
    errores = []

    def _entero_positivo(v):
        return isinstance(v, int) and not isinstance(v, bool) and v > 0

    def _numero(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def _numero_positivo(v):
        return _numero(v) and v > 0

    for tf, n in config.get('candles', {}).items():
        if not _entero_positivo(n):
            errores.append(f"candles.{tf}={n!r} debe ser un entero positivo")

    niveles = config.get('niveles', {})
    for campo in ('ruptura_max_dias', 'proximidad_tolerancias'):
        if campo in niveles and not _numero_positivo(niveles[campo]):
            errores.append(f"niveles.{campo}={niveles[campo]!r} debe ser un numero positivo")

    for campo in ('libro_max_edad_seg', 'flujo_max_edad_seg'):
        if campo in config and not _numero_positivo(config[campo]):
            errores.append(f"{campo}={config[campo]!r} debe ser un numero positivo")

    def _validar_bloque(bloque, etiqueta):
        thresholds = bloque.get('thresholds', {})
        for campo in ('imbalance_long', 'imbalance_short', 'delta_long', 'delta_short',
                      'vol_ratio_high', 'vol_ratio_low'):
            if campo in thresholds and not _numero(thresholds[campo]):
                errores.append(f"{etiqueta}.thresholds.{campo}={thresholds[campo]!r} debe ser numero")

        signals = bloque.get('signals', {})
        for campo in ('min_conditions_long', 'min_conditions_short', 'strong_conditions'):
            if campo in signals and not _entero_positivo(signals[campo]):
                errores.append(f"{etiqueta}.signals.{campo}={signals[campo]!r} debe ser entero positivo")

    _validar_bloque(config, 'config')
    for tf, bloque in config.get('by_tf', {}).items():
        _validar_bloque(bloque, f"by_tf.{tf}")

    if errores:
        for e in errores:
            logger.error(f"Config invalida: {e}")
        raise ValueError(f"{len(errores)} error(es) de validacion en params.json (ver logs)")

    return config


def load_config(config_file: Optional[Path] = None) -> Dict:
    """Carga config con defaults. Si config_file es None, usa params.json en mismo dir que este módulo."""
    if config_file is None:
        config_file = Path(__file__).parent / "params.json"

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
        },
        # Dos relojes distintos: el libro lo graba libro.py cada 900s y el
        # flujo lo graba flujo.py cada 60s. Mezclarlos bajo un solo umbral
        # dejaria pasar un flujo de 25 minutos como si fuera del momento.
        "libro_max_edad_seg": 1500,
        "flujo_max_edad_seg": 150,
        # Las senales se registran siempre; 'operable' en el log dice si estan
        # validadas. Por defecto NO: ver la nota en el record de analyzer.py.
        "senales_validadas": False
    }

    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                loaded = json.load(f)
            default_config = _validar_config(_deep_merge(default_config, loaded))
        except Exception as e:
            logger.warning(f"Error loading config: {e}, using defaults")

    return default_config


def get_tf_config(tf: str, config: Dict) -> Dict:
    """Config específica del TF. Si no existe, usa defaults globales."""
    if 'by_tf' in config and tf in config['by_tf']:
        return config['by_tf'][tf]
    return {
        'thresholds': config.get('thresholds', {}),
        'signals': config.get('signals', {})
    }


def get_config_hash(config_dict: Dict) -> str:
    """Hash SHA256 corto de la config para detectar cambios."""
    config_json = json.dumps(config_dict, sort_keys=True)
    return hashlib.sha256(config_json.encode()).hexdigest()[:12]


def evaluar_ruptura(niveles_datos: Optional[Dict], max_dias: float) -> Dict[str, bool]:
    """Evalua ruptura de techos/suelos recientes."""
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
    """Evalua proximidad a niveles vigentes."""
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


def evaluate_setup(principal: Optional[Dict], confirmacion: Optional[Dict],
                   ob: Optional[Dict], ruptura: Optional[Dict], rebote: Optional[Dict],
                   config: Dict) -> Optional[Dict]:
    """
    UNICA FUENTE DE VERDAD en evaluacion de senales.
    Retorna None si faltan datos criticos (principal, confirmacion, ob).
    """
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
        ruptura['long'] if ruptura else False,
        rebote['long'] if rebote else False,
    ]
    condiciones_short = [
        principal['close'] < principal['sma'],
        confirmacion['trend'] == 'DOWN',
        ob['imbalance'] < thresholds['imbalance_short'],
        ob['delta'] < thresholds['delta_short'],
        principal['vol_ratio'] < thresholds['vol_ratio_low'],
        ruptura['short'] if ruptura else False,
        rebote['short'] if rebote else False,
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
# DETECCIÓN DE ANOMALÍAS EN DATOS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def detectar_spike_velas(velas: List[list], max_desviacion_pct: float = 10.0) -> Optional[str]:
    """
    Detecta si hay valores extremos en velas (posible data corruption).
    Retorna descripcion del spike si lo encuentra, None si OK.

    Compara cada vela contra la mediana de las anteriores.
    """
    if len(velas) < 3:
        return None

    closes = [v[4] for v in velas]
    highs = [v[2] for v in velas]
    lows = [v[3] for v in velas]

    for i in range(2, len(velas)):
        ventana_anterior = closes[max(0, i-10):i]
        if not ventana_anterior:
            continue

        median = sorted(ventana_anterior)[len(ventana_anterior) // 2]
        current_close = closes[i]

        desv_pct = abs(current_close - median) / median * 100
        if desv_pct > max_desviacion_pct:
            return (f"Spike en vela [{i}]: close={current_close:.2f} vs mediana={median:.2f} "
                   f"({desv_pct:.1f}% desviacion)")

    return None


def detectar_gap_libro(fechas_utc: List, max_gap_seg: int = 3600) -> List[Tuple[int, str, str]]:
    """
    Detecta brechas anormales en libro (posible corte de datos).
    Retorna lista de (indice, fecha1, fecha2) para cada gap > max_gap_seg.
    """
    gaps = []
    for i in range(1, len(fechas_utc)):
        diff = (fechas_utc[i] - fechas_utc[i-1]).total_seconds()
        if diff > max_gap_seg:
            gaps.append((i, fechas_utc[i-1].isoformat(), fechas_utc[i].isoformat()))
    return gaps


def validar_indicador(indicador: Optional[Dict], nombre: str,
                     n_velas_min: int = 2) -> Tuple[bool, Optional[str]]:
    """
    Valida que un indicador (principal, confirmacion) sea usable.
    Retorna (es_valido, mensaje_si_invalido).
    """
    if indicador is None:
        return False, f"{nombre}: No disponible (velas insuficientes o error de lectura)"

    campos_requeridos = ['close', 'sma', 'trend', 'vol_ratio', 'rsi']
    for campo in campos_requeridos:
        if campo not in indicador:
            return False, f"{nombre}: Falta campo '{campo}'"

    if indicador.get('close', 0) <= 0:
        return False, f"{nombre}: close={indicador['close']} invalido"

    if indicador.get('vol_ratio', 0) < 0:
        return False, f"{nombre}: vol_ratio={indicador['vol_ratio']} invalido"

    return True, None


def validar_libro(libro: Optional[Dict], nombre: str) -> Tuple[bool, Optional[str]]:
    """Valida que orderbook tenga campos esperados."""
    if libro is None:
        return False, f"{nombre}: No disponible (archivo corrupto o desactualizado)"

    campos = ['imbalance', 'delta']
    for campo in campos:
        if campo not in libro:
            return False, f"{nombre}: Falta campo '{campo}'"

    return True, None
