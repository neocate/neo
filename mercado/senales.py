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

import json
import os
import time

from mercado import indicadores

# Parametros en caliente (2026-08-17, mismo patron que mercado/indicadores.py
# y herramientas/niveles.py): los umbrales/ventanas de mas abajo ya NO son
# constantes de modulo fijas - son los valores por defecto de
# PARAMS_DEFECTO, sobreescribibles sin tocar codigo via senales_config.json
# (pensado para telegram_control.py mas adelante, de momento nada escribe
# ahi todavia). Si el fichero no existe, el comportamiento es identico al
# de siempre.
PARAMS_DEFECTO = {
    # Tope de velas recientes que 'detectar()' realmente necesita mirar.
    # ATR/RSI de indicadores.py son O(N) y recalculan la serie ENTERA desde
    # el principio en cada llamada - si el caller (monitor_niveles.py) pasa
    # el historico completo (90 dias de 1m son ~130k filas) y llama a
    # detectar() en CADA vela nueva, el costo por vela crece sin limite
    # mientras el proceso siga vivo. El suavizado de Wilder (ATR/RSI) decae
    # exponencialmente: a partir de unas pocas centenas de velas la
    # diferencia frente a usar todo el historico es insignificante, y de
    # sobra para varios swings de extremos_locales incluso con k grande -
    # asi que se puede truncar sin perder precision real.
    "ventana_maxima": 500,
    "ventana_ruptura": 30,
    "ventana_impulso": 3,
    "umbral_impulso_atr": 2.5,
    "umbral_aceleracion_atr": 1.2,
    "umbral_aceleracion_ritmo": 2.5,
    "rsi_sobrecompra": 70.0,
    "rsi_sobreventa": 30.0,
}

LIMITES_PARAMS = {
    "ventana_maxima": (10, 100_000),
    "ventana_ruptura": (2, 5000),
    "ventana_impulso": (1, 5000),
    "umbral_impulso_atr": (0.01, 100.0),
    "umbral_aceleracion_atr": (0.01, 100.0),
    "umbral_aceleracion_ritmo": (0.01, 100.0),
    "rsi_sobrecompra": (50.0, 100.0),
    "rsi_sobreventa": (0.0, 50.0),
}

# Compatibilidad hacia atras: descargar_bit.py/backtest_senales.py/
# monitor_senales.py referencian VENTANA_MAXIMA como constante de modulo
# directa (import o 'senales.VENTANA_MAXIMA'), no via cargar_params() - se
# mantiene aqui con el valor de PARAMS_DEFECTO para no romperlos. OJO: esta
# copia se fija al importar el modulo, NO es "caliente" (si se cambia
# 'ventana_maxima' en senales_config.json despues de importar, estos tres
# ficheros no se enteran sin reiniciar su proceso) - dentro de este mismo
# modulo, detectar() SI usa cargar_params()['ventana_maxima'] en cada
# llamada, eso si es caliente de verdad.
VENTANA_MAXIMA = PARAMS_DEFECTO["ventana_maxima"]


def _ruta_config():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "senales_config.json")


_cache_params = None
_cache_mtime = None
_cache_verificado = 0.0  # time.monotonic() de la ultima comprobacion de mtime

# Minimo de segundos entre comprobaciones de mtime en disco (2026-08-17,
# bug real en vivo, ver mismo comentario en mercado/indicadores.py): el
# proyecto vive en un share de red (NAS), y detectar() se llama UNA VEZ POR
# VELA desde herramientas/backtest_senales.py (cientos de miles de velas en
# el historico completo de Binance) - sin este throttle, cada llamada a
# detectar() disparaba varios viajes de red (uno por cada _impulso/
# _aceleracion/_ruptura/_rechazo/_rsi_giro/detectar que resuelve un
# parametro a None), convirtiendo un backtest de segundos en uno de horas.
INTERVALO_RECOMPROBAR = 2.0


def cargar_params():
    """PARAMS_DEFECTO + lo que haya en senales_config.json (si existe) -
    cacheado por mtime del fichero (y el propio mtime solo se comprueba como
    mucho cada INTERVALO_RECOMPROBAR segundos, ver su comentario) para no
    releerlo/parsearlo en cada llamada a detectar()."""
    global _cache_params, _cache_mtime, _cache_verificado
    ahora = time.monotonic()
    if _cache_params is not None and (ahora - _cache_verificado) < INTERVALO_RECOMPROBAR:
        return _cache_params
    _cache_verificado = ahora

    ruta = _ruta_config()
    mtime = os.path.getmtime(ruta) if os.path.exists(ruta) else None
    if _cache_params is not None and mtime == _cache_mtime:
        return _cache_params

    params = dict(PARAMS_DEFECTO)
    if mtime is not None:
        try:
            with open(ruta) as f:
                guardado = json.load(f)
            params.update({k: v for k, v in guardado.items() if k in PARAMS_DEFECTO})
        except (OSError, ValueError):
            pass
    _cache_params, _cache_mtime = params, mtime
    return params


