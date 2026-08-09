# ---------------------------------------------------------------
#  posicion/posicion.py - PosicionSim (posicion de papel) y OrdenPendiente
#  (orden limite entre la señal y la posicion), mas su ciclo de vida:
#  abrir, cerrar, y las dos consultas al libro (favorece / continuacion).
#
#  Extraido de monitor.py el 2026-08-04 (ver estrategia/senales.py para el
#  motivo).
# ---------------------------------------------------------------

from datetime import datetime, timezone

from mercado import datos, flujo
from estrategia.senales import SENALES_LARGO
from estrategia.escalera import _calcular_stop, _objetivo_escalera, _aplicar_trailing_giveback
from estrategia.filtros import _evaluar_filtros, _texto_filtros


class PosicionSim:
    """Posicion de PAPEL (sin dinero real) abierta por una señal del monitor.

    lado: "largo" o "corto". stop: precio que invalida la tesis de la señal
    (extremo de la vela que disparo la entrada). El capital de la moneda se
    actualiza (compone) al cerrar con el P&L realizado - ver `capitales`.
    """

    def __init__(self, lado, entrada, cantidad, margen, nocional, stop, motivo, hora,
                 id_op=None, veredictos=None, tasa_entrada=0.0, tasa_salida=0.0):
        self.lado = lado
        self.entrada = entrada
        self.cantidad = cantidad
        self.margen = margen
        self.nocional = nocional
        self.stop = stop
        self.motivo = motivo
        self.hora = hora
        self.imb_racha = 0
        self.imb_avisada = False
        self.id = id_op
        self.veredictos = veredictos or {}    # {nombre_filtro: (veredicto, valor)}
        # Objetivo de la escalera de trailing (ver _objetivo_escalera). None
        # si no hay ATR al abrir - degrada a el stop fijo de siempre, sin
        # trailing, en vez de inventar un numero.
        self.objetivo = None
        # Trailing por giveback del pico (2026-08-04, ver anotaciones.md y
        # PARAMS_AJUSTABLES["trailing_giveback"]): mejor precio alcanzado a
        # favor y ATR de la vela de señal, para perseguir el precio dejando
        # solo una fraccion del recorrido ganado entre el stop y el pico.
        self.pico_precio = entrada
        self.atr_entrada = None
        # Filtro sombra "estancamiento" (log-only, no cierra nada - ver
        # diagnostico.py, misma logica portada aqui para grabarla EN VIVO en
        # vez de reconstruirla despues). mfe_pct/mfe_dt: mejor pnl_neto_pct
        # visto hasta ahora y cuando, para medir cuanto tiempo lleva sin
        # superar su propio maximo.
        self.apertura_dt = datetime.now(timezone.utc)
        self.mfe_pct = None
        self.mfe_dt = None
        # 'limite' o 'mercado' - de que tipo fue la entrada (ver _abrir). None
        # hasta que _abrir() lo rellene.
        self.entrada_tipo = None
        # Comisiones en TANTO POR UNO (0.0006 = taker de Bitget). La de entrada
        # ya esta pagada al abrir; la de salida se estima al precio actual.
        self.tasa_entrada = tasa_entrada
        self.tasa_salida = tasa_salida
        self.comision_entrada = nocional * tasa_entrada

    def pnl(self, precio):
        """(pnl_usdt, pnl_%_sobre_margen) BRUTO al precio actual (solo precio)."""
        bruto = (precio - self.entrada) if self.lado == "largo" else (self.entrada - precio)
        usdt = bruto * self.cantidad
        pct = usdt / self.margen * 100 if self.margen else 0.0
        return usdt, pct

    def comision_salida(self, precio):
        """Comision que costaria cerrar AHORA (se paga sobre el nocional de
        salida, que no es el de entrada si el precio se ha movido)."""
        return abs(self.cantidad) * precio * self.tasa_salida

    def coste_total(self, precio):
        """Las DOS comisiones: la de entrada (pagada) + la de cerrar ahora."""
        return self.comision_entrada + self.comision_salida(precio)

    def pnl_neto(self, precio):
        """(pnl_usdt, pnl_%_sobre_margen) NETO: lo que quedaria de verdad si se
        cerrase ahora. Es la cifra que decide si una señal vale algo."""
        usdt, _ = self.pnl(precio)
        neto = usdt - self.coste_total(precio)
        pct = neto / self.margen * 100 if self.margen else 0.0
        return neto, pct

    def punto_muerto(self, precio):
        """Cuanto tiene que moverse el precio (en bps) solo para no perder."""
        if not self.nocional:
            return 0.0
        return self.coste_total(precio) / self.nocional * 1e4

    def stop_tocado(self, precio):
        return precio <= self.stop if self.lado == "largo" else precio >= self.stop

    def liquidacion_aprox(self):
        """Estimacion GRUESA (ignora fees/funding/margen de mantenimiento):
        precio al que el margen quedaria a 0 solo por el movimiento de precio."""
        lev = self.nocional / self.margen if self.margen else 0
        if lev <= 0:
            return None
        return self.entrada * (1 - 1 / lev) if self.lado == "largo" else self.entrada * (1 + 1 / lev)


