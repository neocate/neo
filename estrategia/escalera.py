# ---------------------------------------------------------------
#  estrategia/escalera.py - Nivel objetivo de trailing (estructura) y
#  trailing por giveback del pico (2026-08-04, salida por defecto en
#  libre/veto - ver anotaciones.md), mas el calculo del stop inicial.
#
#  Extraido de monitor.py el 2026-08-04 (ver estrategia/senales.py para el
#  motivo). Funciones puras salvo _aplicar_trailing_giveback, que muta el
#  'pos' que recibe (misma firma que antes, movida sin reimplementar para
#  poder verificarla contra datos historicos reales sin duplicar codigo).
# ---------------------------------------------------------------

from mercado import indicadores


def _proximo_nivel(velas, atr, precio, lado, cfg, distancia_min=0.0):
    """Busca el SIGUIENTE soporte/resistencia PROBADO por delante del precio,
    en la direccion FAVORABLE de 'lado' (resistencia arriba para largo,
    soporte abajo para corto). Ventana de --escalera-ventana velas CERRADAS,
    excluyendo la de señal y la que se esta formando (misma convencion que
    FiltroSoporte, estrategia/filtros.py). 'Probado' = al menos
    --escalera-toques velas de la ventana pasaron dentro de
    --escalera-tolerancia-atr x ATR de el (mismo criterio de 'toque' que
    FiltroSoporte.evaluar, para no inventar una segunda definicion). Los
    candidatos son SWING highs/lows (indicadores.extremos_locales), no
    cualquier vela: si no, la vela de hace 5 minutos, a un par de bps del
    precio sin que este se haya alejado y vuelto, cuenta como 'nivel' - que
    no es estructura, es ruido de la ultima hora.

    'distancia_min' (en precio, no bps) descarta candidatos mas cerca que eso
    -para el objetivo de la escalera, que exige un minimo de --escalera-min-atr
    x ATR (ver _objetivo_escalera); el filtro de ENTRADA sigue llamando con
    distancia_min=0, busca el mas cercano que sea, para compararlo aparte
    contra el punto muerto.

    Devuelve (nivel, distancia_bps) del PRIMERO que cumple - el mas cercano -
    o (None, None) si no hay ninguno probado en esa direccion."""
    ventana = cfg["escalera_ventana"]
    if not atr or len(velas) < ventana + 2:
        return None, None
    tol = atr * cfg["escalera_tolerancia_atr"]
    if tol <= 0:
        return None, None
    historico = velas[-(ventana + 2):-2]
    if not historico:
        return None, None

    altos_idx, bajos_idx = indicadores.extremos_locales(historico)
    if lado == "largo":
        candidatos = sorted(historico[j][2] for j in altos_idx
                             if historico[j][2] > precio + distancia_min)
    else:
        candidatos = sorted((historico[j][3] for j in bajos_idx
                              if historico[j][3] < precio - distancia_min),
                             reverse=True)

    for nivel in candidatos:
        toques = 0
        dentro = False
        for v in historico:
            alto, bajo = v[2], v[3]
            cerca = (bajo - tol) <= nivel <= (alto + tol)
            if cerca and not dentro:
                toques += 1
                dentro = True
            elif not cerca:
                dentro = False
        if toques >= cfg["escalera_toques"]:
            return nivel, abs(nivel - precio) / precio * 1e4
    return None, None


