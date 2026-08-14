# ---------------------------------------------------------------
# backtest_senales.py - Backtest OFFLINE de mercado/senales.py sobre
# historico profundo, para afinar umbrales SIN tocar monitor_niveles.py ni
# depender de una sesion en vivo (grabador_libro.py corriendo, Bitget, etc).
#
# Dos fuentes posibles:
#   --fuente historicos (default) -> historicos/<fecha>_<COIN>_<TF>_binance.csv
#                                     (el mas profundo: años de historia,
#                                     bajado con historicos/descargar_bin.py)
#   --fuente bitget               -> herramientas/libro/historico_<COIN>_<TF>_bitget.csv
#                                     (el que usa monitor_niveles.py en vivo,
#                                     mas corto pero el precio real vigilado)
# Es solo para pruebas/calibracion offline - NO se usa para decidir niveles
# en vivo (ver advertencia de Bitget-vs-Binance en niveles.py), asi
# que mezclar exchange aqui no es un problema.
#
# Recorre el historico vela a vela llamando a senales.detectar() (la MISMA
# funcion que usa monitor_niveles.py en vivo) y para cada señal que se
# dispara mide el retorno % a N velas despues, en la direccion que esa
# señal anticipa (ver DIRECCION_ESPERADA) - comparado contra la linea base
# (retorno medio de CUALQUIER vela a esa misma distancia). Una señal sin
# "borde" real deberia parecerse a la linea base.
#
# --desde-1m: en vez de cargar el historico nativo de <tf> tal cual lo
# publica el exchange, carga el de 1m y agrega sus velas ya cerradas en
# bloques de <tf> (mismo criterio de timestamp que el exchange: apertura
# del bloque). Sirve para comparar si detectar() da resultados distintos
# sobre velas "reconstruidas" desde 1m frente a las nativas del TF - util
# para decidir si algun dia merece la pena que monitor_niveles.py agregue
# asi en vivo, SIN tocarlo todavia (esto es solo para probar offline).
#
# --desde/--hasta (YYYY-MM-DD): acotan el periodo a contrastar a fechas
# concretas (en vez de "los ultimos --dias"), para comparar tramos - ej.
# 2018-2022 vs 2023-2026.
#
# --tolerancia-atr y --toques-min (los dos juntos activan el contraste):
# ademas de la tabla de siempre, calcula los niveles vigentes de
# niveles.py y clasifica cada señal en 'favorable' (nivel a favor
# de su direccion cerca), 'contrario' (nivel en contra cerca) o 'lejos'.
# Los niveles se RECALCULAN cada --refresco-niveles dias (30 por defecto) a
# lo largo de todo el periodo -desde/--hasta, cada vez con los ultimos
# DIAS_NIVELES_PREVIOS (90) dias ANTERIORES a ese punto - sin fuga de
# informacion del futuro hacia cada tramo. Antes esto era una unica foto
# fija tomada en --desde; sobre periodos de años esa foto quedaba obsoleta
# enseguida (niveles de 2018 ya no significaban nada en 2021) - con el
# refresco cada tramo usa niveles calculados con precios cercanos a esa
# fecha, no de años atras.
#
# Uso:
#   python herramientas/backtest_senales.py <coin> <tf> [--fuente historicos|bitget]
#                                            [--dias N | --desde F --hasta F]
#                                            [--horizontes 5,15,30] [--k 3] [--desde-1m]
#                                            [--tolerancia-atr 0.25 --toques-min 4]
#                                            [--refresco-niveles 30]
#                                            [--dias-niveles-previos 90]
#
# Ejemplos:
#   python herramientas/backtest_senales.py btc 15m
#   python herramientas/backtest_senales.py btc 15m --dias 365 --horizontes 10,30,60
#   python herramientas/backtest_senales.py eth 5m --fuente bitget
#   python herramientas/backtest_senales.py btc 15m --desde-1m --dias 365
#   python herramientas/backtest_senales.py eth 4h --desde 2018-08-11 --hasta 2022-01-01 --tolerancia-atr 0.25 --toques-min 4 --refresco-niveles 30
# ---------------------------------------------------------------