class OrdenPendiente:
    """Orden LIMITE puesta al mejor bid/ask, esperando a que el precio la
    REBASE (no solo la toque - si solo la toca hay cola por delante en ese
    mismo precio, ver VETO_TF.md sec. 5). Vive entre "llega la señal" y "se
    abre la posicion" (o se cancela / escala a mercado); no es una posicion
    -sin riesgo, sin PnL- por eso no vive dentro de PosicionSim.

    'vueltas' cuenta cuantas veces se ha comprobado sin resolverse - se usa
    SOLO para decidir la escalada a mercado en la primera vuelta siguiente a
    colocarla (confluencia>=2); con confluencia simple se queda esperando,
    hasta que se llene, la cancele una señal contraria, o expire por
    --orden-max-velas (ver 'creada_dt' y VETO_TF.md: se vieron esperas de
    hasta 3.6h en 15m sin ninguna de las dos - para cuando llenaba, el
    contexto de la ruptura original ya estaba viejo).

    'creada_dt' es la hora REAL (UTC, con fecha) en que se coloco, para medir
    cuanto lleva esperando en minutos reales - independiente de --cada y de
    cuantas vueltas hayan pasado, que varian con la config. 'hora' sigue
    siendo el string corto (HH:MM:SS) que ya se mostraba en CSV/consola, no
    se toca para no romper ese formato.

    'señal' guarda la foto (alto_c/bajo_c/atr/velas) de la vela que
    disparo la señal, capturada AQUI, al colocar la orden - no en la vuelta en
    que se llena. Sin esto, _abrir() calculaba el stop y los filtros sombra
    sobre la vela cerrada de la vuelta de LLENADO, que puede ser una vela
    distinta (y sin relacion con la señal) si la orden tarda en llenarse y de
    por medio cierra una vela nueva."""

    def __init__(self, lado, precio_limite, motivo, confluencia, hora, id_op, señal):
        self.lado = lado
        self.precio_limite = precio_limite
        self.motivo = motivo
        self.confluencia = confluencia
        self.hora = hora
        self.creada_dt = datetime.now(timezone.utc)
        self.id = id_op
        self.vueltas = 0
        self.señal = señal