def guardar_params(params):
    """Escribe 'params' (solo claves conocidas de PARAMS_DEFECTO, dentro de
    LIMITES_PARAMS) en senales_config.json - pensado para
    telegram_control.py mas adelante (de momento nada lo llama todavia).
    Escritura atomica (tmp + os.replace), mismo patron que
    herramientas/grabador_libro.py._guardar_config /
    mercado/indicadores.py.guardar_params."""
    a_guardar = dict(cargar_params())
    for k, v in params.items():
        if k not in PARAMS_DEFECTO:
            continue
        lo, hi = LIMITES_PARAMS[k]
        if not (lo <= v <= hi):
            raise ValueError(f"{k}={v} fuera de rango [{lo},{hi}]")
        a_guardar[k] = v
    ruta = _ruta_config()
    tmp = ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump(a_guardar, f)
    os.replace(tmp, ruta)

# Grupo A (backtest offline contra niveles.py, ver
# herramientas/backtest_senales.py --tolerancia-atr/--toques-min): estas 7
# señales dan mejor edge cuando hay un nivel VIGENTE del tipo "contrario" a
# su propia direccion cerca (ej. ruptura_alza funciona mejor rompiendo una
# resistencia real que en espacio abierto) - justo lo opuesto de la
# intuicion ingenua de "nivel a mi favor cerca". Sin ese nivel util cerca,
# el backtest no les encontro borde real - por eso detectar() las descarta
# en vez de emitirlas tal cual cuando se le pasan niveles_vigentes.
#
# Backtest 2018-11-15 -> 2022-01-01, BTC y ETH, 15m/1h/4h, agregado desde
# 1m, niveles recalculados cada 30 dias (ver git log de backtest_senales.py
# para la corrida completa) - edge@30 CONFIRMADO (positivo en las 6
# combinaciones moneda/TF) solo en 4 de las 7:
#   ruptura_alza_en_resistencia, aceleracion_baja_en_soporte,
#   rechazo_max_en_soporte, ruptura_baja_en_soporte
# Las otras 3 mejoraron frente a su version sin filtrar pero siguen sin
# edge fiable (positivo en unas combinaciones, negativo en otras):
#   rsi_sobrecompra_en_soporte, rsi_sobreventa_en_resistencia,
#   div_bajista_en_soporte
# Es decir: el filtro de nivel es necesario pero NO suficiente para esas 3 -
# antes de darlas por buenas en vivo, probablemente les falte un filtro de
# regimen (tendencia neta del periodo, ver hallazgo de rsi_sobreventa
# cambiando de signo entre la ventana 2022-2026 alcista y la 2018-2022
# mixta) u otra vuelta de calibracion.
NIVEL_UTIL_GRUPO_A = {
    "ruptura_alza": "techo",
    "rsi_sobreventa": "techo",
    "ruptura_baja": "suelo",
    "rechazo_max": "suelo",
    "aceleracion_baja": "suelo",
    "div_bajista": "suelo",
    "rsi_sobrecompra": "suelo",
}
NOMBRE_REFINADO = {
    "ruptura_alza": "ruptura_alza_en_resistencia",
    "rsi_sobreventa": "rsi_sobreventa_en_resistencia",
    "ruptura_baja": "ruptura_baja_en_soporte",
    "rechazo_max": "rechazo_max_en_soporte",
    "aceleracion_baja": "aceleracion_baja_en_soporte",
    "div_bajista": "div_bajista_en_soporte",
    "rsi_sobrecompra": "rsi_sobrecompra_en_soporte",
}
# Edge@30 confirmado (positivo, consistente BTC+ETH, 15m/1h/4h) vs todavia
# en pruebas (mejoro pero no fiable aun) - ver comentario de arriba.
REFINADAS_CONFIRMADAS = {
    "ruptura_alza_en_resistencia", "aceleracion_baja_en_soporte",
    "rechazo_max_en_soporte", "ruptura_baja_en_soporte",
}
REFINADAS_EN_PRUEBAS = set(NOMBRE_REFINADO.values()) - REFINADAS_CONFIRMADAS


def _series(velas):
    aperturas = [v[1] for v in velas]
    altos = [v[2] for v in velas]
    bajos = [v[3] for v in velas]
    cierres = [v[4] for v in velas]
    return aperturas, altos, bajos, cierres


def _impulso(cierres, atr_actual, ventana=None, umbral=None):
    """'impulso_alza'/'impulso_baja': las ultimas 'ventana' velas se
    movieron, en conjunto (cierre actual contra el cierre de hace 'ventana'
    velas), mas de 'umbral'xATR en una direccion."""
    if ventana is None or umbral is None:
        params = cargar_params()
        if ventana is None:
            ventana = params["ventana_impulso"]
        if umbral is None:
            umbral = params["umbral_impulso_atr"]
    if atr_actual is None or atr_actual <= 0 or len(cierres) <= ventana:
        return None
    movimiento = cierres[-1] - cierres[-1 - ventana]
    if movimiento > umbral * atr_actual:
        return "alza"
    if movimiento < -umbral * atr_actual:
        return "baja"
    return None


