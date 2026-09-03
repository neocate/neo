# ---------------------------------------------------------------
# mercado/contrato.py - Lee contrato del par (margen, comisiones, etc)
# ---------------------------------------------------------------

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

def obtener_contrato(simbolo):
    """Lee contrato completo del par: comisiones, margen, mínimo, etc.

    Args:
        simbolo: str (ej: 'ETH/USDT:USDT')

    Returns:
        dict: {
            'simbolo': str,
            'comision_maker': float,
            'comision_taker': float,
            'minimo_cantidad': float,
            'minimo_valor': float,
            'precision_cantidad': int,
            'precision_precio': int,
            'leverage_maximo': float,
            'margen_maximo': float,
            'funding_rate': float (futuros, None si no aplica),
            'interest_rate': float (margen, None si no aplica),
        }
    """
    cliente = _init_cliente()

    try:
        # Obtener info del mercado
        mercados = cliente.fetch_markets()
        mercado = None
        for m in mercados:
            if m['symbol'] == simbolo:
                mercado = m
                break

        if not mercado:
            raise ValueError(f"Par {simbolo} no encontrado en Bitget")

        # Comisiones por defecto
        comisiones = _leer_comisiones(cliente, simbolo)

        # Apalancamiento y margen (depende del tipo)
        leverage_maximo, margen_maximo = _leer_leverage(mercado)

        # Funding rate (solo futuros)
        funding_rate = _leer_funding_rate(cliente, simbolo)

        # Interest rate (solo margen)
        interest_rate = _leer_interest_rate(cliente, simbolo)

        return {
            'simbolo': simbolo,
            'comision_maker': comisiones.get('maker', 0.0002),
            'comision_taker': comisiones.get('taker', 0.0006),
            'comision_fuente': comisiones.get('fuente', 'estimado'),
            'minimo_cantidad': mercado.get('limits', {}).get('amount', {}).get('min', 0.0001),
            'minimo_valor': mercado.get('limits', {}).get('cost', {}).get('min', 5),
            'precision_cantidad': mercado.get('precision', {}).get('amount', 8),
            'precision_precio': mercado.get('precision', {}).get('price', 2),
            'leverage_maximo': leverage_maximo,
            'margen_maximo': margen_maximo,
            'funding_rate': funding_rate,
            'interest_rate': interest_rate,
        }

    except Exception as e:
        raise ValueError(f"Error leyendo contrato {simbolo}: {e}")

def _leer_comisiones(cliente, simbolo):
    """Comisiones maker/taker del par, diciendo de DONDE salen.

    Antes llamaba a fetch_trading_feeS(simbolo): esa funcion es PLURAL, no
    lleva simbolo y devuelve todos los pares. Pasarselo lo colaba como
    'params' y reventaba con "'str' object has no attribute 'keys'". El
    except desnudo se lo tragaba y devolvia los defaults, pero contrato.py
    seguia diciendo que venian del contrato: todos los backtests reportaban
    comisiones "reales" que en realidad eran estimadas.

    Orden de preferencia:
      1. fetch_trading_fee (SINGULAR) -> el tramo VIP real de la cuenta,
         pero necesita credenciales.
      2. market['maker'/'taker'] -> tarifa publica del par, ya cargada por
         load_markets, sin peticion adicional ni auth.
      3. defaults, solo si las dos anteriores fallan.
    """
    if cliente.apiKey:
        try:
            f = cliente.fetch_trading_fee(simbolo)
            if f.get('maker') is not None and f.get('taker') is not None:
                return {'maker': float(f['maker']), 'taker': float(f['taker']),
                        'fuente': 'cuenta'}
        except Exception:
            pass
    try:
        m = cliente.market(simbolo)
        if m.get('maker') is not None and m.get('taker') is not None:
            return {'maker': float(m['maker']), 'taker': float(m['taker']),
                    'fuente': 'mercado'}
    except Exception:
        pass
    return {'maker': 0.0002, 'taker': 0.0006, 'fuente': 'estimado'}

def _leer_leverage(mercado):
    """Lee límites de apalancamiento y margen del mercado."""
    limits = mercado.get('limits', {})

    # Intenta obtener del objeto mercado
    leverage_maximo = 125  # Default Bitget
    margen_maximo = 1.0 / leverage_maximo if leverage_maximo else 0.008

    if 'leverage' in limits and limits['leverage']:
        leverage_maximo = limits['leverage'].get('max', 125)
        if leverage_maximo:
            margen_maximo = 1.0 / leverage_maximo
        else:
            margen_maximo = 0.008

    return leverage_maximo, margen_maximo

def _leer_funding_rate(cliente, simbolo):
    """Lee funding rate actual (futuros). Retorna None si no aplica."""
    if ':' not in simbolo:
        return None  # No es futuros

    try:
        # Bitget API para funding rate
        # CCXT puede no tener esto, así que retornamos None por ahora
        # En la práctica, usarías cliente.private_request() o REST directo
        return None
    except:
        return None

def _leer_interest_rate(cliente, simbolo):
    """Lee interest rate de margen. Retorna None si no aplica."""
    if ':' in simbolo:
        return None  # Es futuros, no margen

    try:
        # Similar a funding rate, Bitget requiere endpoint privado
        return None
    except:
        return None

def resumen(simbolo):
    """Imprime resumen del contrato en formato legible."""
    contrato = obtener_contrato(simbolo)

    print(f"\n=== CONTRATO: {contrato['simbolo']} ===")
    print(f"Comisiones:")
    print(f"  Maker:  {contrato['comision_maker']*100:.4f}%")
    print(f"  Taker:  {contrato['comision_taker']*100:.4f}%")
    print(f"\nLímites de orden:")
    print(f"  Mínimo cantidad:  {contrato['minimo_cantidad']}")
    print(f"  Mínimo valor:     {contrato['minimo_valor']} USDT")
    print(f"  Precisión:        {contrato['precision_cantidad']} decimales")
    print(f"\nApalancamiento:")

    if contrato['leverage_maximo'] and contrato['leverage_maximo'] > 0:
        print(f"  Máximo:           {contrato['leverage_maximo']}x")
        print(f"  Margen mínimo:    {contrato['margen_maximo']*100:.2f}%")
    else:
        print(f"  Máximo:           N/A (margen sin apalancamiento reportado)")
        print(f"  Margen mínimo:    {contrato['margen_maximo']*100:.2f}%")

    if contrato['funding_rate'] is not None:
        print(f"\nFinanciamiento (futuros):")
        print(f"  Funding rate:     {contrato['funding_rate']}")

    if contrato['interest_rate'] is not None:
        print(f"\nInterés (margen):")
        print(f"  Interest rate:    {contrato['interest_rate']}")

    return contrato