def _abrir(coin, cfg, m, motivo, capitales, nuevas_claves, señal=None, precio_entrada=None,
           entrada_tipo="mercado", confluencia_override=None, rechazo=None):
    """Abre una posicion de PAPEL. Devuelve la PosicionSim.

    'rechazo': lista opcional (mutada in-place) donde se anota el motivo
    corto si NO se abre (hoy solo "rvol") - para que quien llama (monitor.py)
    pueda distinguir la razon sin cambiar el contrato de "None = no abrio"
    que ya usan otros callers (backtests). None (default) = no se registra
    nada, mismo comportamiento de siempre.

    'precio_entrada' None = entra al precio actual (mercado). Si se pasa (una
    orden limite que se acaba de llenar), se usa ESE precio como entrada, no
    el tick actual - pueden diferir. 'entrada_tipo' decide la comision de
    ENTRADA: 'limite' = maker, 'mercado' = taker (la de SALIDA siempre es
    taker, un stop se ejecuta a mercado - ver VETO_TF.md sec. 5).
    'confluencia_override': cuando la entrada viene de una OrdenPendiente que
    se llena varias vueltas despues, 'nuevas_claves' de ESTA vuelta ya no son
    las de cuando nacio la señal (esas ya se "vieron" y no vuelven a estar en
    nuevas) - se pasa la confluencia real, calculada al COLOCAR la orden.
    'señal': la foto (alto_c/bajo_c/atr/velas) de la vela que
    disparo la señal (ver OrdenPendiente.señal); el stop y los filtros sombra
    se calculan sobre ESA vela, no sobre 'm' (que en un llenado tardio es la
    vuelta ACTUAL, con otra vela cerrada ya de por medio). None = usa 'm' -
    apertura directa sin OrdenPendiente por delante."""
    lado = "largo" if motivo in SENALES_LARGO else "corto"
    precio = precio_entrada if precio_entrada is not None else m["precio"]
    base = señal if señal is not None else m
    capital = capitales[coin]
    margen = capital * cfg["fraccion_entrada"]
    nocional = margen * cfg["leverage"]
    cantidad = nocional / precio if precio else 0.0
    stop, stop_origen = _calcular_stop(lado, base, cfg, motivo)

    # Guarda de cordura (31-jul): si la misma señal reabre varias veces
    # dentro de la MISMA vela (senales() no cambia hasta que cierra una vela
    # nueva - ver docstring de senales()), el stop se calcula sobre un
    # extremo que puede haber quedado obsoleto si el precio ya se movio mas
    # alla de el. Visto en vivo: 3 reaperturas seguidas de 'rechazo_min' con
    # el MISMO stop=63094.62 mientras el precio caia de 63278 a 63016 - la
    # tercera abria un largo con el stop 78 puntos POR ENCIMA de la entrada,
    # una operacion invalida de raiz, no solo mal timada. Se rechaza en vez
    # de abrir una posicion rota; se reintentara solo (via OrdenPendiente)
    # hasta que cierre una vela nueva y el extremo se actualice de verdad.
    riesgo_valido = (stop < precio) if lado == "largo" else (stop > precio)
    if not riesgo_valido:
        print(f"   (sin abrir {lado} por '{motivo}': stop {stop:.4f} invalido "
              f"contra entrada {precio:.4f} - el extremo de referencia quedo "
              f"obsoleto dentro de esta vela, se espera a que cierre una nueva)")
        return None

    veredictos = _evaluar_filtros(lado, base, cfg)

    # Veto REAL del filtro RVOL (no sombra) - UNICAMENTE aceleracion_alza/
    # baja, no impulso_* (ver monitor.py cfg["rvol_veta"] y
    # herramientas/backtest_rvol_filtro.py, 2026-08-07: RVOL>=1.0 mejora
    # aceleracion_* en 29k trades/9 años BTC+ETH 15m; en impulso_* la
    # diferencia es marginal, no se valido el veto ahi). Los demas filtros
    # de 'veredictos' se quedan como sombra (solo log/CSV), esto es el
    # UNICO que corta la apertura.
    if cfg.get("rvol_veta") and motivo in ("aceleracion_alza", "aceleracion_baja"):
        veredicto_vol, valor_vol = veredictos.get("volumen", (None, None))
        if veredicto_vol is False:
            valor_txt = f"{valor_vol:.2f}" if valor_vol is not None else "?"
            print(f"   (sin abrir {lado} por '{motivo}': RVOL {valor_txt} no confirma "
                  f"(veto activo, --sin-rvol-veta lo apaga))")
            if rechazo is not None:
                rechazo.append("rvol")
            return None

    # Veto REAL del filtro volatilidad (no sombra) - a diferencia de RVOL,
    # aplica a CUALQUIER motivo (impulso_* Y aceleracion_*): el backtest de
    # 9 años (herramientas/backtest_filtros_combinados.py, 2026-08-09) lo
    # probo sobre impulso+aceleracion combinados y salio neutro/positivo en
    # las tres temporalidades (5m/15m/1h) - a diferencia de soporte/
    # bollinger/btc, que empeoraron sin excepcion y por eso NO se promueven.
    if cfg.get("volatilidad_veta"):
        veredicto_atr, valor_atr = veredictos.get("volatilidad", (None, None))
        if veredicto_atr is False:
            valor_txt = f"{valor_atr:.3f}" if valor_atr is not None else "?"
            print(f"   (sin abrir {lado} por '{motivo}': ATR% {valor_txt} no confirma "
                  f"(veto activo, --sin-volatilidad-veta lo apaga))")
            if rechazo is not None:
                rechazo.append("volatilidad")
            return None

    id_op = f"{coin}_{cfg['tf']}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    tasa_entrada = (cfg["comision_maker"] if entrada_tipo == "limite"
                    else cfg["comision_taker"]) / 100.0
    tasa_salida = cfg["comision_taker"] / 100.0
    pos = PosicionSim(lado, precio, cantidad, margen, nocional, stop, motivo,
                       datetime.now().strftime("%H:%M:%S"), id_op, veredictos,
                       tasa_entrada=tasa_entrada, tasa_salida=tasa_salida)
    pos.stop_origen = stop_origen
    pos.entrada_tipo = entrada_tipo
    pos.atr_entrada = m.get("atr")
    if cfg["exit_rr"] > 0:
        # Objetivo FIJO a --exit-rr veces el riesgo inicial (rama "sma",
        # 2026-08-03) - sustituye a la escalera por completo para esta
        # posicion; ver el bloque de gestion en _revisar() y anotaciones.md.
        riesgo = abs(precio - stop)
        pos.objetivo = precio + cfg["exit_rr"] * riesgo if lado == "largo" else precio - cfg["exit_rr"] * riesgo
    else:
        pos.objetivo = _objetivo_escalera(m["velas"], m.get("atr"), precio, lado, cfg)

    liq = pos.liquidacion_aprox()
    liq_txt = f"{liq:.4f}" if liq else "?"
    riesgo_bps = abs(precio - stop) / precio * 1e4 if precio else 0
    obj_txt = f"{pos.objetivo:.4f}" if pos.objetivo else "?"
    texto = (f"ABRE {lado.upper()} (PAPEL) por '{motivo}': entrada {precio:.4f} "
             f"({entrada_tipo}) | "
             f"margen {margen:.2f} USDT x{cfg['leverage']:.0f} = nocional {nocional:.2f} USDT | "
             f"cantidad {cantidad:.4f} | stop {stop:.4f} ({stop_origen}, "
             f"riesgo {riesgo_bps:.0f} bps) | objetivo escalera {obj_txt} | "
             f"liquidacion aprox {liq_txt} (estimacion gruesa, sin funding)")
    print(f"   >>> {texto}")
    print(f"   coste i/v {pos.coste_total(precio):.4f} USDT "
          f"({pos.coste_total(precio)/margen*100:.2f}% del margen) -> "
          f"punto muerto {pos.punto_muerto(precio):.1f} bps de precio")
    print(f"   filtros (sombra, no vetan): {_texto_filtros(veredictos)}")
    return pos


