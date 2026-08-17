import json
import math
import os
import time

PARAMS_DEFECTO = {
    "sma_periodo": 20,
    "ema_periodo": 12,
    "rsi_periodo": 14,
    "bb_periodo": 20, "bb_desviaciones": 2,
    "atr_periodo": 14,
    "adx_periodo": 14,
    "rvol_periodo": 20,
    "extremos_k": 3,
    "macd_rapido": 12, "macd_lento": 26, "macd_senal": 9,
}

LIMITES_PARAMS = {
    "sma_periodo": (1, 500),
    "ema_periodo": (1, 500),
    "rsi_periodo": (1, 500),
    "bb_periodo": (1, 500), "bb_desviaciones": (0.1, 10.0),
    "atr_periodo": (1, 500),
    "adx_periodo": (1, 500),
    "rvol_periodo": (1, 500),
    "extremos_k": (1, 100),
    "macd_rapido": (1, 500), "macd_lento": (1, 500), "macd_senal": (1, 500),
}


def _ruta_config():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "indicadores_config.json")


_cache_params = None
_cache_mtime = None
_cache_verificado = 0.0

INTERVALO_RECOMPROBAR = 2.0


def cargar_params():
    """cargar_params() -> dict, PARAMS_DEFECTO + indicadores_config.json (si existe)"""
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
    """guardar_params(params: dict) -> None. Escribe en indicadores_config.json, valida contra LIMITES_PARAMS."""
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


def sma(cierres, periodo=None):
    """sma(cierres: list[float], periodo: int|None = None) -> list[float|None]"""
    if periodo is None:
        periodo = cargar_params()["sma_periodo"]
    n = len(cierres)
    if n < periodo or periodo <= 0:
        return [None] * n

    resultado = [None] * (periodo - 1)
    suma_ventana = sum(cierres[:periodo])
    resultado.append(suma_ventana / periodo)

    for i in range(periodo, n):
        suma_ventana += cierres[i] - cierres[i - periodo]
        resultado.append(suma_ventana / periodo)

    return resultado


def ema(cierres, periodo=None):
    """ema(cierres: list[float], periodo: int|None = None) -> list[float|None]"""
    if periodo is None:
        periodo = cargar_params()["ema_periodo"]
    n = len(cierres)
    if n < periodo or periodo <= 0:
        return [None] * n

    multiplicador = 2 / (periodo + 1)
    resultado = [None] * (periodo - 1)

    ema_actual = sum(cierres[:periodo]) / periodo
    resultado.append(ema_actual)

    for i in range(periodo, n):
        ema_actual = (cierres[i] - ema_actual) * multiplicador + ema_actual
        resultado.append(ema_actual)

    return resultado


