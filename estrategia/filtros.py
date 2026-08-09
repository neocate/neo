# ---------------------------------------------------------------
# filtros.py - Filtros de validacion externa para monitor.py (extraido de
# monitor.py el 2026-08-04).
#
# La mayoria son SOMBRA: junto a cada señal que el monitor SI ejecuta en
# papel, dejan constancia de si ELLOS habrian ejecutado tambien, y con que
# valor - se puede comparar el PnL real de las operaciones donde el filtro
# decia SI contra las que decia NO, sin perder muestra. volatilidad y volumen
# (RVOL, solo en aceleracion_*) son la excepcion: ya se promovieron a VETO
# real en monitor.py (cfg["volatilidad_veta"]/cfg["rvol_veta"]) tras
# backtest de escala - ver sus docstrings.
#
# 2026-08-1x: se eliminaron FiltroSoporte, FiltroBollinger y FiltroBTC -
# backtest_filtros_combinados.py (9 años BTC+ETH) los encontro empeorando
# el resultado SIN EXCEPCION si se promovian a veto (ver docstring de
# posicion.py:_abrir), y como filtros sombra la pregunta que median ya esta
# contestada con esos datos - seguir grabandolos no aporta nada nuevo, solo
# gasta una consulta de red extra (FiltroBTC pedia velas de BTC aparte en
# cada intento de apertura). FiltroCVD se suma en su lugar (sombra, sin
# backtest de escala todavia - ver su docstring).
#
# Interfaz: .nombre (str) y .evaluar(lado, ctx) -> (veredicto_bool_o_None, valor)
#   lado: "largo" | "corto"
#   ctx:  {'velas': [...], 'ventana': int, 'funding_pct', 'oi_serie', 'cvd_serie'}
#   veredicto None = sin datos suficientes (no cuenta como SI ni NO)
# ---------------------------------------------------------------

from mercado.indicadores import atr


class FiltroVolatilidad:
    """ATR de la vela de señal, como % del precio.

    TESIS: una ruptura/rechazo en un mercado con volatilidad muy baja (ATR%
    diminuto) es ruido de rango estrecho, no un movimiento real - el propio
    ATR marca el "suelo" de lo operable en cada temporalidad. Recalibrado
    2026-08-02: el umbral 0.3 nunca disparaba (0/30 True en datos reales,
    todo TF-mezclado, ATR% real observado 0.067-0.254, mediana 0.123) -
    era mas alto que el maximo observado, un filtro constante no discrimina
    nada. Se baja a la mediana real para que de verdad separe casos.
    """

    nombre = "volatilidad"

    def __init__(self, periodo=14, umbral_pct=0.12):
        self.periodo = periodo
        self.umbral_pct = umbral_pct

    def evaluar(self, lado, ctx):
        velas = ctx['velas']
        if len(velas) < self.periodo + 3:
            return None, None
        altos = [v[2] for v in velas]
        bajos = [v[3] for v in velas]
        cierres = [v[4] for v in velas]
        serie = atr(altos, bajos, cierres, self.periodo)
        valor_atr = serie[-2]                 # ATR de la vela CERRADA de la señal
        cierre_c = cierres[-2]
        if valor_atr is None or not cierre_c:
            return None, None
        atr_pct = valor_atr / cierre_c * 100
        return atr_pct >= self.umbral_pct, atr_pct


class FiltroVolumenSenal:
    """RVOL de la vela que disparo la señal (volumen / media de las previas).

    TESIS: cualquiera de las señales del monitor (ruptura, rechazo,
    divergencia, agotamiento) es mas fiable si hubo participacion real
    detras - poco volumen = pocos traders detras del movimiento, mas
    facil que sea ruido.
    """

    nombre = "volumen"

    def __init__(self, periodo=20, umbral=1.0):
        self.periodo = periodo
        self.umbral = umbral

    def evaluar(self, lado, ctx):
        velas = ctx['velas']
        if len(velas) < self.periodo + 2:
            return None, None
        senal = velas[-2]
        previas = velas[-(self.periodo + 2):-2]
        if not previas:
            return None, None
        media = sum(v[5] for v in previas) / len(previas)
        if media <= 0:
            return None, None
        rvol = senal[5] / media
        return rvol >= self.umbral, rvol


class FiltroFunding:
    """Funding rate ACTUAL del contrato perpetuo (% por periodo, 8h en
    Bitget - ver mercado/datos.py:funding_rate).

    TESIS: el funding es posicionamiento agregado de TODO el mercado de
    derivados - un eje distinto al precio (TA) o al libro (microestructura),
    no derivado de ninguno de los dos. Funding muy positivo = ya hay muchos
    largos pagando a los cortos (long-crowded); una ruptura_alza en ese
    contexto empuja a los ULTIMOS en subirse, mas riesgo de que sea el
    final del impulso (posible squeeze en contra) que el principio.
    Simetrico para corto con funding muy negativo (short-crowded).
    Recalibrado 2026-08-02, mismo dia que se instrumento: el umbral inicial
    (0.03) se puso a ciegas y resulto ser el mismo error que ya tuvo
    volatilidad - descargado el historico real (fetch_funding_rate_history,
    ~90 dias, 270 asentamientos BTC+ETH via descargar_funding.py) el maximo
    observado es 0.01 EN LAS DOS MONEDAS, el mismo valor exacto - indicio de
    que Bitget aplica un TECHO a este contrato (no que el mercado nunca se
    caliente mas). 0.03 nunca se podia cumplir: 0/540 asentamientos lo
    superan. Umbral nuevo en la mediana-alta real (percentil ~70): 0.007.
    """

    nombre = "funding"

    def __init__(self, umbral_pct=0.007):
        self.umbral_pct = umbral_pct   # % por periodo de funding

    def evaluar(self, lado, ctx):
        funding_pct = ctx.get('funding_pct')
        if funding_pct is None:
            return None, None
        saturado = (lado == "largo" and funding_pct >= self.umbral_pct) or \
                   (lado == "corto" and funding_pct <= -self.umbral_pct)
        return (not saturado), funding_pct


