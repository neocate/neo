
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
    cliente = _init_cliente()

    try:
        mercados = cliente.fetch_markets()
        mercado = None
        for m in mercados:
            if m['symbol'] == simbolo:
                mercado = m
                break

        if not mercado:
            raise ValueError(f"Par {simbolo} no encontrado en Bitget")

        comisiones = _leer_comisiones(cliente, simbolo)

        leverage_maximo, margen_maximo = _leer_leverage(mercado)

        funding_rate = _leer_funding_rate(cliente, simbolo)

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
    limits = mercado.get('limits', {})

    leverage_maximo = 125
    margen_maximo = 1.0 / leverage_maximo if leverage_maximo else 0.008

    if 'leverage' in limits and limits['leverage']:
        leverage_maximo = limits['leverage'].get('max', 125)
        if leverage_maximo:
            margen_maximo = 1.0 / leverage_maximo
        else:
            margen_maximo = 0.008

    return leverage_maximo, margen_maximo

def _leer_funding_rate(cliente, simbolo):
    if ':' not in simbolo:
        return None

    try:
        return None
    except:
        return None

def _leer_interest_rate(cliente, simbolo):
    if ':' in simbolo:
        return None

    try:
        return None
    except:
        return None

def resumen(simbolo):
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