def rsi(cierres, periodo=None):
    """rsi(cierres: list[float], periodo: int|None = None) -> list[float|None] en [0, 100]"""
    if periodo is None:
        periodo = cargar_params()["rsi_periodo"]
    n = len(cierres)
    if n < periodo + 1 or periodo <= 0:
        return [None] * n

    resultado = [None] * periodo

    ganancia_acum = 0.0
    perdida_acum = 0.0
    for i in range(1, periodo + 1):
        diff = cierres[i] - cierres[i - 1]
        if diff > 0:
            ganancia_acum += diff
        else:
            perdida_acum += abs(diff)

    avg_gain = ganancia_acum / periodo
    avg_loss = perdida_acum / periodo

    if avg_loss == 0:
        resultado.append(100.0 if avg_gain > 0 else 0.0)
    else:
        rs = avg_gain / avg_loss
        resultado.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(periodo + 1, n):
        diff = cierres[i] - cierres[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = abs(diff) if diff < 0 else 0.0

        avg_gain = (avg_gain * (periodo - 1) + gain) / periodo
        avg_loss = (avg_loss * (periodo - 1) + loss) / periodo

        if avg_loss == 0:
            rsi_val = 100.0 if avg_gain > 0 else 0.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100.0 - (100.0 / (1.0 + rs))

        resultado.append(rsi_val)

    return resultado


def bollinger_bands(cierres, periodo=None, desviaciones=None):
    """bollinger_bands(cierres, periodo=None, desviaciones=None) -> {'superior', 'media', 'inferior'}"""
    if periodo is None or desviaciones is None:
        params = cargar_params()
        if periodo is None:
            periodo = params["bb_periodo"]
        if desviaciones is None:
            desviaciones = params["bb_desviaciones"]
    n = len(cierres)
    if n < periodo or periodo <= 0:
        return {
            'superior': [None] * n,
            'media': [None] * n,
            'inferior': [None] * n,
        }

    media = sma(cierres, periodo)
    superior = [None] * n
    inferior = [None] * n

    suma_v = sum(cierres[:periodo])
    suma_sq_v = sum(x ** 2 for x in cierres[:periodo])

    for i in range(periodo - 1, n):
        if i >= periodo:
            sale = cierres[i - periodo]
            entra = cierres[i]
            suma_v += entra - sale
            suma_sq_v += entra ** 2 - sale ** 2

        mean = media[i]
        varianza = max(0.0, (suma_sq_v / periodo) - (mean ** 2))
        std = math.sqrt(varianza)

        superior[i] = mean + desviaciones * std
        inferior[i] = mean - desviaciones * std

    return {
        'superior': superior,
        'media': media,
        'inferior': inferior,
    }


def atr(altos, bajos, cierres, periodo=None):
    """atr(altos, bajos, cierres, periodo=None) -> list[float|None]"""
    if periodo is None:
        periodo = cargar_params()["atr_periodo"]
    n = len(cierres)
    resultado = [None] * n
    if n < periodo + 1 or periodo <= 0:
        return resultado

    tr = [0.0] * n
    tr[0] = altos[0] - bajos[0]
    for i in range(1, n):
        h, l, pc = altos[i], bajos[i], cierres[i - 1]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    atr_actual = sum(tr[1:periodo + 1]) / periodo
    resultado[periodo] = atr_actual

    for i in range(periodo + 1, n):
        atr_actual = (atr_actual * (periodo - 1) + tr[i]) / periodo
        resultado[i] = atr_actual

    return resultado


def adx(altos, bajos, cierres, periodo=None):
    """adx(altos, bajos, cierres, periodo=None) -> {'adx', 'di_mas', 'di_menos'}"""
    if periodo is None:
        periodo = cargar_params()["adx_periodo"]
    n = len(cierres)
    resultado = {
        'adx': [None] * n,
        'di_mas': [None] * n,
        'di_menos': [None] * n,
    }
    if n < 2 * periodo or periodo <= 0:
        return resultado

    tr = [0.0] * n
    dm_mas = [0.0] * n
    dm_menos = [0.0] * n

    for i in range(1, n):
        subida = altos[i] - altos[i - 1]
        bajada = bajos[i - 1] - bajos[i]
        dm_mas[i] = subida if (subida > bajada and subida > 0) else 0.0
        dm_menos[i] = bajada if (bajada > subida and bajada > 0) else 0.0
        h, l, pc = altos[i], bajos[i], cierres[i - 1]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    s_tr = sum(tr[1:periodo + 1])
    s_dm_mas = sum(dm_mas[1:periodo + 1])
    s_dm_menos = sum(dm_menos[1:periodo + 1])

    dx = [None] * n

    def _di_dx(idx, s_tr, s_dm_mas, s_dm_menos):
        di_mas = 100 * s_dm_mas / s_tr if s_tr != 0 else 0.0
        di_menos = 100 * s_dm_menos / s_tr if s_tr != 0 else 0.0
        resultado['di_mas'][idx] = di_mas
        resultado['di_menos'][idx] = di_menos
        suma = di_mas + di_menos
        return 100 * abs(di_mas - di_menos) / suma if suma != 0 else 0.0

    dx[periodo] = _di_dx(periodo, s_tr, s_dm_mas, s_dm_menos)

    for i in range(periodo + 1, n):
        s_tr = s_tr - s_tr / periodo + tr[i]
        s_dm_mas = s_dm_mas - s_dm_mas / periodo + dm_mas[i]
        s_dm_menos = s_dm_menos - s_dm_menos / periodo + dm_menos[i]
        dx[i] = _di_dx(i, s_tr, s_dm_mas, s_dm_menos)

    idx_primer = 2 * periodo - 1
    adx_actual = sum(dx[periodo:2 * periodo]) / periodo
    resultado['adx'][idx_primer] = adx_actual

    for i in range(idx_primer + 1, n):
        adx_actual = (adx_actual * (periodo - 1) + dx[i]) / periodo
        resultado['adx'][i] = adx_actual

    return resultado


def macd(cierres, rapido=None, lento=None, senal=None):
    """macd(cierres, rapido=None, lento=None, senal=None) -> {'macd', 'senal', 'histograma'}"""
    if rapido is None or lento is None or senal is None:
        params = cargar_params()
        if rapido is None:
            rapido = params["macd_rapido"]
        if lento is None:
            lento = params["macd_lento"]
        if senal is None:
            senal = params["macd_senal"]

    n = len(cierres)
    vacio = {'macd': [None] * n, 'senal': [None] * n, 'histograma': [None] * n}
    if n < lento or rapido <= 0 or lento <= 0 or senal <= 0:
        return vacio

    ema_rapida = ema(cierres, rapido)
    ema_lenta = ema(cierres, lento)
    linea_macd = [
        (er - el) if (er is not None and el is not None) else None
        for er, el in zip(ema_rapida, ema_lenta)
    ]

    primer_valido = next((i for i, v in enumerate(linea_macd) if v is not None), None)
    if primer_valido is None or (n - primer_valido) < senal:
        return {'macd': linea_macd, 'senal': [None] * n, 'histograma': [None] * n}

    ema_senal_sub = ema(linea_macd[primer_valido:], senal)
    linea_senal = [None] * primer_valido + ema_senal_sub

    histograma = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(linea_macd, linea_senal)
    ]

    return {'macd': linea_macd, 'senal': linea_senal, 'histograma': histograma}