def _cerrar(coin, cfg, pos, precio, motivo, capitales):
    """Cierra la posicion de PAPEL, actualiza el capital compuesto de la moneda.

    El capital se mueve con el pnl **NETO**: las comisiones de entrada y salida
    se descuentan de verdad. Devuelve un dict con el desglose para el CSV (el
    id permite cruzar este cierre con los veredictos de los filtros que se
    guardaron al abrir, en una fila anterior)."""
    bruto, pct_bruto = pos.pnl(precio)
    comision = pos.coste_total(precio)
    neto, pct_neto = pos.pnl_neto(precio)
    # La perdida real se acota al margen: en un exchange real la posicion se
    # liquida antes de perder mas que el margen puesto (ver
    # PosicionSim.liquidacion_aprox). Sin este tope, un movimiento entre dos
    # vueltas de sondeo mayor que el margen dejaba 'neto' mas negativo que
    # -100% y capitales[coin] podia cruzar a NEGATIVO - con margen/nocional
    # negativos en la siguiente apertura, todo el signo de pnl() se invierte
    # sin ningun aviso.
    capado = neto < -pos.margen
    if capado:
        neto, pct_neto = -pos.margen, -100.0
    capitales[coin] += neto
    texto = (f"CIERRA {pos.lado.upper()} (PAPEL) por '{motivo}': salida {precio:.4f} | "
             f"bruto {bruto:+.4f} - comision {comision:.4f} = "
             f"NETO {neto:+.4f} USDT ({pct_neto:+.1f}% del margen) | "
             f"capital {coin} ahora: {capitales[coin]:.4f} USDT")
    print(f"   >>> {texto}")
    if capado:
        print("   (perdida acotada al margen: el movimiento real superaba el "
              "100% - en un exchange real ya se habria liquidado)")
    if bruto > 0 >= neto:
        print(f"   (ojo: en bruto ganaba {bruto:+.4f} pero la comision se lo comio)")
    return {
        "bruto": bruto, "pct_bruto": pct_bruto, "comision": comision,
        "neto": neto, "pct_neto": pct_neto, "motivo": motivo, "id": pos.id,
        "lado": pos.lado,
    }