class FiltroOpenInterest:
    """Cambio de open interest sobre una ventana de velas cerradas.

    TESIS: una ruptura respaldada por OI CRECIENTE es dinero NUEVO entrando
    (conviccion real, posiciones nuevas); una ruptura con OI plano o
    decreciente es mas probable que sea cierre de posiciones contrarias
    (short covering / liquidacion de largos) que un movimiento nuevo - no
    deberia durar igual. No depende del lado: crecimiento de OI confirma
    para largo Y para corto por igual (participacion nueva en cualquier
    direccion), a diferencia de FiltroFunding, que si distingue.

    Bitget/ccxt NO ofrece historial de open interest (solo el valor
    instantaneo) - por eso 'serie' no se pide en vivo, se lee de
    flujo_<fecha>_<monedas>.csv (herramientas/grabador_libro.py, proceso
    APARTE que si lo graba en vivo sin cortarse con los reinicios de
    monitor.py - ver mercado/lectura.py:_open_interest_serie). [] si ese
    proceso no esta corriendo: el filtro se abstiene (None), no inventa.
    Umbral de partida SIN VALIDAR, igual que FiltroFunding.
    """

    nombre = "open_interest"

    def __init__(self, umbral_pct=0.0):
        self.umbral_pct = umbral_pct   # 0.0 = confirma con cualquier OI creciente

    def evaluar(self, lado, ctx):
        serie = ctx.get('oi_serie')
        ventana = ctx.get('ventana', 30)
        if not serie or len(serie) < ventana + 1:
            return None, None
        oi_ref = serie[-(ventana + 1)][1]
        oi_actual = serie[-1][1]
        if not oi_ref:
            return None, None
        cambio_pct = (oi_actual - oi_ref) / oi_ref * 100
        return cambio_pct >= self.umbral_pct, cambio_pct


class FiltroCVD:
    """Tendencia del CVD (order flow acumulado - ver mercado/flujo.py y
    herramientas/grabador_libro.py) sobre la ventana de señal, leido de
    flujo_*.csv (ver mercado/lectura.py:_serie_flujo, misma fuente que
    FiltroOpenInterest).

    TESIS, con evidencia de UN caso real medido (ETH 2026-08-07/08, ver
    conversacion): sobre las rupturas de nivel de esa noche, el IMBALANCE
    puntual del libro (una foto de segundos) no distinguio las que
    continuaron de las que revirtieron - practicamente ruido. La TENDENCIA
    del CVD sostenida en las velas previas si coincidio con la ruptura de
    mejor continuacion de todo el set. Sin backtest de escala todavia (a
    diferencia de RVOL/volatilidad, que si lo tienen antes de promoverse a
    veto real - ver herramientas/backtest_rvol_filtro.py y
    backtest_filtros_combinados.py) - arranca en SOMBRA, mismo tratamiento
    que tuvo RVOL antes de su backtest de 2026-08-07.

    A diferencia de FiltroOpenInterest (variacion en %, sobre una base
    estable de cientos de miles de contratos), el CVD es un ACUMULADOR que
    cruza cero - aqui se compara la DIRECCION del cambio contra 'lado', un
    % no significa nada con una base que puede ser negativa.
    """

    nombre = "cvd"

    def evaluar(self, lado, ctx):
        serie = ctx.get('cvd_serie')
        ventana = ctx.get('ventana', 30)
        if not serie or len(serie) < ventana + 1:
            return None, None
        cvd_ref = serie[-(ventana + 1)][1]
        cvd_actual = serie[-1][1]
        if cvd_ref is None or cvd_actual is None:
            return None, None
        delta = cvd_actual - cvd_ref
        confirma = (delta > 0) if lado == "largo" else (delta < 0)
        return confirma, delta


# ---------------------------------------------------------------
# Uso especifico de monitor.py (extraido de monitor.py el 2026-08-04):
# volatilidad y volumen son VETO real (ver cfg["volatilidad_veta"]/
# ["rvol_veta"] en monitor.py); funding/open_interest/cvd son sombra -
# junto a cada señal que el monitor SI ejecuta en papel, dejan constancia de
# si ELLOS habrian ejecutado tambien. Se contrastan offline contra el PnL
# real antes de considerar promoverlos.
# ---------------------------------------------------------------

FILTROS = [FiltroVolatilidad(), FiltroVolumenSenal(), FiltroFunding(),
           FiltroOpenInterest(), FiltroCVD()]


def _evaluar_filtros(lado, m, cfg):
    """Corre los filtros de validacion externa sobre el contexto de la
    señal. Devuelve {nombre: (veredicto_o_None, valor_o_None)}."""
    ctx = {"velas": m["velas"], "ventana": cfg["ventana"], "atr": m.get("atr"),
           "funding_pct": m.get("funding_pct"), "oi_serie": m.get("oi_serie"),
           "cvd_serie": m.get("cvd_serie")}
    return {f.nombre: f.evaluar(lado, ctx) for f in FILTROS}


def _texto_filtros(veredictos):
    """Linea corta para consola: volatilidad=SI(0.42) volumen=NO(0.7) cvd=?(None)."""
    partes = []
    for nombre, (ok, valor) in veredictos.items():
        marca = "SI" if ok else ("NO" if ok is False else "?")
        valor_txt = f"{valor:.2f}" if valor is not None else "-"
        partes.append(f"{nombre}={marca}({valor_txt})")
    return " ".join(partes)
