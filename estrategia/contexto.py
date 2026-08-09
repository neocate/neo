# ---------------------------------------------------------------
#  estrategia/contexto.py - Consultas a OTROS timeframes para dar contexto
#  a la señal del propio TF: arbitro DI (desempate/veto), vecino rapido
#  (adelantar apertura/cierre) y regimen de fondo (SMA diaria).
#
#  Extraido de monitor.py el 2026-08-04 (ver estrategia/senales.py para el
#  motivo). Cada consulta se cachea por vela (coin, tf) -> no repite la
#  llamada a la API mientras esa vela no haya cerrado una nueva.
# ---------------------------------------------------------------

from mercado import datos, indicadores
from estrategia.senales import senales

_MINUTOS_TF = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
               "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720,
               "1d": 1440}


def _tf_minutos(tf):
    return _MINUTOS_TF.get(tf, 0)


# Auto-eleccion de --tf-arbitro cuando no se especifica uno explicito (ver
# default None en _parse_args). Tiene que ser MAS LENTO que --tf - un fijo
# "1h" para todo fallaba en los extremos: en --tf 4h quedaba MAS RAPIDO que
# lo operado, y en --tf 1h a la misma velocidad (sin aportar nada). Cada
# franja se lleva la siguiente parada natural, no cualquier TF mas lento.
_ARBITRO_AUTO = {
    "1m": "1h", "3m": "1h", "5m": "1h", "15m": "1h", "30m": "1h",
    "1h": "4h", "2h": "1d", "4h": "1d", "6h": "1d", "12h": "1d",
}


def _tf_arbitro_auto(tf):
    """TF arbitro por defecto para 'tf' (ver _ARBITRO_AUTO). '' si no hay
    ninguno mas lento definido (ej. tf ya es '1d') - equivale a desactivarlo,
    igual que pasar '-' a mano."""
    return _ARBITRO_AUTO.get(tf, "")


# Vecino RAPIDO (2026-08-02): al reves del arbitro, para "avisar antes" -
# siempre la MENOR tf disponible (5m), no el vecino inmediato en cadena.
# Probado con las dos: simulado sobre 10 dias de velas de 1m con el
# senales() real, 1h<-5m sale igual o mejor que 1h<-15m (mediana +11.0 vs
# +8.0 bps en BTC, +21.3 vs +19.4 en ETH, con mas muestra) - la cadena por
# vecino inmediato solo suma el retraso de la parada intermedia sin ganar
# nada. 4h<-5m/4h<-15m sin muestra suficiente en 10 dias pero mismo criterio.
# 5m no tiene vecino mas rapido en lo que se usa (1m se probo y se descarto
# por demasiado volatil, ver anotaciones.md 2026-08-04). Confirmado tambien
# contra datos reales de la sesion 03/04-ago: comparando las entradas de 1h
# contra la ventana de 5m (el vecino real) el gap mediana baja de -141.8 a
# +6.5 bps - casi identico al 5m puro.
_VECINO_RAPIDO_AUTO = {"15m": "5m", "1h": "5m", "4h": "5m"}


def _tf_vecino_auto(tf):
    """TF vecino rapido por defecto para 'tf' (ver _VECINO_RAPIDO_AUTO). ''
    si no hay ninguno mas rapido definido - equivale a desactivarlo."""
    return _VECINO_RAPIDO_AUTO.get(tf, "")


_CACHE_ARBITRO = {}       # (coin, tf) -> (timestamp_ultima_vela, veredicto)


def _arbitro(coin, cfg):
    """Consulta el TF ARBITRO (--tf-arbitro, automatico por defecto -ver
    _ARBITRO_AUTO-) para desempatar
    cuando las señales se contradicen. Devuelve un dict o None si falla.

    QUE se le pregunta y por que:
      - DI+ / DI- (direccion) es quien DECIDE. Discrimina de verdad: sobre 286
        velas reparte 54-64% / 36-46% en ETH y BTC, con separacion clara (>5
        puntos) en ~3 de cada 4 velas. Y el 2026-07-29 acerto los dos empates
        desde el primer momento (DI- 24.9 vs DI+ 15.7).
      - ADX (fuerza) se GRABA pero NO decide: ese mismo dia marcaba RANGO
        (17.6 y 18.5) en plena caida sostenida, o sea habria desempatado a
        favor del rebote, que es justo lo que salio mal. Se registra para
        poder medirlo con mas sesiones antes de darle o quitarle el papel.
      - La tendencia EMA20/50 NO se usa: en julio tardo 7.5h en girar tras el
        techo, y ese mismo dia seguia diciendo ALCISTA 3 horas despues de
        empezar la caida.

    Se cachea por vela: el 1h no cambia entre vueltas de 2 minutos.
    """
    tf = cfg["tf_arbitro"]
    if not tf:
        return None
    try:
        simbolo, _ = datos.normalizar_simbolo(coin, "f")
        velas = datos.velas(simbolo, tf, 120)
    except Exception:
        return None
    if len(velas) < 40:
        return None

    clave = (coin, tf)
    ultima = velas[-1][0]
    if clave in _CACHE_ARBITRO and _CACHE_ARBITRO[clave][0] == ultima:
        return _CACHE_ARBITRO[clave][1]

    r = indicadores.adx([v[2] for v in velas], [v[3] for v in velas],
                        [v[4] for v in velas], 14)
    # -2 = ultima vela CERRADA (la -1 se esta formando), igual que las señales
    a, dp, dm = r["adx"][-2], r["di_mas"][-2], r["di_menos"][-2]
    if dp is None or dm is None:
        return None
    separacion = abs(dp - dm)
    direccion = None
    if separacion >= cfg["di_separacion"]:
        direccion = "largo" if dp > dm else "corto"
    ver = {"tf": tf, "adx": a, "di_mas": dp, "di_menos": dm,
           "separacion": separacion, "direccion": direccion}
    _CACHE_ARBITRO[clave] = (ultima, ver)
    return ver


