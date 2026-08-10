# ---------------------------------------------------------------
# senales.py - Capa 2: señales de VELA (impulso, ruptura, rechazo,
# divergencia RSI) sobre velas YA CERRADAS.
#
# A diferencia de flujo.py (libro de ordenes - irrepetible, ver su cabecera
# y la de herramientas/grabador_libro.py), esto trabaja sobre velas: se
# puede recalcular en frio en cualquier momento porque el exchange guarda
# su historico (mismo motivo por el que grabador_libro.py NO necesita
# grabar velas en vivo, solo refrescarlas).
#
# Todas las señales se evaluan sobre la ULTIMA vela de 'velas' (se asume
# cerrada - mismo criterio que descargar_bit.py, que nunca descarga la
# vela en curso). 'velas' es una lista de [timestamp, open, high, low,
# close, volumen], ordenada de mas vieja a mas nueva.
# ---------------------------------------------------------------

from mercado import indicadores

# Tope de velas recientes que 'detectar()' realmente necesita mirar. ATR/RSI
# de indicadores.py son O(N) y recalculan la serie ENTERA desde el principio
# en cada llamada - si el caller (monitor_niveles.py) pasa el historico
# completo (90 dias de 1m son ~130k filas) y llama a detectar() en CADA
# vela nueva, el costo por vela crece sin limite mientras el proceso siga
# vivo. El suavizado de Wilder (ATR/RSI) decae exponencialmente: a partir de
# unas pocas centenas de velas la diferencia frente a usar todo el historico
# es insignificante, y de sobra para varios swings de extremos_locales
# incluso con k grande - asi que se puede truncar sin perder precision real.
VENTANA_MAXIMA = 500

VENTANA_RUPTURA = 30
VENTANA_IMPULSO = 3
UMBRAL_IMPULSO_ATR = 2.5
UMBRAL_ACELERACION_ATR = 1.2
UMBRAL_ACELERACION_RITMO = 2.5
RSI_SOBRECOMPRA = 70.0
RSI_SOBREVENTA = 30.0


def _series(velas):
    aperturas = [v[1] for v in velas]
    altos = [v[2] for v in velas]
    bajos = [v[3] for v in velas]
    cierres = [v[4] for v in velas]
    return aperturas, altos, bajos, cierres


def _impulso(cierres, atr_actual, ventana=VENTANA_IMPULSO, umbral=UMBRAL_IMPULSO_ATR):
    """'impulso_alza'/'impulso_baja': las ultimas 'ventana' velas se
    movieron, en conjunto (cierre actual contra el cierre de hace 'ventana'
    velas), mas de 'umbral'xATR en una direccion."""
    if atr_actual is None or atr_actual <= 0 or len(cierres) <= ventana:
        return None
    movimiento = cierres[-1] - cierres[-1 - ventana]
    if movimiento > umbral * atr_actual:
        return "alza"
    if movimiento < -umbral * atr_actual:
        return "baja"
    return None


def _aceleracion(aperturas, cierres, atr_actual, ventana=VENTANA_IMPULSO,
                  umbral_atr=UMBRAL_ACELERACION_ATR, umbral_ritmo=UMBRAL_ACELERACION_RITMO):
    """'aceleracion_alza'/'aceleracion_baja': la ULTIMA vela sola se movio
    (cierre-apertura) mas de 'umbral_atr'xATR Y mas de 'umbral_ritmo' veces
    el ritmo (movimiento absoluto medio cierre-apertura) de las 'ventana'
    velas previas a ella."""
    if atr_actual is None or atr_actual <= 0 or len(cierres) <= ventana:
        return None
    mov_ultima = cierres[-1] - aperturas[-1]
    previas = [abs(cierres[-1 - i] - aperturas[-1 - i]) for i in range(1, ventana + 1)]
    ritmo_previo = sum(previas) / len(previas)
    if ritmo_previo <= 0:
        return None
    if abs(mov_ultima) <= umbral_atr * atr_actual:
        return None
    if abs(mov_ultima) <= umbral_ritmo * ritmo_previo:
        return None
    return "alza" if mov_ultima > 0 else "baja"


def _ruptura(altos, bajos, cierres, ventana=VENTANA_RUPTURA):
    """'ruptura_alza'/'ruptura_baja': el CIERRE de la ultima vela rompe el
    maximo/minimo de las 'ventana' velas ANTERIORES (sin contar la propia)."""
    if len(cierres) <= ventana:
        return None
    max_previo = max(altos[-1 - ventana:-1])
    min_previo = min(bajos[-1 - ventana:-1])
    if cierres[-1] > max_previo:
        return "alza"
    if cierres[-1] < min_previo:
        return "baja"
    return None