def _gestionar_posicion(coin, cfg, pos, precio, vela_form, m, direccion_nueva,
                          es_continuacion, vetado_cierre, capitales):
    """Gestiona UNA posicion de papel ya abierta durante esta vuelta: stop,
    salida RR fija (cfg["exit_rr"]>0) o escalera/trailing/breakeven/señal
    contraria (exit_rr==0), estancamiento, y el chequeo de continuacion por
    order flow.

    2026-08-10: extraida del bucle de _revisar() en monitor.py para poder
    gestionar VARIAS posiciones concurrentes por moneda (hasta
    cfg["posiciones_max"], antes era 1 sola) sin duplicar la logica -
    'direccion_nueva'/'es_continuacion'/'vetado_cierre' son de la MONEDA
    esta vuelta (no de esta posicion en particular), se calculan una vez en
    _revisar() y se comparten entre todas las posiciones abiertas de esa
    moneda/rama.

    Devuelve un dict: {"pos": pos_o_None (None si se cerro), "cierre":
    cierre_o_None, "eventos": [...], "imb_val", "imb_racha_val",
    "estancamiento_veredicto", "estancamiento_valor"} - 'pos' mutado
    in-place si sigue abierta (mismo objeto)."""
    eventos = []
    cierre = None
    imb_val = imb_racha_val = None
    estancamiento_veredicto = estancamiento_valor = ""

    if pos.lado == "largo":
        peor_intravuelta = min(precio, vela_form[3])   # low
    else:
        peor_intravuelta = max(precio, vela_form[2])   # high
    if pos.stop_tocado(peor_intravuelta):
        # Ejecuta al PEOR entre el stop nominal y el precio actual - ver
        # docstring original en el historial de monitor.py._revisar.
        precio_cierre = (min(pos.stop, precio) if pos.lado == "largo"
                         else max(pos.stop, precio))
        cierre = _cerrar(coin, cfg, pos, precio_cierre,
                         f"stop @ {pos.stop:.4f}", capitales)
        cierre["motivo"] = "stop"
        eventos.append("cierre")
        return {"pos": None, "cierre": cierre, "eventos": eventos,
                "imb_val": imb_val, "imb_racha_val": imb_racha_val,
                "estancamiento_veredicto": "", "estancamiento_valor": ""}

    if cfg["exit_rr"] > 0:
        # Salida RR FIJA (rama "sma", 2026-08-03): unicamente objetivo o
        # stop - sin escalera, sin breakeven por reversion, sin cierre por
        # señal contraria. pos.objetivo ya viene fijo desde _abrir().
        alcanzado = False
        if pos.objetivo is not None:
            mejor_intravuelta = (max(precio, vela_form[2]) if pos.lado == "largo"
                                  else min(precio, vela_form[3]))
            alcanzado = (mejor_intravuelta >= pos.objetivo if pos.lado == "largo"
                         else mejor_intravuelta <= pos.objetivo)
        if alcanzado:
            cierre = _cerrar(coin, cfg, pos, pos.objetivo, "objetivo_rr", capitales)
            cierre["motivo"] = "objetivo_rr"
            eventos.append("cierre")
            return {"pos": None, "cierre": cierre, "eventos": eventos,
                    "imb_val": imb_val, "imb_racha_val": imb_racha_val,
                    "estancamiento_veredicto": "", "estancamiento_valor": ""}
        usdt, pct = pos.pnl(precio)
        neto, pct_neto = pos.pnl_neto(precio)
        print(f"   posicion [{pos.id}]: {pos.lado.upper()} desde {pos.entrada:.4f} "
              f"({pos.hora})  neto {neto:+.4f} USDT ({pct_neto:+.1f}% margen; "
              f"bruto {usdt:+.4f})  stop {pos.stop:.4f} objetivo_rr {pos.objetivo:.4f}")
    else:
        # exit_rr == 0: señal contraria / breakeven por reversion / escalera
        # o trailing por giveback - ver comentarios originales, sin cambios
        # de logica respecto a la version de una sola posicion.
        if (direccion_nueva is not None and direccion_nueva != pos.lado
                and not vetado_cierre and es_continuacion):
            cierre = _cerrar(coin, cfg, pos, precio, "señal contraria", capitales)
            cierre["motivo"] = "senal_contraria"
            eventos.append("cierre")
            return {"pos": None, "cierre": cierre, "eventos": eventos,
                    "imb_val": imb_val, "imb_racha_val": imb_racha_val,
                    "estancamiento_veredicto": "", "estancamiento_valor": ""}

        if (direccion_nueva is not None and direccion_nueva != pos.lado
                and not vetado_cierre and not es_continuacion):
            en_lado_bueno = (precio > pos.entrada if pos.lado == "largo"
                              else precio < pos.entrada)
            if en_lado_bueno:
                nuevo_stop = pos.entrada
                stop_anterior = pos.stop
                pos.stop = (max(pos.stop, nuevo_stop) if pos.lado == "largo"
                            else min(pos.stop, nuevo_stop))
                if pos.stop != stop_anterior:
                    pos.stop_origen = "breakeven (reversion contraria)"
                    print(f"   >>> [{pos.id}] reversion contraria ({direccion_nueva}) "
                          f"no cierra - stop a breakeven ({pos.stop:.4f})")
                    eventos.append("stop_breakeven")
            else:
                print(f"   ([{pos.id}] reversion contraria ({direccion_nueva}) no "
                      f"cierra, pero el precio ya esta del lado malo de la entrada - "
                      f"no se toca el stop, sigue el original)")

        if cfg["trailing_giveback"] > 0 and pos.atr_entrada:
            if _aplicar_trailing_giveback(pos, precio, vela_form, cfg):
                print(f"   >>> [{pos.id}] TRAILING: pico {pos.pico_precio:.4f} -> "
                      f"nuevo stop {pos.stop:.4f}")
                eventos.append("trailing_ajustado")
        elif pos.objetivo is not None:
            mejor_intravuelta = (max(precio, vela_form[2]) if pos.lado == "largo"
                                  else min(precio, vela_form[3]))
            alcanzado = (mejor_intravuelta >= pos.objetivo if pos.lado == "largo"
                         else mejor_intravuelta <= pos.objetivo)
            if alcanzado:
                nuevo_stop = pos.objetivo
                pos.stop = (max(pos.stop, nuevo_stop) if pos.lado == "largo"
                            else min(pos.stop, nuevo_stop))
                pos.stop_origen = f"escalera@{nuevo_stop:.4f}"
                siguiente = _objetivo_escalera(m["velas"], m.get("atr"), precio,
                                                pos.lado, cfg)
                sig_txt = f"{siguiente:.4f}" if siguiente else "?"
                print(f"   >>> [{pos.id}] ESCALERA: nivel alcanzado ({nuevo_stop:.4f}) "
                      f"-> nuevo stop {pos.stop:.4f} | siguiente objetivo {sig_txt}")
                pos.objetivo = siguiente
                eventos.append("escalera")

        usdt, pct = pos.pnl(precio)
        neto, pct_neto = pos.pnl_neto(precio)
        print(f"   posicion [{pos.id}]: {pos.lado.upper()} desde {pos.entrada:.4f} "
              f"({pos.hora})  neto {neto:+.4f} USDT ({pct_neto:+.1f}% margen; "
              f"bruto {usdt:+.4f})  stop {pos.stop:.4f}")

    # "estancamiento" (ver PosicionSim.mfe_pct/mfe_dt): si lleva >=30 min sin
    # superar su propio maximo favorable, con la tendencia en contra, y el
    # precio sigue del lado bueno de la entrada, asegura breakeven - unico
    # mecanismo de proteccion intermedia que existe con la salida RR fija
    # (2026-08-1x, ver anotaciones.md).
    ahora_utc = datetime.now(timezone.utc)
    if pos.mfe_pct is None or pct_neto > pos.mfe_pct:
        pos.mfe_pct = pct_neto
        pos.mfe_dt = ahora_utc
    minutos_en_trade = (ahora_utc - pos.apertura_dt).total_seconds() / 60
    minutos_desde_mfe = ((ahora_utc - pos.mfe_dt).total_seconds() / 60
                          if pos.mfe_dt else 0.0)
    if minutos_en_trade >= 30:
        tendencia_a_favor = ((m["tendencia"] == "ALCISTA" and pos.lado == "largo")
                              or (m["tendencia"] == "BAJISTA" and pos.lado == "corto"))
        estancamiento_valor = round(minutos_desde_mfe, 1)
        estancamiento_veredicto = (minutos_desde_mfe / minutos_en_trade > 0.6
                                    and not tendencia_a_favor)
        if estancamiento_veredicto:
            en_lado_bueno = (precio > pos.entrada if pos.lado == "largo"
                              else precio < pos.entrada)
            if en_lado_bueno:
                nuevo_stop = pos.entrada
                stop_anterior = pos.stop
                pos.stop = (max(pos.stop, nuevo_stop) if pos.lado == "largo"
                            else min(pos.stop, nuevo_stop))
                if pos.stop != stop_anterior:
                    pos.stop_origen = "breakeven (estancamiento)"
                    print(f"   >>> [{pos.id}] ESTANCAMIENTO: {minutos_desde_mfe:.0f} min "
                          f"sin superar el maximo favorable ({minutos_en_trade:.0f} min "
                          f"en trade, tendencia en contra) - stop a breakeven "
                          f"({pos.stop:.4f})")
                    eventos.append("stop_breakeven_estancamiento")

    imb_val, imb_racha_val, disparo = _chequear_continuacion(coin, cfg, pos, m["simbolo"])
    if disparo:
        eventos.append("continuacion")

    return {"pos": pos, "cierre": None, "eventos": eventos,
            "imb_val": imb_val, "imb_racha_val": imb_racha_val,
            "estancamiento_veredicto": estancamiento_veredicto,
            "estancamiento_valor": estancamiento_valor}


