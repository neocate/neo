# ---------------------------------------------------------------
#  estrategia/senales.py - Deteccion de señales sobre velas cerradas
#
#  Extraido de monitor.py el 2026-08-04 para separar la deteccion de señales
#  del resto de la orquestacion (ver anotaciones.md). Funciones PURAS,
#  sin llamadas de red ni estado de modulo: dado (velas, rsi_serie, cfg) o
#  un conjunto de claves de señal, siempre devuelven lo mismo.
# ---------------------------------------------------------------

from mercado import indicadores

# Direccion que sugiere cada tipo de señal (para la posicion de papel).
SENALES_LARGO = {"ruptura_alza", "rechazo_min", "div_alcista", "rsi_sobreventa",
                  "impulso_alza", "aceleracion_alza"}
SENALES_CORTO = {"ruptura_baja", "rechazo_max", "div_bajista", "rsi_sobrecompra",
                  "impulso_baja", "aceleracion_baja"}

# Solo estas ABREN posicion (ver 'claves_apertura' en _revisar). Las demas
# siguen pudiendo CERRAR una posicion contraria ya abierta via
# _direccion(nuevas_claves), solo dejan de poder iniciar una nueva.
#
# impulso_alza/baja SUSTITUYE a ruptura_alza/baja aqui (2026-08-04, ver
# anotaciones.md) - ruptura sigue calculandose y sigue en SENALES_LARGO/
# CORTO (puede cerrar una posicion contraria, igual que rechazo/div/rsi
# ahora), pero ya no abre. Motivo: reconstruyendo con velas REALES de
# Bitget (10 dias, BTC+ETH, 5m/15m/1h) donde de verdad rompia ruptura_* con
# ventana=30, se vio que el precio YA se habia movido +22 a +28 bps de
# media en los 30 minutos previos, y ese recorrido se devuelve casi
# siempre (retorno a 30 min: -1.6 a -2.9 bps en TODOS los tamaños de
# ventana probados, 10 a 40 - no es un problema de calibracion). impulso_*
# dispara antes, sobre el momentum reciente en vez de la ruptura de un
# rango de 30 velas: mismo backtest, MFE algo menor (+11 a +14 bps) pero
# retorno a 30 min POSITIVO por primera vez en toda la exploracion de hoy.
# Simulando la operacion COMPLETA (stop en el extremo del tramo de
# impulso, comision real, el trailing por giveback) sobre un barrido
# lookback x umbral (2D, 7x6 combinaciones): la columna umbral=3.0xATR
# fue positiva en TODOS los lookback probados (2 a 10 velas), la unica sin
# ninguna celda negativa - eso es lo que se eligio en su momento (lookback=4
# como punto intermedio, no el pico lookback=2/K=3.0). Sin validacion en
# vivo todavia, solo backtest historico.
#
# RECALIBRADO 2026-08-07 con backtest de ROBUSTEZ real (55 ventanas de 60
# dias, 2017-2026 - ver IMPULSO_LOOKBACK/IMPULSO_MIN_ATR mas abajo y el
# comentario junto a cfg["impulso_lookback"] en monitor.py): el lookback=4
# de arriba ya no es el default - se bajo a 3 (con min_atr=2.5) por dar
# 100% de ventanas ganadoras en 15m, a cambio de ceder un poco del pico de
# 1h (que sigue ganando el 100% de las ventanas de todos modos).
#
# aceleracion_alza/baja se suma aqui el 2026-08-05 (ver conversacion): NO
# sustituye a impulso_alza/baja, corre EN PARALELO para poder comparar en el
# CSV cual entra antes y cual paga mas ruido. Motivo: impulso_* exige que el
# movimiento ya se haya ACUMULADO en --impulso-lookback velas, así que por
# construccion dispara tarde en el tramo - visto en vivo el 2026-08-04
# 23:15-23:18 UTC en BTC: entradas en el ultimo tercio de un pico que
# revirtio 40 min despues. aceleracion_* mide si la ULTIMA vela cerrada,
# ella sola, rompe el ritmo de las anteriores (ver senales(), mas abajo).
# Ya tiene backtest de robustez tambien (2026-08-07, mismo barrido que
# impulso_* - ver ACELERACION_MIN_ATR mas abajo).
SENALES_CONTINUACION = {"impulso_alza", "impulso_baja", "aceleracion_alza", "aceleracion_baja"}