def _rechazo(altos, bajos, cierres, ventana=VENTANA_RUPTURA):
    """'rechazo_max'/'rechazo_min': la MECHA de la ultima vela pincha un
    nuevo extremo de las 'ventana' velas anteriores, pero el CIERRE se
    queda dentro de ese rango previo (mecha de rechazo, ruptura no
    confirmada). Devuelve una lista ("max"/"min", pueden darse ambas a la
    vez en una vela muy volatil)."""
    if len(cierres) <= ventana:
        return []
    max_previo = max(altos[-1 - ventana:-1])
    min_previo = min(bajos[-1 - ventana:-1])
    eventos = []
    if altos[-1] > max_previo and cierres[-1] <= max_previo:
        eventos.append("max")
    if bajos[-1] < min_previo and cierres[-1] >= min_previo:
        eventos.append("min")
    return eventos


def _divergencias(velas, altos, bajos, rsi_serie, k):
    """'div_bajista'/'div_alcista': el ultimo swing de PRECIO (via
    indicadores.extremos_locales, mismo detector que usa
    niveles_soporte.py) marca un nuevo extremo que el RSI NO confirma
    (maximo de precio mas alto con RSI mas bajo, o minimo de precio mas
    bajo con RSI mas alto) frente al swing anterior del mismo lado.

    Solo se evalua si el swing mas reciente que puede confirmar
    extremos_locales() CAE justo en la ultima vela (indice len(velas)-1-k,
    el mas nuevo posible con k vecinos a la derecha ya cerrados) - si no,
    ya se habria avisado (o descartado) en una vuelta anterior."""
    idx_altos, idx_bajos = indicadores.extremos_locales(velas, k)
    ultimo_posible = len(velas) - 1 - k
    eventos = []

    if len(idx_altos) >= 2 and idx_altos[-1] == ultimo_posible:
        i_prev, i_ult = idx_altos[-2], idx_altos[-1]
        if (altos[i_ult] > altos[i_prev] and rsi_serie[i_ult] is not None
                and rsi_serie[i_prev] is not None and rsi_serie[i_ult] < rsi_serie[i_prev]):
            eventos.append("bajista")

    if len(idx_bajos) >= 2 and idx_bajos[-1] == ultimo_posible:
        i_prev, i_ult = idx_bajos[-2], idx_bajos[-1]
        if (bajos[i_ult] < bajos[i_prev] and rsi_serie[i_ult] is not None
                and rsi_serie[i_prev] is not None and rsi_serie[i_ult] > rsi_serie[i_prev]):
            eventos.append("alcista")

    return eventos


def _rsi_giro(rsi_serie):
    """'rsi_sobrecompra'/'rsi_sobreventa': RSI en zona extrema Y ya girando
    de vuelta hacia el centro (vela a vela, no solo tocando el umbral)."""
    if len(rsi_serie) < 2 or rsi_serie[-1] is None or rsi_serie[-2] is None:
        return None
    actual, previo = rsi_serie[-1], rsi_serie[-2]
    if actual >= RSI_SOBRECOMPRA and actual < previo:
        return "sobrecompra"
    if actual <= RSI_SOBREVENTA and actual > previo:
        return "sobreventa"
    return None


def detectar(velas, k, ventana_ruptura=VENTANA_RUPTURA):
    """Evalua las 12 señales sobre la ULTIMA vela de 'velas' (asumida
    cerrada). 'k' es el mismo parametro de extremos_locales que ya se usa
    para detectar niveles (ver niveles_soporte.py) - se reutiliza aqui para
    los swings de divergencia, sin introducir un segundo criterio de
    "swing" en el proyecto.

    Devuelve una lista de nombres de señales activas (puede estar vacia, y
    puede tener varias a la vez - no son excluyentes entre si). ATR y RSI
    usan los periodos por defecto de indicadores.py (14) - con poca
    historia todavia, ambos devuelven None y las señales que dependen de
    ellos simplemente no se activan.

    Si 'velas' trae mas historico del que hace falta, se recorta a
    VENTANA_MAXIMA (ver comentario junto a la constante) - el caller puede
    pasar tranquilamente la lista completa sin preocuparse de acotarla el
    mismo en cada llamada."""
    limite = max(VENTANA_MAXIMA, ventana_ruptura + 50, 4 * k + 50)
    if len(velas) > limite:
        velas = velas[-limite:]

    aperturas, altos, bajos, cierres = _series(velas)
    atr_serie = indicadores.atr(altos, bajos, cierres)
    rsi_serie = indicadores.rsi(cierres)
    atr_actual = atr_serie[-1]

    activas = []

    impulso = _impulso(cierres, atr_actual)
    if impulso:
        activas.append(f"impulso_{impulso}")

    aceleracion = _aceleracion(aperturas, cierres, atr_actual)
    if aceleracion:
        activas.append(f"aceleracion_{aceleracion}")

    ruptura = _ruptura(altos, bajos, cierres, ventana_ruptura)
    if ruptura:
        activas.append(f"ruptura_{ruptura}")

    for lado in _rechazo(altos, bajos, cierres, ventana_ruptura):
        activas.append(f"rechazo_{lado}")

    for lado in _divergencias(velas, altos, bajos, rsi_serie, k):
        activas.append(f"div_{lado}")

    rsi_extremo = _rsi_giro(rsi_serie)
    if rsi_extremo == "sobrecompra":
        activas.append("rsi_sobrecompra")
    elif rsi_extremo == "sobreventa":
        activas.append("rsi_sobreventa")

    return activas
