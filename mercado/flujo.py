# ---------------------------------------------------------------
# flujo.py - Capa 2 (order flow): features derivadas del LIBRO de órdenes.
#
# A diferencia de indicadores.py (que resume PRECIO pasado — rezagado), esto
# lee la LIQUIDEZ QUE ESTÁ AHORA en el libro. Es el único insumo que no es
# precio público rezagado: refleja intención de compra/venta antes de que se
# ejecute. Es la materia prima del pivote a order flow.
#
# Un libro es: {'bids': [[precio, vol], ...], 'asks': [[precio, vol], ...]}
# bids ordenados de mayor a menor precio; asks de menor a mayor (formato ccxt).
#
# TESIS del imbalance: si hay mucho más volumen en compra (bids) que en venta
# (asks) cerca del precio, la presión empuja el mid hacia arriba en el corto
# plazo (y viceversa). Es un efecto de microestructura bien documentado.
# ---------------------------------------------------------------


def _mejor(niveles):
    """Mejor nivel [precio, vol] o None si el lado está vacío o es inválido."""
    if not niveles or not isinstance(niveles, (list, tuple)) or len(niveles) == 0:
        return None
    try:
        precio = float(niveles[0][0])
        volumen = float(niveles[0][1])
        return [precio, volumen]
    except (IndexError, TypeError, ValueError):
        return None


def mid(libro):
    """Precio medio entre el mejor bid y el mejor ask. None si falta un lado."""
    if not isinstance(libro, dict):
        return None

    bid = _mejor(libro.get('bids'))
    ask = _mejor(libro.get('asks'))

    if not bid or not ask:
        return None

    return (bid[0] + ask[0]) / 2.0


def spread_bps(libro):
    """Spread (ask-bid) en puntos básicos del mid. None si falta un lado o el spread es <= 0."""
    if not isinstance(libro, dict):
        return None

    bid = _mejor(libro.get('bids'))
    ask = _mejor(libro.get('asks'))

    if not bid or not ask:
        return None

    p_bid, p_ask = bid[0], ask[0]
    m = (p_bid + p_ask) / 2.0

    if m <= 0 or p_ask < p_bid:  # Previene divisiones por cero y libros cruzados
        return None

    return ((p_ask - p_bid) / m) * 10_000.0


def _volumen(niveles, n):
    """Suma el volumen de los primeros N niveles con casteo de seguridad a float."""
    total_vol = 0.0
    for nivel in niveles[:n]:
        try:
            total_vol += float(nivel[1])
        except (IndexError, TypeError, ValueError):
            continue
    return total_vol


def imbalance(libro, niveles=10):
    """Desequilibrio del libro en los primeros 'niveles', en rango [-1, +1].

    +1 = todo el volumen está en bids (presión compradora máxima)
    -1 = todo en asks (presión vendedora)
     0 = equilibrado.  Fórmula: (volBid - volAsk) / (volBid + volAsk).
    """
    if not isinstance(libro, dict) or niveles <= 0:
        return 0.0

    bids = libro.get('bids')
    asks = libro.get('asks')

    if not bids or not asks:
        return 0.0

    vb = _volumen(bids, niveles)
    va = _volumen(asks, niveles)

    total = vb + va
    if total <= 0:
        return 0.0

    return (vb - va) / total


def microprecio(libro):
    """Micro-precio: mid ponderado por el volumen del lado CONTRARIO (top of book).

    Pondera el precio Ask por el volumen del Bid y viceversa:
        MicroPrice = (P_ask * V_bid + P_bid * V_ask) / (V_bid + V_ask)

    Un V_bid alto empuja el microprecio hacia P_ask (por encima del mid),
    reflejando presión compradora de corto plazo.
    """
    if not isinstance(libro, dict):
        return None

    bid = _mejor(libro.get('bids'))
    ask = _mejor(libro.get('asks'))

    if not bid or not ask:
        return None

    pb, vb = bid[0], bid[1]
    pa, va = ask[0], ask[1]

    total_vol = vb + va
    if total_vol <= 0:
        return (pb + pa) / 2.0

    # Ponderación correcta para Micro-Price
    return (pa * vb + pb * va) / total_vol