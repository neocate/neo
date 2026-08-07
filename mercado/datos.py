# ---------------------------------------------------------------
# datos.py - Capa 1: Datos de mercado (CCXT + Bitget)
# ---------------------------------------------------------------

import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

# Cliente CCXT para Bitget
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
    """Normaliza símbolo según tipo de mercado.

    Args:
        par: str (ej: 'eth', 'btc')
        tipo_mercado: 'f' (futuros), 'm' (margen), 's' (spot)
        modo: 'isolated' o 'cross' (solo aplica a margen/futuros)
              default: 'cross' para futuros, 'isolated' para margen

    Returns:
        tuple: (simbolo, modo_efectivo)
               ej: ('ETH/USDT:USDT', 'cross') para futuros
    """
    simbolo_base = f"{par.upper()}/USDT"
    tipo = tipo_mercado.lower()

    # Determinar modo por defecto según tipo
    if modo is None:
        if tipo == 'f':
            modo = 'cross'
        elif tipo == 'm':
            modo = 'isolated'
        else:  # spot
            modo = None

    if tipo == 'f':
        return f"{simbolo_base}:USDT", modo
    else:
        return simbolo_base, modo

def velas(simbolo, timeframe, cantidad=100):
    """Trae velas OHLCV de Bitget.

    Args:
        simbolo: str (ej: 'ETH/USDT:USDT')
        timeframe: str (ej: '3m', '15m', '1h', '4h')
        cantidad: int (cuántas velas traer)

    Returns:
        list: [[timestamp, open, high, low, close, volume], ...]
    """
    cliente = _init_cliente()
    try:
        ohlcv = cliente.fetch_ohlcv(simbolo, timeframe, limit=cantidad)
        return ohlcv
    except Exception as e:
        raise ValueError(f"Error trayendo velas {simbolo}: {e}")

def precio(simbolo):
    """Obtiene precio actual de mercado.

    Args:
        simbolo: str (ej: 'ETH/USDT:USDT')

    Returns:
        float: precio último
    """
    cliente = _init_cliente()
    try:
        ticker = cliente.fetch_ticker(simbolo)
        return ticker['last']
    except Exception as e:
        raise ValueError(f"Error obteniendo precio {simbolo}: {e}")

def libro(simbolo, depth=20):
    """Obtiene libro de órdenes (bids/asks).

    Args:
        simbolo: str (ej: 'ETH/USDT:USDT')
        depth: int (profundidad, por defecto 20)

    Returns:
        dict: {'bids': [[precio, cantidad], ...], 'asks': [[precio, cantidad], ...]}
    """
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
    """Obtiene el funding rate ACTUAL del contrato perpetuo.

    Args:
        simbolo: str (ej: 'BTC/USDT:USDT')

    Returns:
        float o None: tasa de funding del periodo vigente (ej: 0.0001 =
        0.01%). None si el simbolo no es un perpetuo o el exchange no
        reporta nada en ese momento.
    """
    cliente = _init_cliente()
    try:
        r = cliente.fetch_funding_rate(simbolo)
        return r.get('fundingRate')
    except Exception as e:
        raise ValueError(f"Error obteniendo funding rate {simbolo}: {e}")

def open_interest(simbolo):
    """Obtiene el open interest ACTUAL del contrato perpetuo.

    Bitget (via ccxt) no ofrece historial de open interest, solo esta
    lectura instantanea - cualquier serie/ventana hay que acumularla
    llamando esto repetidas veces con el tiempo (ver monitor.py).

    Args:
        simbolo: str (ej: 'BTC/USDT:USDT')

    Returns:
        float o None: cantidad de contratos abiertos (openInterestAmount).
    """
    cliente = _init_cliente()
    try:
        r = cliente.fetch_open_interest(simbolo)
        return r.get('openInterestAmount')
    except Exception as e:
        raise ValueError(f"Error obteniendo open interest {simbolo}: {e}")

def trades(simbolo, desde=None, limite=500):
    """Trae operaciones EJECUTADAS recientes (con lado agresor: buy/sell).

    Es la materia prima del CVD / trade flow: a diferencia del libro (liquidez
    en reposo), esto es volumen que YA cruzo, con su agresor. No tiene
    historico profundo garantizado en Bitget -> hay que grabarlo en vivo si
    se quiere una serie continua (ver herramientas/grabador_libro.py).

    Args:
        simbolo: str (ej: 'BTC/USDT:USDT')
        desde: int ms (solo operaciones posteriores) o None (las mas recientes)
        limite: int (maximo de trades a traer)

    Returns:
        list[dict]: cada uno con 'timestamp', 'price', 'amount', 'side', ...
    """
    cliente = _init_cliente()
    try:
        return cliente.fetch_trades(simbolo, since=desde, limit=limite)
    except Exception as e:
        raise ValueError(f"Error trayendo trades {simbolo}: {e}")

def long_short_ratio(simbolo, timeframe='1h'):
    """Ratio agregado de cuentas en largo vs en corto del mercado (NO tu
    propia posicion) - posicionamiento del mercado de derivados, endpoint
    publico de Bitget. Devuelve el punto MAS RECIENTE.

    OJO: el endpoint de Bitget no pagina por 'since'/'limit' de forma fiable
    (devuelve una ventana fija propia, ~30h vista en pruebas) - por eso esto
    siempre toma el ultimo elemento de lo que venga, no confia en los
    parametros para acotar el rango.

    Args:
        simbolo: str (ej: 'BTC/USDT:USDT')
        timeframe: str (ej: '1h', '4h', '1d')

    Returns:
        float o None: longShortRatio (>1 = mas cuentas en largo que en corto).
    """
    cliente = _init_cliente()
    try:
        serie = cliente.fetch_long_short_ratio_history(simbolo, timeframe=timeframe, limit=1)
        if not serie:
            return None
        return serie[-1].get('longShortRatio')
    except Exception as e:
        raise ValueError(f"Error obteniendo long/short ratio {simbolo}: {e}")