def _aplicar_trailing_giveback(pos, precio, vela_form, cfg):
    """Ajusta pos.stop/pos.pico_precio con el trailing por giveback del pico
    (2026-08-04, ver anotaciones.md). Se arma cuando la ganancia supera
    cfg["trailing_armado_atr"] x ATR de la vela de señal; desde ahi el stop
    persigue el pico dejando solo cfg["trailing_giveback"] del recorrido
    ganado entre el stop y el maximo (nunca empeora el stop). Extraida a
    funcion aparte (no inline en _revisar) para poder verificarla contra
    datos historicos reales sin reimplementarla en un script de analisis -
    devuelve True si el stop se movio esta vuelta."""
    mejor_intravuelta = (max(precio, vela_form[2]) if pos.lado == "largo"
                          else min(precio, vela_form[3]))
    if pos.lado == "largo":
        pos.pico_precio = max(pos.pico_precio, mejor_intravuelta)
        ganancia = pos.pico_precio - pos.entrada
    else:
        pos.pico_precio = min(pos.pico_precio, mejor_intravuelta)
        ganancia = pos.entrada - pos.pico_precio
    if ganancia < cfg["trailing_armado_atr"] * pos.atr_entrada:
        return False
    candidato = (pos.pico_precio - cfg["trailing_giveback"] * ganancia
                 if pos.lado == "largo"
                 else pos.pico_precio + cfg["trailing_giveback"] * ganancia)
    nuevo_stop = (max(pos.stop, candidato) if pos.lado == "largo"
                  else min(pos.stop, candidato))
    if nuevo_stop == pos.stop:
        return False
    pos.stop = nuevo_stop
    pos.stop_origen = f"trailing@{pos.stop:.4f}"
    return True


def _objetivo_escalera(velas, atr, precio, lado, cfg):
    """Objetivo de la escalera de trailing: el siguiente nivel PROBADO que
    ademas respete --escalera-min-atr x ATR de distancia (VETO_TF.md sec. 3 -
    con menos margen que eso, el objetivo no compensa el punto muerto mas el
    ruido normal de precio, y ratchear ahi te expulsa por nada).

    Si no hay ninguno tan lejos (o no hay estructura), el objetivo de reserva
    es ese mismo multiplo de ATR desde el precio actual - la version 'siempre
    hay un proximo escalon' aunque no haya estructura real ahi.

    Devuelve el PRECIO objetivo (float) o None si no hay ATR."""
    if not atr:
        return None
    minimo = atr * cfg["escalera_min_atr"]
    nivel, _ = _proximo_nivel(velas, atr, precio, lado, cfg, distancia_min=minimo)
    if nivel is not None:
        return nivel
    return precio + minimo if lado == "largo" else precio - minimo


def _calcular_stop(lado, m, cfg, motivo=None):
    """Devuelve (precio_stop, origen). El stop es el extremo de la vela de la
    señal SEPARADO ademas --stop-atr veces el ATR.

    Por que no el extremo crudo: el riesgo por operacion salia arbitrario -en
    una vela pequeña el stop quedaba a 5 bps y en una grande a 80, sin razon
    de mercado-, y con 29 bps de MAE medio medido en julio los trades morian
    por ruido, no porque la tesis se rompiera. Con el colchon en ATR el riesgo
    es homogeneo entre monedas y temporalidades, que ademas es condicion para
    que el sizing signifique algo.

    'motivo' en ("impulso_alza", "impulso_baja") (2026-08-04): el extremo NO
    es el de la vela de señal sola, es el del TRAMO completo de impulso
    (--impulso-lookback velas) - fiel al backtest que valida este stop (ver
    estrategia/senales.py, SENALES_CONTINUACION): durante varias velas
    seguidas moviendose en la misma direccion, el extremo de una sola vela
    queda demasiado pegado al precio, un stop ahi no sobrevive al ruido
    normal de una racha ya en marcha.
    """
    if motivo in ("impulso_alza", "impulso_baja"):
        velas = m["velas"]
        lookback = cfg.get("impulso_lookback", 4)
        # velas[-1] es la que se esta formando, velas[-2] la de señal - el
        # tramo son esa y las 'lookback' anteriores, sin incluir la en formacion.
        inicio = max(0, len(velas) - 2 - lookback)
        tramo = velas[inicio:-1]
        extremo = min(v[3] for v in tramo) if lado == "largo" else max(v[2] for v in tramo)
        origen_base = "extremo_tramo"
    else:
        extremo = m["bajo_c"] if lado == "largo" else m["alto_c"]
        origen_base = "extremo_vela"
    atr = m.get("atr")
    if not atr or cfg["stop_atr"] <= 0:
        return extremo, origen_base
    colchon = atr * cfg["stop_atr"]
    stop = extremo - colchon if lado == "largo" else extremo + colchon
    return stop, f"{origen_base}+{cfg['stop_atr']}xATR"
