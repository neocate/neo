import json
import os
import time

from mercado import indicadores

PARAMS_DEFECTO = {
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

VENTANA_MAXIMA = PARAMS_DEFECTO["ventana_maxima"]


def _ruta_config():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "senales_config.json")


_cache_params = None
_cache_mtime = None
_cache_verificado = 0.0

INTERVALO_RECOMPROBAR = 2.0


def cargar_params():
    """cargar_params() -> dict, PARAMS_DEFECTO + senales_config.json (si existe)"""
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
    """guardar_params(params: dict) -> None. Escribe en senales_config.json, valida contra LIMITES_PARAMS."""
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
    """_impulso(cierres, atr_actual, ventana=None, umbral=None) -> 'alza'|'baja'|None"""
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
    """_aceleracion(aperturas, cierres, atr_actual, ventana=None, umbral_atr=None, umbral_ritmo=None) -> 'alza'|'baja'|None"""
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
    """_ruptura(altos, bajos, cierres, ventana=None) -> 'alza'|'baja'|None"""
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
    """_rechazo(altos, bajos, cierres, ventana=None) -> list['max'|'min']"""
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
    """_divergencias(velas, altos, bajos, rsi_serie, k) -> list['bajista'|'alcista']"""
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
    """_rsi_giro(rsi_serie, sobrecompra=None, sobreventa=None) -> 'sobrecompra'|'sobreventa'|None"""
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
    """detectar(velas, k, ventana_ruptura=None, niveles_vigentes: list[(precio, rol)]|None = None, tolerancia_nivel: float|None = None) -> list[str]"""
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