import csv
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import senales
from herramientas.descargar_bit import _archivo as _archivo_bitget
from herramientas.niveles import (
    detectar_niveles as _detectar_niveles, _evaluar_estado, _rol_efectivo,
)

DIR_HISTORICOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "historicos")
DIAS_NIVELES_PREVIOS = 90.0  # default - override con --dias-niveles-previos

# Direccion que cada señal anticipa: +1 = espera subida, -1 = espera
# bajada. impulso/aceleracion/ruptura son de CONTINUACION; rechazo/
# divergencia/rsi-extremo son de REVERSION - por eso rechazo_max (mecha de
# rechazo arriba) espera bajada, no subida.
DIRECCION_ESPERADA = {
    "impulso_alza": 1, "impulso_baja": -1,
    "aceleracion_alza": 1, "aceleracion_baja": -1,
    "ruptura_alza": 1, "ruptura_baja": -1,
    "rechazo_max": -1, "rechazo_min": 1,
    "div_bajista": -1, "div_alcista": 1,
    "rsi_sobrecompra": -1, "rsi_sobreventa": 1,
}
# Las variantes refinadas del Grupo A (ver senales.NOMBRE_REFINADO) anticipan
# la MISMA direccion que su señal base - solo cambia el nombre/filtro, no
# lo que predicen.
for _base, _refinado in senales.NOMBRE_REFINADO.items():
    DIRECCION_ESPERADA[_refinado] = DIRECCION_ESPERADA[_base]
del _base, _refinado


def _ultimo_timestamp_ms(ruta):
    with open(ruta, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - 65536))
        cola = f.read()
    lineas = [l for l in cola.split(b"\n") if l.strip()]
    return int(lineas[-1].split(b",")[0])


def _localizar_historicos(coin, tf):
    """Busca en historicos/ el CSV mas reciente <fecha>_<COIN>_<TF>_binance.csv
    (puede haber varias fechas si se re-bajo mas de una vez - se toma la
    ultima modificada, mismo patron que monitor_niveles._localizar_csv_libro)."""
    if not os.path.isdir(DIR_HISTORICOS):
        return None
    patron = re.compile(rf"^\d{{2}}-\d{{2}}-\d{{2}}_{coin.upper()}_{tf}_binance\.csv$")
    candidatos = [os.path.join(DIR_HISTORICOS, n) for n in os.listdir(DIR_HISTORICOS) if patron.match(n)]
    if not candidatos:
        return None
    return max(candidatos, key=os.path.getmtime)


def _cargar_velas(ruta, desde_ms=None, hasta_ms=None):
    """Carga 'ruta' (mismo formato en historicos/ y herramientas/libro/:
    timestamp,fecha_utc,open,high,low,close,volumen) filtrando por
    timestamp absoluto en ms (None = sin limite por ese lado) - se
    descartan las filas fuera de rango AL VUELO en vez de cargar el fichero
    entero en memoria para tirarlas despues (los de 1m/3m pueden pesar
    cientos de MB)."""
    velas = []
    with open(ruta, newline="") as f:
        r = csv.reader(f)
        next(r)  # cabecera
        for row in r:
            if len(row) < 7:
                continue  # linea vacia/incompleta (visto en la practica: un
                           # '\r' suelto al final de un CSV de historicos/)
            ts = int(row[0])
            if desde_ms is not None and ts < desde_ms:
                continue
            if hasta_ms is not None and ts > hasta_ms:
                continue
            velas.append([ts, float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])])
    return velas