def _aceleracion(aperturas, cierres, atr_actual, ventana=None,
                  umbral_atr=None, umbral_ritmo=None):
    """'aceleracion_alza'/'aceleracion_baja': la ULTIMA vela sola se movio
    (cierre-apertura) mas de 'umbral_atr'xATR Y mas de 'umbral_ritmo' veces
    el ritmo (movimiento absoluto medio cierre-apertura) de las 'ventana'
    velas previas a ella."""
    if ventana is None or umbral_atr is None or umbral_ritmo is None:
        params = cargar_params()
        if ventana is None:
            ventana = params["ventana_impulso"]
        if umbral_atr is None:
            umbral_atr = params["umbral_aceleracion_atr"]
        if umbral_ritmo is None:
            umbral_ritmo = params["umbral_aceleracion_ritmo"]
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


def _ruptura(altos, bajos, cierres, ventana=None):
    """'ruptura_alza'/'ruptura_baja': el CIERRE de la ultima vela rompe el
    maximo/minimo de las 'ventana' velas ANTERIORES (sin contar la propia)."""
    if ventana is None:
        ventana = cargar_params()["ventana_ruptura"]
    if len(cierres) <= ventana:
        return None
    max_previo = max(altos[-1 - ventana:-1])
    min_previo = min(bajos[-1 - ventana:-1])
    if cierres[-1] > max_previo:
        return "alza"
    if cierres[-1] < min_previo:
        return "baja"
    return None


def _rechazo(altos, bajos, cierres, ventana=None):
    """'rechazo_max'/'rechazo_min': la MECHA de la ultima vela pincha un
    nuevo extremo de las 'ventana' velas anteriores, pero el CIERRE se
    queda dentro de ese rango previo (mecha de rechazo, ruptura no
    confirmada). Devuelve una lista ("max"/"min", pueden darse ambas a la
    vez en una vela muy volatil)."""
    if ventana is None:
        ventana = cargar_params()["ventana_ruptura"]
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
    niveles.py) marca un nuevo extremo que el RSI NO confirma
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


def _rsi_giro(rsi_serie, sobrecompra=None, sobreventa=None):
    """'rsi_sobrecompra'/'rsi_sobreventa': RSI en zona extrema Y ya girando
    de vuelta hacia el centro (vela a vela, no solo tocando el umbral)."""
    if sobrecompra is None or sobreventa is None:
        params = cargar_params()
        if sobrecompra is None:
            sobrecompra = params["rsi_sobrecompra"]
        if sobreventa is None:
            sobreventa = params["rsi_sobreventa"]
    if len(rsi_serie) < 2 or rsi_serie[-1] is None or rsi_serie[-2] is None:
        return None
    actual, previo = rsi_serie[-1], rsi_serie[-2]
    if actual >= sobrecompra and actual < previo:
        return "sobrecompra"
    if actual <= sobreventa and actual > previo:
        return "sobreventa"
    return None


def detectar(velas, k, ventana_ruptura=None, niveles_vigentes=None, tolerancia_nivel=None):
    """Evalua las 12 señales sobre la ULTIMA vela de 'velas' (asumida
    cerrada). 'k' es el mismo parametro de extremos_locales que ya se usa
    para detectar niveles (ver niveles.py) - se reutiliza aqui para
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
    mismo en cada llamada.

    'niveles_vigentes' (lista de (precio, rol_efectivo) - ver
    herramientas/backtest_senales.py._niveles_vigentes) y 'tolerancia_nivel',
    si se dan, activan el filtro del Grupo A (ver NIVEL_UTIL_GRUPO_A): esas
    7 señales solo se devuelven (renombradas via NOMBRE_REFINADO) si hay
    cerca un nivel del tipo que de verdad les ayuda - si no, se descartan.
    El resto de señales no se ven afectadas. Sin 'niveles_vigentes' (caso
    por defecto), el comportamiento es el de siempre, sin filtrar nada."""
    if ventana_ruptura is None:
        ventana_ruptura = cargar_params()["ventana_ruptura"]
    limite = max(cargar_params()["ventana_maxima"], ventana_ruptura + 50, 4 * k + 50)
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

    if niveles_vigentes is None:
        return activas

    precio_actual = cierres[-1]
    refinadas = []
    for nombre in activas:
        tipo_util = NIVEL_UTIL_GRUPO_A.get(nombre)
        if tipo_util is None:
            refinadas.append(nombre)
            continue
        cerca_del_util = any(rol == tipo_util and abs(precio_actual - precio_nivel) <= tolerancia_nivel
                              for precio_nivel, rol in niveles_vigentes)
        if cerca_del_util:
            refinadas.append(NOMBRE_REFINADO[nombre])
    return refinadas
