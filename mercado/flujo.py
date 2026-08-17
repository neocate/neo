def _mejor(niveles):
    """_mejor(niveles: list) -> [precio, volumen] | None"""
    if not niveles or not isinstance(niveles, (list, tuple)) or len(niveles) == 0:
        return None
    try:
        precio = float(niveles[0][0])
        volumen = float(niveles[0][1])
        return [precio, volumen]
    except (IndexError, TypeError, ValueError):
        return None


def mid(libro):
    """mid(libro: {'bids': [...], 'asks': [...]}) -> float | None"""
    if not isinstance(libro, dict):
        return None

    bid = _mejor(libro.get('bids'))
    ask = _mejor(libro.get('asks'))

    if not bid or not ask or ask[0] < bid[0]:
        return None

    return (bid[0] + ask[0]) / 2.0


def spread_bps(libro):
    """spread_bps(libro: {'bids': [...], 'asks': [...]}) -> float | None"""
    if not isinstance(libro, dict):
        return None

    bid = _mejor(libro.get('bids'))
    ask = _mejor(libro.get('asks'))

    if not bid or not ask:
        return None

    p_bid, p_ask = bid[0], ask[0]
    m = (p_bid + p_ask) / 2.0

    if m <= 0 or p_ask < p_bid:
        return None

    return ((p_ask - p_bid) / m) * 10_000.0


def _volumen(niveles, n):
    """_volumen(niveles: list, n: int) -> float"""
    total_vol = 0.0
    for nivel in niveles[:n]:
        try:
            total_vol += float(nivel[1])
        except (IndexError, TypeError, ValueError):
            continue
    return total_vol


def imbalance(libro, niveles=10):
    """imbalance(libro: {'bids': [...], 'asks': [...]}, niveles: int = 10) -> float en [-1, 1]"""
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
    """microprecio(libro: {'bids': [...], 'asks': [...]}) -> float | None"""
    if not isinstance(libro, dict):
        return None

    bid = _mejor(libro.get('bids'))
    ask = _mejor(libro.get('asks'))

    if not bid or not ask or ask[0] < bid[0]:
        return None

    pb, vb = bid[0], bid[1]
    pa, va = ask[0], ask[1]

    total_vol = vb + va
    if total_vol <= 0:
        return (pb + pa) / 2.0

    return (pa * vb + pb * va) / total_vol