def _niveles_vigentes(velas_previas, k, tolerancia_atr, toques_min, confirmacion_velas=2):
    """Niveles vigentes (vivos/flip) al final de 'velas_previas' - mismo
    criterio que _analizar() de niveles.py, pero operando sobre una
    lista de velas ya en memoria (no releida del disco) para poder darle
    SOLO la historia anterior al periodo que se esta contrastando, sin
    fuga de informacion del periodo hacia el calculo de niveles.

    Devuelve (lista de (precio, rol_efectivo), tolerancia) - rol_efectivo
    ya tiene en cuenta el flip (ver _rol_efectivo de niveles.py):
    un techo que hizo flip se devuelve como 'suelo', y viceversa - lo que
    importa para el contraste es el rol ACTUAL, no el original."""
    niveles, atr_ref, tolerancia = _detectar_niveles(velas_previas, k, tolerancia_atr, toques_min)
    for niv in niveles:
        estado, _, _ = _evaluar_estado(velas_previas, niv["precio"], niv["tipo"], tolerancia,
                                        niv["ultimo"], confirmacion_velas)
        niv["estado"] = estado
    vigentes = [niv for niv in niveles if niv["estado"] in ("vivo", "flip")]
    return [(niv["precio"], _rol_efectivo(niv)) for niv in vigentes], tolerancia


def _niveles_por_tramos(velas, desde_ms, hasta_ms, refresco_dias, dias_lookback,
                         k, tolerancia_atr, toques_min, confirmacion_velas=2):
    """Recalcula los niveles vigentes cada 'refresco_dias' a lo largo de
    [desde_ms, hasta_ms) - en cada recalculo usa SOLO los 'dias_lookback'
    dias ANTERIORES a ese punto concreto (ventana movil, no toda la
    historia desde el principio) - sin fuga de informacion del futuro
    hacia ningun tramo. Sin esto, una unica foto tomada en --desde queda
    obsoleta sobre periodos de años (ver cabecera del archivo).

    Devuelve una lista ordenada de (ts_desde_cuando_aplica, niveles_vigentes,
    tolerancia) - _backtest() usa el tramo mas reciente cuyo ts_desde sea
    <= la vela que se este evaluando."""
    refresco_ms = int(refresco_dias * 86_400_000)
    lookback_ms = int(dias_lookback * 86_400_000)
    tramos = []
    ts = desde_ms
    while ts < hasta_ms:
        velas_previas = [v for v in velas if ts - lookback_ms <= v[0] < ts]
        if len(velas_previas) >= 100:
            vigentes, tolerancia = _niveles_vigentes(velas_previas, k, tolerancia_atr, toques_min, confirmacion_velas)
        else:
            vigentes, tolerancia = [], 0.0
        tramos.append((ts, vigentes, tolerancia))
        ts += refresco_ms
    return tramos


def _tf_minutos(tf):
    unidad = tf[-1]
    cantidad = int(tf[:-1])
    return cantidad * {"m": 1, "h": 60, "d": 1440}[unidad]


def _cerrar_bucket(velas_1m_bucket, bucket_ts):
    altos = [v[2] for v in velas_1m_bucket]
    bajos = [v[3] for v in velas_1m_bucket]
    return [bucket_ts, velas_1m_bucket[0][1], max(altos), min(bajos),
            velas_1m_bucket[-1][4], sum(v[5] for v in velas_1m_bucket)]