def rvol(volumenes, periodo=None):
    """rvol(volumenes: list[float], periodo: int|None = None) -> list[float|None]"""
    if periodo is None:
        periodo = cargar_params()["rvol_periodo"]
    n = len(volumenes)
    if n < periodo + 1 or periodo <= 0:
        return [None] * n

    resultado = [None] * periodo
    suma_ventana = sum(volumenes[:periodo])

    for i in range(periodo, n):
        media = suma_ventana / periodo
        resultado.append(volumenes[i] / media if media > 0 else None)
        suma_ventana += volumenes[i] - volumenes[i - periodo]

    return resultado


def extremos_locales(velas, k=None):
    """extremos_locales(velas: list[[ts,o,h,l,c,vol]], k: int|None = None) -> (indices_altos, indices_bajos)"""
    if k is None:
        k = cargar_params()["extremos_k"]
    n = len(velas)
    altos, bajos = [], []
    for j in range(k, n - k):
        alto_j, bajo_j = velas[j][2], velas[j][3]
        vecinos_altos = [velas[x][2] for x in range(j - k, j + k + 1) if x != j]
        vecinos_bajos = [velas[x][3] for x in range(j - k, j + k + 1) if x != j]
        if alto_j >= max(vecinos_altos):
            altos.append(j)
        if bajo_j <= min(vecinos_bajos):
            bajos.append(j)
    return altos, bajos


def ultimo(indicador):
    """ultimo(indicador: list | dict[str, list]) -> float | dict"""
    if isinstance(indicador, dict):
        return {k: ultimo(v) for k, v in indicador.items()}

    for valor in reversed(indicador):
        if valor is not None:
            return valor
    return None
