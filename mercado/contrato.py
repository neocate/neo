# ---------------------------------------------------------------
# mercado/contrato.py - Lee contrato del par (margen, comisiones, etc)
# ---------------------------------------------------------------

import ccxt
import os
from dotenv import load_dotenv

from mercado import datos

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
    """Lee comisiones maker/taker del par."""
    try:
        fees = cliente.fetch_trading_fees(simbolo)
        return {
            'maker': fees.get('maker', 0.0002),
            'taker': fees.get('taker', 0.0006),
        }
    except:
        # Default Bitget
        return {'maker': 0.0002, 'taker': 0.0006}

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
    """Lee funding rate actual (futuros). Retorna None si no aplica o falla.

    Reusa mercado.datos.funding_rate() (fetch_funding_rate de ccxt, ya
    probado en vivo - lo usa herramientas/grabador_libro.py) en vez de
    duplicar la llamada aqui - antes esta funcion era un stub que devolvia
    None siempre ("CCXT puede no tener esto"), lo que habria dado datos
    silenciosamente vacios al futuro uso de obtener_contrato() para la
    cartera simulada (ver PENDIENTES.md, marcador_tpsl.py: "que lea
    contrato para comisiones y funding")."""
    if ':' not in simbolo:
        return None  # No es futuros
    try:
        return datos.funding_rate(simbolo)
    except ValueError:
        return None

def _leer_interest_rate(cliente, simbolo):
    """Lee interest rate de margen. Retorna None si no aplica.

    Sin implementar A PROPOSITO, no es un descuido: este proyecto solo
    opera futuros USDT-M (ver mercado/datos.py, herramientas/descargar_bit.py
    - todos los simbolos traen ':', nunca margen), y Bitget exige un
    endpoint privado propio para esto que no esta cableado en ningun sitio
    del proyecto todavia. Si algun dia se opera margen de verdad, esto
    necesita una implementacion real, no basta con quitar este comentario."""
    if ':' in simbolo:
        return None  # Es futuros, no margen
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