def _agregar_1m(velas_1m, tf_ms):
    """Agrupa 'velas_1m' (ascendentes, ya cerradas) en velas de 'tf_ms'
    milisegundos, con el mismo criterio de timestamp que Bitget/Binance: el
    de APERTURA del bloque (ts - ts % tf_ms). Un bloque solo se agrega si
    esta COMPLETO - se detecta que lo esta porque aparece una vela de 1m de
    un bloque posterior (el ultimo bloque, sin nada despues, se descarta -
    en un backtest no hace falta esperar a la proxima vuelta como en vivo).
    Devuelve solo las velas agregadas y CERRADAS."""
    agregadas = []
    buffer = []
    bucket_actual = None
    for v in velas_1m:
        bucket = v[0] - (v[0] % tf_ms)
        if bucket_actual is None:
            bucket_actual = bucket
        if bucket != bucket_actual:
            agregadas.append(_cerrar_bucket(buffer, bucket_actual))
            buffer = []
            bucket_actual = bucket
        buffer.append(v)
    return agregadas


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _backtest(velas, k, horizontes, desde_ms=None, tramos_niveles=None):
    """Recorre 'velas' vela a vela llamando a senales.detectar() sobre una
    ventana acotada (senales.VENTANA_MAXIMA - mismo criterio que usa
    monitor_niveles.py en vivo, ver su comentario) para no copiar listas
    cada vez mas grandes en cada paso.

    'desde_ms', si se da, evita contar disparos/baseline de las velas de
    calentamiento cargadas de mas SOLO para contexto (ver DIAS_NIVELES_PREVIOS
    en main()) - la señal en si SI ve esas velas previas (para tener
    historia real, no arrancar en frio justo en el punto de corte).

    'tramos_niveles', si se da (ver _niveles_por_tramos), clasifica cada
    disparo segun el nivel vigente mas cercano DEL TRAMO QUE APLIQUE en ese
    momento (se recalculan periodicamente, no una foto fija) RELATIVO a la
    direccion que esa señal concreta anticipa:
      "favorable" - hay un nivel a favor cerca (suelo para una señal alcista,
                    techo para una bajista - ej. rebotando en soporte)
      "contrario" - hay un nivel en contra cerca (techo para una alcista,
                    suelo para una bajista - ej. chocando con resistencia)
      "lejos"     - no hay ningun nivel vigente cerca
    (si hay de los dos tipos a la vez, gana "favorable" - caso raro).
    Devuelve:
      disparos: {nombre_señal: {horizonte: [(retorno_pct, clase_o_None, ts_ms_señal), ...]}}
      baseline: {horizonte: [retorno_pct, ...]} de TODAS las velas (para comparar)
    """
    ventana_max = senales.VENTANA_MAXIMA
    disparos = {nombre: {h: [] for h in horizontes} for nombre in DIRECCION_ESPERADA}
    baseline = {h: [] for h in horizontes}
    n = len(velas)
    inicio = min(ventana_max, n)
    if desde_ms is not None:
        inicio = max(inicio, next((idx for idx, v in enumerate(velas) if v[0] >= desde_ms), n))

    idx_tramo = 0
    for i in range(inicio, n):
        cierre_i = velas[i][4]
        ts_i = velas[i][0]  # timestamp de la vela que dispara la señal - lo lleva
                             # cada disparo (ver mas abajo) para que quien mida el
                             # retorno en vivo (fjsl.py) pueda ubicar de que vela
                             # exacta viene, sin tener que re-buscarla por fecha.
        for h in horizontes:
            if i + h < n:
                baseline[h].append((velas[i + h][4] - cierre_i) / cierre_i * 100)

        niveles_vigentes = tolerancia_nivel = None
        if tramos_niveles:
            while idx_tramo + 1 < len(tramos_niveles) and tramos_niveles[idx_tramo + 1][0] <= ts_i:
                idx_tramo += 1
            _, niveles_vigentes, tolerancia_nivel = tramos_niveles[idx_tramo]

        # Con niveles_vigentes activo, senales.detectar() ya filtra/renombra
        # el Grupo A (ver mercado/senales.NIVEL_UTIL_GRUPO_A) - las 7 solo
        # salen (renombradas) si tienen cerca el nivel que de verdad les
        # ayuda; si no, detectar() las descarta directamente.
        ventana = velas[i + 1 - ventana_max:i + 1]
        activas = senales.detectar(ventana, k, niveles_vigentes=niveles_vigentes,
                                    tolerancia_nivel=tolerancia_nivel)
        if not activas:
            continue

        for nombre in activas:
            clase = None
            if niveles_vigentes is not None:
                direccion = DIRECCION_ESPERADA[nombre]
                tipo_favorable = "suelo" if direccion == 1 else "techo"
                tipo_contrario = "techo" if direccion == 1 else "suelo"
                cerca_favorable = any(rol == tipo_favorable and abs(cierre_i - precio) <= tolerancia_nivel
                                       for precio, rol in niveles_vigentes)
                cerca_contrario = any(rol == tipo_contrario and abs(cierre_i - precio) <= tolerancia_nivel
                                       for precio, rol in niveles_vigentes)
                clase = "favorable" if cerca_favorable else ("contrario" if cerca_contrario else "lejos")
            for h in horizontes:
                if i + h < n:
                    retorno = (velas[i + h][4] - cierre_i) / cierre_i * 100
                    disparos[nombre][h].append((retorno, clase, ts_i))

    return disparos, baseline


