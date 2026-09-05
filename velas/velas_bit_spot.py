
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import velas_bit as _vb

MERCADO = 'spot'
SIMBOLO = _vb.MERCADOS[MERCADO]

TIMEFRAMES = _vb.TIMEFRAMES
TF_SEGUNDOS = _vb.TF_SEGUNDOS
CABECERA = _vb.CABECERA
DIR_VELAS = _vb.DIR_VELAS
DIR_LOCK = _vb.DIR_LOCK
DIR_LOG = _vb.DIR_LOG
MARGEN_CIERRE = _vb.MARGEN_CIERRE
CONFIRMA_SEG = _vb.CONFIRMA_SEG

origen_exchange = _vb.origen_exchange
resolver_tfs = _vb.resolver_tfs
parsear_args = _vb.parsear_args
extraer_moneda = _vb._extraer_moneda
estado_vela = _vb._estado_vela
fecha = _vb._fecha
ts_actual = _vb._ts_actual
ts_ultima_cerrada = _vb._ts_ultima_cerrada
tf_ms = _vb._tf_ms


def simbolo(coin):
    return _vb._simbolo(coin, MERCADO)


def ruta_csv(coin, timeframe):
    return _vb.ruta_csv(coin, timeframe, MERCADO)


def Lock(coin, timeframe):
    return _vb.Lock(coin, timeframe, MERCADO)


def log(coin, timeframe, mensaje, consola=True):
    return _vb._log(coin, timeframe, MERCADO, mensaje, consola=consola)


def poner_al_dia(coin, timeframe, origen_ts=None):
    return _vb.poner_al_dia(coin, timeframe, MERCADO, origen_ts=origen_ts)


def bajar_por_lotes(coin, timeframe, destino_ts, origen_ts=None):
    return _vb.bajar_por_lotes(coin, timeframe, MERCADO, destino_ts,
                               origen_ts=origen_ts)


def vela_actual(coin, timeframe):
    return _vb.vela_actual(coin, timeframe, MERCADO)


def ultimas_velas(coin, timeframe, n):
    return _vb.ultimas_velas(coin, timeframe, MERCADO, n)


def resumen(coin, tfs):
    return _vb.resumen(coin, tfs, MERCADO)
