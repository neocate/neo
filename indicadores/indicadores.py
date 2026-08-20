# ---------------------------------------------------------------
# indicadores.py - Capa 2: Indicadores técnicos parametrizables
# Versión Fusionada: Optimizaciones O(N) + Análisis Estructural
# ---------------------------------------------------------------

import math

def sma(cierres, periodo=20):
    """Simple Moving Average (SMA) - Optimizado O(N).

    Calcula la media aritmética de los precios en una ventana dada.
    Utiliza un algoritmo de suma flotante para mantener la eficiencia.

    Args:
        cierres: list[float] (lista de precios de cierre)
        periodo: int (ventana de cálculo, default 20)

    Returns:
        list[float]: SMA para cada punto (None para puntos sin suficiente historia)
    """
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


def ema(cierres, periodo=12):
    """Exponential Moving Average (EMA).

    Media móvil que otorga más peso a los datos recientes. 
    Se inicializa con una SMA de los primeros 'periodo' puntos.

    Args:
        cierres: list[float]
        periodo: int (default 12)

    Returns:
        list[float]: EMA para cada punto
    """
    n = len(cierres)
    if n < periodo or periodo <= 0:
        return [None] * n

    multiplicador = 2 / (periodo + 1)
    resultado = [None] * (periodo - 1)
    
    # Base inicial: SMA
    ema_actual = sum(cierres[:periodo]) / periodo
    resultado.append(ema_actual)

    # Suavizado exponencial
    for i in range(periodo, n):
        ema_actual = (cierres[i] - ema_actual) * multiplicador + ema_actual
        resultado.append(ema_actual)

    return resultado


def rsi(cierres, periodo=14):
    """Relative Strength Index (RSI) con Suavizado de Wilder O(N).

    Mide la velocidad y el cambio de los movimientos de precios.
    Implementa el suavizado acumulativo de Wilder para mayor eficiencia.

    Args:
        cierres: list[float]
        periodo: int (default 14)

    Returns:
        list[float]: RSI 0-100 para cada punto
    """
    n = len(cierres)
    if n < periodo + 1 or periodo <= 0:
        return [None] * n

    resultado = [None] * periodo

    # Ganancias y pérdidas iniciales (primeros 'periodo' cambios)
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

    # Resto de puntos usando la fórmula de suavizado acumulativo de Wilder
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


def bollinger_bands(cierres, periodo=20, desviaciones=2):
    """Bollinger Bands: media ± desviaciones estándar - Optimizado O(N).

    Utiliza un algoritmo de suma flotante para la varianza, permitiendo
    calcular las bandas en tiempo lineal independientemente del periodo.

    Args:
        cierres: list[float]
        periodo: int (ventana SMA, default 20)
        desviaciones: float (multiplicador de std, default 2)

    Returns:
        dict: {
            'superior': list[float],
            'media': list[float],
            'inferior': list[float]
        }
    """
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

    # Algoritmo de suma flotante para varianza en O(N)
    suma_v = sum(cierres[:periodo])
    suma_sq_v = sum(x ** 2 for x in cierres[:periodo])

    for i in range(periodo - 1, n):
        if i >= periodo:
            sale = cierres[i - periodo]
            entra = cierres[i]
            suma_v += entra - sale
            suma_sq_v += entra ** 2 - sale ** 2

        mean = media[i]
        # max(0.0, ...) previene errores numéricos de precisión que den varianza negativa mínima
        varianza = max(0.0, (suma_sq_v / periodo) - (mean ** 2))
        std = math.sqrt(varianza)

        superior[i] = mean + desviaciones * std
        inferior[i] = mean - desviaciones * std

    return {
        'superior': superior,
        'media': media,
        'inferior': inferior,
    }


def atr(altos, bajos, cierres, periodo=14):
    """Average True Range (ATR): mide volatilidad con Suavizado de Wilder O(N).

    Sirve para fijar stops y objetivos dinámicos proporcionales a la volatilidad.

    Args:
        altos, bajos, cierres: list[float] (mismo largo)
        periodo: int (default 14)

    Returns:
        list[float]: ATR alineado con las velas
    """
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


def adx(altos, bajos, cierres, periodo=14):
    """Average Directional Index (ADX) + DI+ / DI-.

    ADX mide la fuerza de la tendencia, mientras que DI+ y DI- dan la dirección.
    Implementa el suavizado de Wilder para todos sus componentes.

    Args:
        altos, bajos, cierres: list[float] (una por vela)
        periodo: int (default 14)

    Returns:
        dict: {'adx': [...], 'di_mas': [...], 'di_menos': [...]}
    """
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
        s_tr = (s_tr * (periodo - 1) + tr[i]) / periodo
        s_dm_mas = (s_dm_mas * (periodo - 1) + dm_mas[i]) / periodo
        s_dm_menos = (s_dm_menos * (periodo - 1) + dm_menos[i]) / periodo
        dx[i] = _di_dx(i, s_tr, s_dm_mas, s_dm_menos)

    idx_primer = 2 * periodo - 1
    adx_actual = sum(dx[periodo:2 * periodo]) / periodo
    resultado['adx'][idx_primer] = adx_actual

    for i in range(idx_primer + 1, n):
        adx_actual = (adx_actual * (periodo - 1) + dx[i]) / periodo
        resultado['adx'][i] = adx_actual

    return resultado


def rvol(volumenes, periodo=20):
    """RVOL (Relative Volume): volumen de cada vela sobre la media de las
    'periodo' velas anteriores (sin incluir la propia) - Optimizado O(N).

    RVOL >= 1 = volumen por encima de lo normal para esa ventana. Se calcula
    de las velas ya descargadas (tienen histórico completo) - no necesita
    captura en vivo, a diferencia del libro/OI/CVD (ver
    herramientas/grabador_libro.py).

    Args:
        volumenes: list[float] (volumen de cada vela, mismo orden que velas)
        periodo: int (velas anteriores a promediar, default 20)

    Returns:
        list[float]: RVOL alineado con 'volumenes' (None sin suficiente
        historia, o si la media de la ventana da 0)
    """
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


def extremos_locales(velas, k=3):
    """Identifica Swing Highs y Swing Lows para análisis estructural.

    Un swing high es una vela cuyo alto domina a las k velas de cada lado.
    Ayuda a identificar soportes y resistencias reales filtrando el ruido.

    Args:
        velas: list de [timestamp, open, high, low, close, vol]
        k: velas vecinas a cada lado que debe dominar (default 3)

    Returns:
        (indices_altos, indices_bajos): list[int], indices dentro de 'velas'
    """
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
    """Devuelve el último valor válido (no None) de un indicador.

    Args:
        indicador: list[float] o dict con lists

    Returns:
        float o dict: último valor válido
    """
    if isinstance(indicador, dict):
        return {k: ultimo(v) for k, v in indicador.items()}

    for valor in reversed(indicador):
        if valor is not None:
            return valor
    return None