# Defaults del umbral de impulso - ver PARAMS_AJUSTABLES en monitor.py para
# la descripcion completa y como se ajusta en caliente. Recalibrados
# 2026-08-07 (ver comentario junto a cfg["impulso_lookback"] en monitor.py):
# 100% de ventanas ganadoras en 15m y en 1h sobre 55 ventanas de 60 dias
# (2017-2026), a cambio de un pico algo menor en 1h que con el valor viejo
# (lookback=4/min_atr=3.0, que seguia siendo el optimo puro de 1h solo).
IMPULSO_LOOKBACK = 3
IMPULSO_MIN_ATR = 2.5

# Defaults de aceleracion (ver SENALES_CONTINUACION arriba). ACELERACION_VENTANA
# incluye la vela de disparo: con el default (6) se compara la ultima vela
# cerrada contra el ritmo medio de las 5 anteriores. ACELERACION_MIN_ATR es
# mas bajo que IMPULSO_MIN_ATR a proposito: aqui solo se exige que UNA vela
# sea significativa, no el acumulado de --impulso-lookback: exigir un umbral
# tan alto como el de impulso a una sola vela casi nunca dispara.
# ACELERACION_MULT es cuantas veces el ritmo previo tiene que ser la vela de
# disparo - el filtro de que sea una ACELERACION real, no solo una vela de
# tamaño normal en un mercado ya volatil.
#
# ACELERACION_MIN_ATR recalibrado 2026-08-07 (0.8 -> 1.2, mismo backtest de
# robustez que impulso): a diferencia de impulso, este valor mejora en las
# CUATRO franjas sin excepcion - ver comentario junto a
# cfg["aceleracion_min_atr"] en monitor.py.
ACELERACION_VENTANA = 6
ACELERACION_MIN_ATR = 1.2
ACELERACION_MULT = 2.5


def _tendencia(cierres):
    """Etiqueta de tendencia comparando EMA20 vs EMA50 (en el tf del monitor)."""
    ema20 = indicadores.ultimo(indicadores.ema(cierres, 20))
    ema50 = indicadores.ultimo(indicadores.ema(cierres, 50))
    if ema20 is None or ema50 is None:
        return "?"
    if ema20 > ema50:
        return "ALCISTA"
    if ema20 < ema50:
        return "BAJISTA"
    return "PLANA"