def _media(xs):
    return sum(xs) / len(xs) if xs else None


def _tasa_acierto(retornos, direccion):
    if not retornos:
        return None
    aciertos = sum(1 for r in retornos if r * direccion > 0)
    return aciertos / len(retornos) * 100


def _fecha_ms(fecha):
    return int(datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return
    coin, tf = args[0], args[1]
    resto = args[2:]

    fuente = "historicos"
    dias = None
    desde = hasta = None
    horizontes = [5, 15, 30]
    k = 3
    desde_1m = False
    tolerancia_atr = toques_min = None
    confirmacion_velas = 2
    refresco_niveles = 30.0
    dias_niveles_previos = DIAS_NIVELES_PREVIOS
    i = 0
    while i < len(resto):
        if resto[i] == "--fuente":
            i += 1; fuente = resto[i]
        elif resto[i] == "--dias":
            i += 1; dias = float(resto[i])
        elif resto[i] == "--desde":
            i += 1; desde = resto[i]
        elif resto[i] == "--hasta":
            i += 1; hasta = resto[i]
        elif resto[i] == "--horizontes":
            i += 1; horizontes = [int(x) for x in resto[i].split(",")]
        elif resto[i] == "--k":
            i += 1; k = int(resto[i])
        elif resto[i] == "--desde-1m":
            desde_1m = True
        elif resto[i] == "--tolerancia-atr":
            i += 1; tolerancia_atr = float(resto[i])
        elif resto[i] == "--toques-min":
            i += 1; toques_min = int(resto[i])
        elif resto[i] == "--confirmacion-velas":
            i += 1; confirmacion_velas = int(resto[i])
        elif resto[i] == "--refresco-niveles":
            i += 1; refresco_niveles = float(resto[i])
        elif resto[i] == "--dias-niveles-previos":
            i += 1; dias_niveles_previos = float(resto[i])
        i += 1

    tf_carga = "1m" if desde_1m else tf
    if fuente == "historicos":
        ruta = _localizar_historicos(coin, tf_carga)
        if ruta is None:
            print(f"No hay CSV en historicos/ para {coin.upper()} {tf_carga} "
                  f"(patron esperado: <fecha>_{coin.upper()}_{tf_carga}_binance.csv)")
            return
    elif fuente == "bitget":
        ruta = _archivo_bitget(coin, tf_carga)
        if not os.path.exists(ruta):
            print(f"No hay historico Bitget para {coin.upper()} {tf_carga} en {ruta} - "
                  f"bajalo primero con descargar_bit.py")
            return
    else:
        print("Fuente invalida - usar 'historicos' o 'bitget'")
        return

    hasta_ms = _fecha_ms(hasta) + 86_400_000 - 1 if hasta else None  # inclusive todo el dia
    if desde:
        desde_ms = _fecha_ms(desde)
    elif dias is not None:
        ref_ms = hasta_ms if hasta_ms is not None else _ultimo_timestamp_ms(ruta)
        desde_ms = ref_ms - int(dias * 86_400_000)
    else:
        desde_ms = None

    niveles_activo = tolerancia_atr is not None and toques_min is not None
    if niveles_activo and desde_ms is None:
        print("--tolerancia-atr/--toques-min piden --desde (o --dias) para saber donde "
              "empieza el periodo a contrastar sin fuga de informacion.")
        return

    desde_ms_carga = desde_ms
    if niveles_activo:
        desde_ms_carga = desde_ms - int(dias_niveles_previos * 86_400_000)

    print(f"Cargando {ruta}" + (f" ({_fmt_fecha(desde_ms_carga)} -> {_fmt_fecha(hasta_ms) if hasta_ms else 'fin'})"
                                 if desde_ms_carga else " (todo el historico)") + " ...")
    velas = _cargar_velas(ruta, desde_ms_carga, hasta_ms)
    if not velas:
        print("No se cargo ninguna vela (¿rango de fechas fuera del historico disponible?).")
        return

    if desde_1m:
        n_1m = len(velas)
        velas = _agregar_1m(velas, _tf_minutos(tf) * 60_000)
        print(f"{n_1m} velas de 1m -> {len(velas)} velas de {tf} agregadas "
              f"(bloque final incompleto descartado)")

    print(f"{len(velas)} velas: {_fmt_fecha(velas[0][0])} -> {_fmt_fecha(velas[-1][0])}")
    print(f"k={k}  horizontes={horizontes} velas  ventana señales={senales.VENTANA_MAXIMA}")

    tramos_niveles = None
    if niveles_activo:
        velas_previas = [v for v in velas if v[0] < desde_ms]
        if len(velas_previas) < 100:
            print(f"Solo {len(velas_previas)} velas antes de {desde} - muy poco para calcular "
                  f"niveles fiables (mueve --desde mas adelante).")
            return
        hasta_ms_tramos = hasta_ms + 1 if hasta_ms is not None else velas[-1][0] + 1
        tramos_niveles = _niveles_por_tramos(velas, desde_ms, hasta_ms_tramos, refresco_niveles,
                                              dias_niveles_previos, k, tolerancia_atr, toques_min,
                                              confirmacion_velas)
        n_medio = sum(len(t[1]) for t in tramos_niveles) / len(tramos_niveles) if tramos_niveles else 0
        print(f"Niveles recalculados cada {refresco_niveles:.0f} dias ({len(tramos_niveles)} tramos, "
              f"lookback {dias_niveles_previos:.0f}d): {n_medio:.0f} niveles vigentes de media por tramo")
    print()

    disparos, baseline = _backtest(velas, k, horizontes, desde_ms, tramos_niveles)
    baseline_media = {h: _media(baseline[h]) for h in horizontes}

    _tabla_principal(disparos, horizontes, baseline_media, niveles_activo)
    if not niveles_activo:
        return
    _tabla_contraste(disparos, horizontes, tramos_niveles, baseline_media)


def _edge(retornos, direccion, h, baseline_media):
    """Antes vivia como closure dentro de main() (capturando baseline_media) -
    se saca a nivel de modulo (2026-08-12) para que fjsl.py pueda reusarla
    tal cual, en vez de reimplementar la misma formula."""
    if not retornos:
        return None, None, None
    ret_dir = _media([r * direccion for r in retornos])
    acierto = _tasa_acierto(retornos, direccion)
    # 'edge' compara contra lo que ganaria la MISMA apuesta direccional
    # sobre una vela cualquiera (direccion*baseline), no contra el
    # baseline crudo - si el activo tiene tendencia neta en el periodo,
    # comparar contra el baseline sin ajustar de signo favorece a
    # ciegas a las señales que apuestan a favor de esa tendencia.
    edge = ret_dir - direccion * baseline_media[h]
    return ret_dir, acierto, edge


def _tabla_principal(disparos, horizontes, baseline_media, niveles_activo):
    """Extraida de main() (2026-08-12) para que fjsl.py imprima exactamente
    la misma tabla en cada ciclo, sin duplicar el formateo."""
    cab = f"{'señal':<18}{'n':>7}"
    for h in horizontes:
        cab += f"   ret@{h}(%)  acierto%   edge@{h}(%)"
    print(cab)
    print("-" * len(cab))
    for nombre in sorted(DIRECCION_ESPERADA):
        # Con niveles_activo, senales.detectar() ya no emite los nombres
        # base del Grupo A bajo su nombre original (los renombra o los
        # descarta - ver NOMBRE_REFINADO) - no tiene sentido imprimir esa
        # fila, siempre saldria a 0.
        if niveles_activo and nombre in senales.NOMBRE_REFINADO:
            continue
        direccion = DIRECCION_ESPERADA[nombre]
        n_disparos = len(disparos[nombre][horizontes[0]]) if horizontes else 0
        fila = f"{nombre:<18}{n_disparos:>7}"
        for h in horizontes:
            retornos = [r for r, _, _ in disparos[nombre][h]]
            resultado = _edge(retornos, direccion, h, baseline_media)
            fila += (f"   {resultado[0]:>8.3f}  {resultado[1]:>7.1f}  {resultado[2]:>10.3f}"
                     if retornos else f"   {'--':>8}  {'--':>7}  {'--':>10}")
        print(fila)

    print("\n'ret@N' es el retorno medio en la direccion que la señal anticipa (positivo = a favor).")
    print("'edge@N' es ret@N MENOS lo que ganaria la misma apuesta direccional sobre una vela")
    print("cualquiera (direccion x retorno medio de TODO el historico a esa distancia) - aisla el")
    print("aporte de la señal en si de la tendencia neta del periodo. edge@N ~ 0 = sin borde real.")


def _tabla_contraste(disparos, horizontes, tramos_niveles, baseline_media):
    """Extraida de main() (2026-08-12), mismo motivo que _tabla_principal."""
    tolerancias = [t[2] for t in tramos_niveles if t[1]]
    rango_tol = f"{min(tolerancias):.4f}-{max(tolerancias):.4f}" if tolerancias else "--"
    print(f"\n=== Contraste por contexto de nivel vigente (tolerancia por tramo: {rango_tol}) ===")
    print("favorable = nivel a favor cerca (suelo si la señal es alcista, techo si es bajista)")
    print("contrario = nivel en contra cerca (techo si es alcista, suelo si es bajista)")
    print("lejos     = sin ningun nivel vigente cerca\n")
    categorias = ["favorable", "contrario", "lejos"]
    cab2 = f"{'señal':<18}"
    for cat in categorias:
        cab2 += f"{'n_' + cat[:4]:>9}"
        for h in horizontes:
            cab2 += f"  edge_{cat[:4]}@{h}"
    print(cab2)
    print("-" * len(cab2))
    for nombre in sorted(DIRECCION_ESPERADA):
        if nombre in senales.NOMBRE_REFINADO:
            continue  # ver mismo motivo que en la tabla principal
        direccion = DIRECCION_ESPERADA[nombre]
        fila = f"{nombre:<18}"
        for cat in categorias:
            retornos_h0 = [r for r, c, _ in disparos[nombre][horizontes[0]] if c == cat]
            fila += f"{len(retornos_h0):>9}"
            for h in horizontes:
                retornos = [r for r, c, _ in disparos[nombre][h] if c == cat]
                resultado = _edge(retornos, direccion, h, baseline_media)
                fila += f"  {resultado[2]:>10.3f}" if retornos else f"  {'--':>10}"
        print(fila)
    print("\nCompara edge_favo/edge_cont/edge_lejo por señal - si son parecidos, el contexto de nivel")
    print("no cambia nada; si 'favorable' bate claramente a 'contrario' y 'lejos', el nivel si aporta.")


if __name__ == "__main__":
    main()