_CACHE_REGIMEN = {}       # (coin, tf) -> (timestamp_ultima_vela, veredicto)


def _regimen(coin, cfg):
    """Regimen de fondo: precio vs su SMA de --regimen-sma en --regimen-tf
    (1d por defecto). Devuelve 'alcista'/'bajista'/None (sin dato).

    NO es el arbitro (_arbitro, DI del TF inmediatamente mas lento) - es una
    señal mucho mas lenta (dias, no velas) y mide otra cosa (tendencia de
    fondo de semanas/meses, no humor de la vela anterior). Encontrado
    2026-08-03 auditando por que 'ruptura_alza' perdia con CUALQUIER gestion
    de salida probada (escalera, RR fijo, trailing ATR, scale-out): el año
    de backtest resulto ser un mercado bajista el 75% del tiempo (BTC -45%,
    ETH -47% en el año) - partido por trimestres, largo SOLO ganaba en el
    unico trimestre alcista. El DI del arbitro no discriminaba nada (~87%
    vs ~91% cubre comision); esta SMA si: alineado con la direccion del
    trade +0.146%/trade vs -0.087% en contra (RR 1:3, n=965, ver
    anotaciones.md). Probado tambien un cruce de dos SMA (10/50, 20/50,
    20/100): peor discriminacion que esta comparacion simple precio-vs-SMA,
    el retraso extra de esperar a que las dos medias crucen diluye la señal.
    """
    tf = cfg["regimen_tf"]
    if not tf:
        return None
    try:
        simbolo, _ = datos.normalizar_simbolo(coin, "f")
        velas = datos.velas(simbolo, tf, cfg["regimen_sma"] + 5)
    except Exception:
        return None
    if len(velas) < cfg["regimen_sma"] + 2:
        return None

    clave = (coin, tf)
    ultima = velas[-1][0]
    if clave in _CACHE_REGIMEN and _CACHE_REGIMEN[clave][0] == ultima:
        return _CACHE_REGIMEN[clave][1]

    cierres = [v[4] for v in velas]
    sma = indicadores.sma(cierres, cfg["regimen_sma"])
    # -2 = ultima vela CERRADA (la -1 se esta formando), igual que el arbitro
    if sma[-2] is None:
        return None
    veredicto = "alcista" if cierres[-2] > sma[-2] else "bajista"
    _CACHE_REGIMEN[clave] = (ultima, veredicto)
    return veredicto


_CACHE_VECINO = {}        # (coin, tf) -> (timestamp_ultima_vela, resultado)


def _vecino_senales(coin, cfg):
    """Consulta el TF VECINO RAPIDO (--tf-vecino-rapido, automatico por
    defecto - ver _VECINO_RAPIDO_AUTO) para intentar abrir/cerrar ANTES de
    que cierre la propia vela, cuando ese vecino ya muestra la misma señal
    de continuacion. Devuelve un dict (senales/alto_c/bajo_c/atr/velas) o
    None si esta desactivado o falla. Cacheado por vela del vecino, igual
    que _arbitro().

    Reusa senales() tal cual sobre las velas del vecino - no es una version
    aparte, es la MISMA funcion que usa leer() para el propio tf."""
    tf = cfg["tf_vecino_rapido"]
    if not tf:
        return None
    try:
        simbolo, _ = datos.normalizar_simbolo(coin, "f")
        velas = datos.velas(simbolo, tf, 200)
    except Exception:
        return None
    if len(velas) < cfg["ventana"] + 2:
        return None

    clave = (coin, tf)
    ultima = velas[-1][0]
    if clave in _CACHE_VECINO and _CACHE_VECINO[clave][0] == ultima:
        return _CACHE_VECINO[clave][1]

    cierres = [v[4] for v in velas]
    rsi_serie = indicadores.rsi(cierres, 14)
    sen, alto_c, bajo_c, rsi_c = senales(velas, rsi_serie, cfg)
    serie_atr = indicadores.atr([v[2] for v in velas], [v[3] for v in velas],
                                cierres, 14)
    valor_atr = serie_atr[-2] if len(serie_atr) >= 2 else None

    ver = {"tf": tf, "senales": sen, "alto_c": alto_c, "bajo_c": bajo_c,
           "atr": valor_atr, "velas": velas}
    _CACHE_VECINO[clave] = (ultima, ver)
    return ver