def senales(velas, rsi_serie, cfg):
    """Detecta senales de posible cambio de precio en la ULTIMA VELA CERRADA.

    Devuelve (lista_señales, alto_c, bajo_c, rsi_c). La lista es de (clave,
    texto); alto_c/bajo_c son los extremos de la vela cerrada (para el stop de
    la posicion de papel); rsi_c es el RSI de esa MISMA vela cerrada - se
    devuelve para que leer() lo reutilice tal cual en el campo "rsi" del
    estado, en vez de recalcular por su cuenta el RSI de la vela EN FORMACION
    (indicadores.ultimo(rsi_serie)): esa vela no es la que deciden las señales,
    y mostrar/grabar un RSI distinto del que disparo rsi_sobrecompra/
    sobreventa confunde al leer la consola o el CSV. Estructura de vela:
    [timestamp,open,high,low,close,vol]. velas[-1] es la vela AUN
    FORMANDOSE -> se ignora, se trabaja sobre velas[-2].
    """
    n = cfg["ventana"]
    if len(velas) < n + 2:
        return [], None, None, None

    cerrada = velas[-2]
    _, _, alto_c, bajo_c, cierre_c, _ = cerrada

    # Rango de las n velas ANTERIORES a la cerrada (sin incluirla):
    prior = velas[-(n + 2):-2]
    highs = [v[2] for v in prior]
    lows = [v[3] for v in prior]
    max_prev = max(highs)
    min_prev = min(lows)

    # RSI alineado con las velas: rsi de la cerrada y de la de antes.
    rsi_c = rsi_serie[-2] if len(rsi_serie) >= 2 else None
    rsi_ant = rsi_serie[-3] if len(rsi_serie) >= 3 else None

    out = []

    # --- Ruptura / rechazo en el MAXIMO del rango ---
    if alto_c > max_prev:
        if cierre_c >= max_prev:
            out.append(("ruptura_alza",
                        f"RUPTURA AL ALZA: cierre {cierre_c:.4f} rompe el maximo "
                        f"de {n} velas ({max_prev:.4f}). Posible impulso alcista."))
        else:
            out.append(("rechazo_max",
                        f"RECHAZO EN MAXIMO: pincho {alto_c:.4f} (nuevo max) pero "
                        f"cerro dentro en {cierre_c:.4f}. Posible reversion BAJISTA."))

    # --- Ruptura / rechazo en el MINIMO del rango ---
    if bajo_c < min_prev:
        if cierre_c <= min_prev:
            out.append(("ruptura_baja",
                        f"RUPTURA A LA BAJA: cierre {cierre_c:.4f} rompe el minimo "
                        f"de {n} velas ({min_prev:.4f}). Posible impulso bajista."))
        else:
            out.append(("rechazo_min",
                        f"RECHAZO EN MINIMO: pincho {bajo_c:.4f} (nuevo min) pero "
                        f"cerro dentro en {cierre_c:.4f}. Posible reversion ALCISTA."))

    # --- Divergencias RSI (precio hace extremo, RSI no lo confirma) ---
    if rsi_c is not None:
        rsi_prior = rsi_serie[-(n + 2):-2]
        if len(rsi_prior) == len(highs):
            j_max = max(range(len(highs)), key=lambda k: highs[k])
            j_min = min(range(len(lows)), key=lambda k: lows[k])
            rsi_en_max_prev = rsi_prior[j_max]
            rsi_en_min_prev = rsi_prior[j_min]
            if alto_c > max_prev and rsi_en_max_prev is not None and rsi_c < rsi_en_max_prev:
                out.append(("div_bajista",
                            f"DIVERGENCIA BAJISTA: precio hace nuevo maximo pero RSI "
                            f"({rsi_c:.1f}) no supera al del maximo anterior "
                            f"({rsi_en_max_prev:.1f}). Posible reversion BAJISTA."))
            if bajo_c < min_prev and rsi_en_min_prev is not None and rsi_c > rsi_en_min_prev:
                out.append(("div_alcista",
                            f"DIVERGENCIA ALCISTA: precio hace nuevo minimo pero RSI "
                            f"({rsi_c:.1f}) no baja del minimo anterior "
                            f"({rsi_en_min_prev:.1f}). Posible reversion ALCISTA."))

    # --- Agotamiento: RSI en extremo y GIRANDO ---
    if rsi_c is not None and rsi_ant is not None:
        if rsi_c >= cfg["rsi_alto"] and rsi_c < rsi_ant:
            out.append(("rsi_sobrecompra",
                        f"RSI EN SOBRECOMPRA girando a la baja ({rsi_ant:.1f}->{rsi_c:.1f}). "
                        f"Agotamiento; posible reversion BAJISTA."))
        if rsi_c <= cfg["rsi_bajo"] and rsi_c > rsi_ant:
            out.append(("rsi_sobreventa",
                        f"RSI EN SOBREVENTA girando al alza ({rsi_ant:.1f}->{rsi_c:.1f}). "
                        f"Agotamiento; posible reversion ALCISTA."))

    # --- Impulso: movimiento neto de las ultimas N velas cerradas supera
    # K x ATR (2026-08-04, ver SENALES_CONTINUACION arriba - sustituye a
    # ruptura_alza/baja como unica familia que ABRE). ATR calculado sobre
    # TODA la serie de velas (igual que leer() hace para "atr_pct"), tomando
    # el indice -2 para alinearlo con la vela CERRADA - misma convencion que
    # el resto de esta funcion, no una segunda definicion de ATR.
    lookback = cfg.get("impulso_lookback", IMPULSO_LOOKBACK)
    if len(velas) >= lookback + 16:  # margen para que ATR(14) tenga sentido
        atrs = indicadores.atr([v[2] for v in velas], [v[3] for v in velas],
                                [v[4] for v in velas], 14)
        atr_c = atrs[-2] if len(atrs) >= 2 else None
        if atr_c and len(velas) >= lookback + 2:
            cierre_prev = velas[-2 - lookback][4]
            mov = cierre_c - cierre_prev
            umbral = cfg.get("impulso_min_atr", IMPULSO_MIN_ATR)
            if abs(mov) > umbral * atr_c:
                veces_atr = abs(mov) / atr_c
                if mov > 0:
                    out.append(("impulso_alza",
                                f"IMPULSO ALCISTA: {lookback} velas se movieron "
                                f"{mov:+.4f} ({veces_atr:.1f}x ATR). Momentum "
                                f"sostenido, posible continuacion."))
                else:
                    out.append(("impulso_baja",
                                f"IMPULSO BAJISTA: {lookback} velas se movieron "
                                f"{mov:+.4f} ({veces_atr:.1f}x ATR). Momentum "
                                f"sostenido, posible continuacion."))

    # --- Aceleracion: la ULTIMA vela cerrada, ella sola, rompe el ritmo de
    # las anteriores (2026-08-05, ver SENALES_CONTINUACION arriba - dispara
    # antes que impulso_alza/baja en el mismo tramo, en vez de esperar a que
    # el movimiento se acumule en --impulso-lookback velas). Ventana propia
    # (--aceleracion-ventana), independiente de la de impulso.
    ventana_ac = cfg.get("aceleracion_ventana", ACELERACION_VENTANA)
    if len(velas) >= ventana_ac + 16:  # mismo margen que impulso para el ATR(14)
        atrs_ac = indicadores.atr([v[2] for v in velas], [v[3] for v in velas],
                                   [v[4] for v in velas], 14)
        atr_ac = atrs_ac[-2] if len(atrs_ac) >= 2 else None
        cierres_ventana = [velas[-2 - ventana_ac + i][4] for i in range(ventana_ac + 1)]
        movs = [cierres_ventana[i + 1] - cierres_ventana[i] for i in range(ventana_ac)]
        mov_ultimo = movs[-1]
        previos = movs[:-1]
        ritmo_previo = sum(abs(m) for m in previos) / len(previos) if previos else 0.0
        min_atr_ac = cfg.get("aceleracion_min_atr", ACELERACION_MIN_ATR)
        mult_ac = cfg.get("aceleracion_mult", ACELERACION_MULT)
        # ritmo_previo == 0 (mercado plano antes de esta vela): cualquier
        # movimiento que ya pase el minimo de ATR cuenta como arranque, no
        # hay "ritmo" contra el que comparar el multiplo.
        if (atr_ac and abs(mov_ultimo) >= min_atr_ac * atr_ac
                and (ritmo_previo == 0 or abs(mov_ultimo) >= mult_ac * ritmo_previo)):
            veces_ritmo = (abs(mov_ultimo) / ritmo_previo) if ritmo_previo else float("inf")
            veces_ritmo_txt = f"{veces_ritmo:.1f}x" if ritmo_previo else "arranque desde plano,"
            if mov_ultimo > 0:
                out.append(("aceleracion_alza",
                            f"ACELERACION ALCISTA: la ultima vela se movio "
                            f"{mov_ultimo:+.4f} ({veces_ritmo_txt} el ritmo de las "
                            f"{ventana_ac - 1} anteriores). Posible arranque de impulso."))
            else:
                out.append(("aceleracion_baja",
                            f"ACELERACION BAJISTA: la ultima vela se movio "
                            f"{mov_ultimo:+.4f} ({veces_ritmo_txt} el ritmo de las "
                            f"{ventana_ac - 1} anteriores). Posible arranque de impulso."))

    return out, alto_c, bajo_c, rsi_c


