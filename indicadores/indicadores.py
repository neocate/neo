
import math

def sma(cierres, periodo=20):
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


def rsi(cierres, periodo=14):
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


def bollinger_bands(cierres, periodo=20, desviaciones=2):
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


def atr(altos, bajos, cierres, periodo=14):
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

    s_tr = sum(tr[1:periodo + 1]) / periodo
    s_dm_mas = sum(dm_mas[1:periodo + 1]) / periodo
    s_dm_menos = sum(dm_menos[1:periodo + 1]) / periodo

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
    n = len(velas)
    if n < 2 * k + 1:
        return [], []
    
    altos, bajos = [], []
    
    for j in range(k, n - k):
        alto_j, bajo_j = velas[j][2], velas[j][3]
        
        es_alto = True
        es_bajo = True
        
        for i in range(j - k, j + k + 1):
            if i != j:
                if velas[i][2] > alto_j:
                    es_alto = False
                if velas[i][3] < bajo_j:
                    es_bajo = False
                if not es_alto and not es_bajo:
                    break
        
        if es_alto:
            altos.append(j)
        if es_bajo:
            bajos.append(j)
    
    return altos, bajos


def ultimo(indicador):
    if isinstance(indicador, dict):
        return {k: ultimo(v) for k, v in indicador.items()}

    for valor in reversed(indicador):
        if valor is not None:
            return valor
    return None
