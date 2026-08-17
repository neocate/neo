import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

_cliente = None

def _init_cliente():
    global _cliente
    if _cliente is None:
        _cliente = ccxt.bitget({
            'apiKey': os.getenv('BITGET_API_KEY'),
            'secret': os.getenv('BITGET_SECRET_KEY'),
            'password': os.getenv('BITGET_PASSPHRASE'),
            'enableRateLimit': True,
        })
    return _cliente

def normalizar_simbolo(par, tipo_mercado, modo=None):
    """normalizar_simbolo(par: str, tipo_mercado: 'f'|'m'|'s', modo: 'cross'|'isolated'|None = None) -> (simbolo: str, modo: str|None)"""
    if '/' in par:
        return par.upper(), modo
    simbolo_base = f"{par.upper()}/USDT"
    tipo = tipo_mercado.lower()

    if modo is None:
        if tipo == 'f':
            modo = 'cross'
        elif tipo == 'm':
            modo = 'isolated'
        else:
            modo = None

    if tipo == 'f':
        return f"{simbolo_base}:USDT", modo
    else:
        return simbolo_base, modo

def velas(simbolo, timeframe, cantidad=100):
    """velas(simbolo: str, timeframe: str, cantidad: int = 100) -> list[[ts, open, high, low, close, volume], ...]"""
    cliente = _init_cliente()
    try:
        ohlcv = cliente.fetch_ohlcv(simbolo, timeframe, limit=cantidad)
        return ohlcv
    except Exception as e:
        raise ValueError(f"Error trayendo velas {simbolo}: {e}")

def precio(simbolo):
    """precio(simbolo: str) -> float"""
    cliente = _init_cliente()
    try:
        ticker = cliente.fetch_ticker(simbolo)
        return ticker['last']
    except Exception as e:
        raise ValueError(f"Error obteniendo precio {simbolo}: {e}")

def libro(simbolo, depth=20):
    """libro(simbolo: str, depth: int = 20) -> {'bids': [[precio, cantidad], ...], 'asks': [[precio, cantidad], ...]}"""
    cliente = _init_cliente()
    try:
        orderbook = cliente.fetch_order_book(simbolo, limit=depth)
        return {
            'bids': orderbook.get('bids', []),
            'asks': orderbook.get('asks', []),
        }
    except Exception as e:
        raise ValueError(f"Error obteniendo libro {simbolo}: {e}")

def funding_rate(simbolo):
    """funding_rate(simbolo: str) -> float | None"""
    cliente = _init_cliente()
    try:
        r = cliente.fetch_funding_rate(simbolo)
        return r.get('fundingRate')
    except Exception as e:
        raise ValueError(f"Error obteniendo funding rate {simbolo}: {e}")

def open_interest(simbolo):
    """open_interest(simbolo: str) -> float | None"""
    cliente = _init_cliente()
    try:
        r = cliente.fetch_open_interest(simbolo)
        return r.get('openInterestAmount')
    except Exception as e:
        raise ValueError(f"Error obteniendo open interest {simbolo}: {e}")

def trades(simbolo, desde=None, limite=500):
    """trades(simbolo: str, desde: int(ms)|None = None, limite: int = 500) -> list[dict]"""
    cliente = _init_cliente()
    try:
        return cliente.fetch_trades(simbolo, since=desde, limit=limite)
    except Exception as e:
        raise ValueError(f"Error trayendo trades {simbolo}: {e}")

class SinDatoParaSimbolo(ValueError):
    pass


def long_short_ratio(simbolo, timeframe='1h'):
    """long_short_ratio(simbolo: str, timeframe: str = '1h') -> float | None. Raises SinDatoParaSimbolo."""
    cliente = _init_cliente()
    try:
        serie = cliente.fetch_long_short_ratio_history(simbolo, timeframe=timeframe, limit=1)
        if not serie:
            return None
        return serie[-1].get('longShortRatio')
    except Exception as e:
        if '"code":"40054"' in str(e):
            raise SinDatoParaSimbolo(f"Bitget no publica long/short ratio para {simbolo}: {e}")
        raise ValueError(f"Error obteniendo long/short ratio {simbolo}: {e}")