def _direccion(claves):
    """Direccion neta de un conjunto de señales nuevas: 'largo'/'corto'/None
    (None tambien si hay señales de las dos direcciones a la vez = ambiguo).

    OJO, esto NO es neutral: las señales son de dos familias que se
    contradicen POR CONSTRUCCION. En una caida con fuerza el precio rompe el
    minimo (ruptura_baja, continuacion) Y el RSI se va a sobreventa
    (rsi_sobreventa/div_alcista, reversion) a la vez. Al anularse, el sistema
    se queda quieto justo en el movimiento fuerte y solo opera despues, cuando
    queda la señal de reversion sola -> sesgo sistematico a operar CONTRA el
    movimiento. Medido el 2026-07-29 en ETH 5m: las 5 vueltas con señal de
    corto tenian tambien una de largo, no se abrio ni un corto en toda una
    sesion bajista, y los 7 trades fueron largos. Ver _ambiguedad().
    """
    largo = any(k in SENALES_LARGO for k in claves)
    corto = any(k in SENALES_CORTO for k in claves)
    if largo and not corto:
        return "largo"
    if corto and not largo:
        return "corto"
    return None


def _ambiguedad(claves):
    """'' si no hay conflicto; si lo hay, las señales enfrentadas.

    Los momentos en que el sistema decide NO actuar eran invisibles en el CSV
    y resulta que son los importantes: hay que poder contarlos antes de
    decidir a quien darle prioridad."""
    largo = sorted(k for k in claves if k in SENALES_LARGO)
    corto = sorted(k for k in claves if k in SENALES_CORTO)
    if largo and corto:
        return f"{'+'.join(largo)} vs {'+'.join(corto)}"
    return ""


# NOTA: aqui vivio un "enfriamiento tras stop" (no reabrir en la misma zona
# hasta recuperar el nivel roto). Se RETIRO el 2026-07-29 al ver que el veto
# por DI lo hace innecesario: las 5 reentradas en cadena de esa sesion eran
# todas largos CONTRA el DI, asi que con el veto ninguna se habria abierto y
# no habia nada que enfriar. El enfriamiento atacaba el sintoma (reentrar
# mucho) con una banda en ATR inventada; el DI ataca la causa (operar contra
# el movimiento). Queda en el historial de git por si se retoma.