def _libro_favorece(simbolo, lado, umbral):
    """Consulta el libro real UNA vez y dice si el imbalance sigue a favor de
    'lado' (mismo umbral y niveles que _chequear_continuacion - reusa el
    criterio ya validado con datos, no inventa uno nuevo). Se usa para decidir
    si una OrdenPendiente que no llego a llenarse merece escalar a mercado:
    a diferencia del ADX del arbitro (solo cambia al cerrar una vela de esa
    franja, no dice nada nuevo a los pocos segundos de la señal), el libro es
    una señal en vivo, pensada para leerse a esta escala de tiempo.

    Devuelve (imbalance_o_None, favorece_bool). None si el libro no responde
    o esta incompleto - en ese caso favorece siempre sale False (sin datos no
    se fuerza una entrada a mercado)."""
    try:
        libro = datos.libro(simbolo, depth=20)
    except Exception:
        return None, False
    if not libro or not libro.get('bids') or not libro.get('asks'):
        return None, False
    imb = flujo.imbalance(libro, niveles=10)
    favorece = (imb >= umbral) if lado == "largo" else (imb <= -umbral)
    return imb, favorece


def _chequear_continuacion(coin, cfg, pos, simbolo):
    """Consulta el libro en vivo; si el imbalance sigue fuerte y a favor de la
    posicion --imb-confirmaciones vueltas seguidas, avisa UNA vez por racha.
    Devuelve (imbalance_o_None, racha, disparo_bool) para el registro CSV."""
    try:
        libro = datos.libro(simbolo, depth=20)
    except Exception as e:
        print(f"   (order flow: error leyendo libro: {e})")
        return None, pos.imb_racha, False
    if not libro or not libro.get('bids') or not libro.get('asks'):
        return None, pos.imb_racha, False

    imb = flujo.imbalance(libro, niveles=10)
    a_favor = (imb >= cfg["imb_umbral"] if pos.lado == "largo"
               else imb <= -cfg["imb_umbral"])

    if a_favor:
        pos.imb_racha += 1
    else:
        pos.imb_racha = 0
        pos.imb_avisada = False

    print(f"   order flow: imbalance {imb:+.2f}  (racha a favor: {pos.imb_racha}/{cfg['imb_confirmaciones']})")

    disparo = False
    if pos.imb_racha >= cfg["imb_confirmaciones"] and not pos.imb_avisada:
        pos.imb_avisada = True
        disparo = True
        texto = (f"CONTINUACION CONFIRMADA por order flow en {pos.lado.upper()}: "
                 f"imbalance {imb:+.2f} a favor durante {pos.imb_racha} vueltas seguidas.")
        print(f"   >>> {texto}")
        # Tampoco avisa por Telegram (solo consola + CSV), mismo motivo que
        # las señales: ruido. Solo apertura/cierre notifican.

    return imb, pos.imb_racha, disparo
