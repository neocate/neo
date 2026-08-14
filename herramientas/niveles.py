# ---------------------------------------------------------------
# niveles.py - Detecta suelos/techos (soporte/resistencia) de UN TF
# a partir del historico ya descargado, para poder compararlos a ojo contra
# un grafico real (TradingView, Binance...) antes de fiarse de ellos.
#
# Deliberadamente SIN valores por defecto en k/tolerancia/toques-min - la
# idea es que el usuario los pase explicito cada vez y compare resultados,
# en vez de heredar sin darse cuenta los que ya se habian validado en el
# proyecto viejo (bit/escalera.py: tolerancia 0.25xATR, 6 toques). Los datos
# tienen que encontrar el optimo sin ese sesgo de partida.
#
# Criterio (mismo que bit/escalera.py._proximo_nivel, pero aqui se listan
# TODOS los niveles confirmados del rango pedido, no solo el siguiente por
# delante del precio):
#   1. Candidatos: extremos_locales() (swing highs/lows, k velas a cada lado)
#   2. Tolerancia en ATR (no en % fijo - un % fijo degenera en un filtro que
#      dice siempre que si o siempre que no, ver anotaciones de bit). Se usa
#      la MEDIANA del ATR del rango analizado como referencia unica, no un
#      ATR que cambia punto a punto - para que todos los niveles se midan
#      con la misma vara.
#   3. Toques minimos: un extremo tocado una sola vez no es soporte real.
#      Se agrupan velas consecutivas dentro de la tolerancia como UN solo
#      toque (si no, una visita larga infla el conteo).
#   4. Niveles que caen dentro de la tolerancia entre si se fusionan en uno,
#      sin importar tipo (techo/suelo) - una zona de rango actua como
#      resistencia y soporte alternada, y extremos_locales suele devolver
#      varios swing points casi pegados en la misma zona. Se agrupan en
#      clusters completos (ancla = precio del primer nivel del cluster, no
#      el ultimo agregado) para que una cadena de niveles muy pegados entre
#      si no derive mas alla de la tolerancia real.
#   5. Estado (vivo/roto/flip) despues del ultimo toque: un nivel se marca
#      "roto" cuando hay --confirmacion-velas cierres CONSECUTIVOS del otro
#      lado del nivel +/- tolerancia (un solo cierre puede ser ruido). Si
#      despues de romperse el precio vuelve a testear la zona desde el otro
#      lado, se marca "flip" (cambio de rol: techo roto actuando de suelo,
#      o viceversa). Valor sugerido por ahora: 2 velas de confirmacion -
#      no calibrado contra datos como k/tolerancia/toques-min, ajustar si
#      se ve mucho falso positivo/negativo.
#
# --tf-macro <tf>: ademas del TF principal (mas fino, para entradas/salidas),
# analiza un TF mas alto con los MISMOS k/tolerancia-atr/toques-min y usa su
# techo y suelo vigentes mas cercanos al precio como TOPES del rango. Los
# niveles del TF principal que caen fuera de ese rango macro se dejan
# afuera del listado "ajustado" (el listado completo de mas arriba no se
# toca, es solo una vista adicional).
#
# Uso:
#   python herramientas/niveles.py <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 3 [--desde-dias 90] [--tf-macro 4h]
#   python herramientas/niveles.py --actualizar <coin> <tf> --k .. --tolerancia-atr .. --toques-min ..
#                                   [--confirmacion-velas 2] [--cada segundos]
#
# --actualizar (2026-08-14): mantiene un LISTADO persistente de niveles en
# herramientas/niveles/<COIN>/ (listado_<TF>.json + historial_<TF>.csv que
# solo crece, un evento por fila: nivel_inicial/nivel_nuevo/toque/rotura/
# flip). Mismo espiritu que descargar_bit.py --velas: sin listado previo
# (o si cambian k/tolerancia-atr/toques-min/confirmacion-velas respecto al
# guardado) hace un barrido completo desde cero; si ya existe con los
# MISMOS parametros, solo actualiza los ESTADOS de los niveles ya
# conocidos con las velas nuevas desde la ultima vez - no vuelve a
# recalcular todo el historico en cada ejecucion. Sin --cada: una sola
# pasada. Con --cada (mismo patron que --velas --cada): modo daemon, llama
# a --actualizar en bucle - barato casi siempre, el barrido caro solo pasa
# la primera vez o si cambian los parametros.
#
# Ejemplos:
#   python herramientas/niveles.py btc 4h --k 2 --tolerancia-atr 0.25 --toques-min 3
#   python herramientas/niveles.py btc 15m --k 5 --tolerancia-atr 0.15 --toques-min 4 --desde-dias 30
#   python herramientas/niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 4 --desde-dias 90 --tf-macro 4h
#   python herramientas/niveles.py --actualizar eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 4
#   python herramientas/niveles.py --actualizar btc 4h --k 3 --tolerancia-atr 0.25 --toques-min 4 --cada 60
# ---------------------------------------------------------------

import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import indicadores
from herramientas.descargar_bit import _archivo_velas as _archivo_bitget

DIR_NIVELES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "niveles")

CAMPOS_HISTORIAL = [
    "fecha_ms", "fecha_utc", "coin", "tf", "evento", "tipo", "precio",
    "toques", "estado", "primero", "ultimo", "pid",
]

# Bitget, NO Binance (2026-08-07, correccion de Fran): los niveles se
# vigilan en vivo contra el precio de Bitget (mercado/datos.py, el mismo
# exchange que opera monitor.py) - calcularlos sobre velas de otro exchange
# (Binance, via descargar_bin.py) podia marcar "toque" o "roto" con precios
# que Bitget nunca vio, o al reves. Ver tambien herramientas/descargar_bit.py
# cabecera: "para contrastar una sesion real usar este script, no
# descargar_bin.py".


def _cargar_velas(coin, tf, desde_dias=None):
    ruta = _archivo_bitget(coin, tf)
    if not os.path.exists(ruta):
        raise FileNotFoundError(
            f"No hay historico de {coin.upper()} {tf} en herramientas/velas/ - "
            f"bajalo primero con: python herramientas/descargar_bit.py --velas {coin} {tf}")

    corte_ms = None
    if desde_dias is not None:
        corte_ms = int((datetime.now(timezone.utc) - timedelta(days=desde_dias)).timestamp() * 1000)

    velas = []
    with open(ruta, newline='') as f:
        r = csv.reader(f)
        next(r)  # cabecera
        for row in r:
            ts = int(row[0])
            if corte_ms is not None and ts < corte_ms:
                continue
            velas.append([ts, float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])])
    return velas, ruta


def _carpeta_niveles(coin):
    """herramientas/niveles/<COIN>/ - carpeta propia de --actualizar (mismo
    patron que herramientas/grabador_libro/ y herramientas/velas/: cada
    script que escribe estado persistente tiene su propia carpeta)."""
    carpeta = os.path.join(DIR_NIVELES, coin.upper())
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _ruta_listado(coin, tf):
    return os.path.join(_carpeta_niveles(coin), f"listado_{tf}.json")


def _ruta_historial(coin, tf):
    return os.path.join(_carpeta_niveles(coin), f"historial_{tf}.csv")


def _mediana(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _contar_toques(velas, nivel, tolerancia):
    """Cuenta toques agrupando velas consecutivas dentro de la tolerancia
    como UN solo toque. Devuelve (toques, ts_primero, ts_ultimo)."""
    toques = 0
    dentro = False
    primero = ultimo = None
    for v in velas:
        alto, bajo = v[2], v[3]
        cerca = (bajo - tolerancia) <= nivel <= (alto + tolerancia)
        if cerca:
            if not dentro:
                toques += 1
                dentro = True
                if primero is None:
                    primero = v[0]
            ultimo = v[0]
        else:
            dentro = False
    return toques, primero, ultimo


def _fusionar(niveles, tolerancia):
    """Niveles que caen dentro de 'tolerancia' de un cluster se fusionan en
    uno (se queda el de mas toques), sin importar tipo techo/suelo. Cada
    candidato se compara contra el precio del PRIMER nivel del cluster
    actual (no el ultimo agregado), para no ir derivando en cadena mas alla
    de la tolerancia real. 'niveles' ya viene ordenado por precio."""
    if not niveles:
        return []

    fusionados = []
    cluster = [niveles[0]]
    ancla = niveles[0]["precio"]

    def _cerrar_cluster():
        fusionados.append(max(cluster, key=lambda d: d["toques"]))

    for niv in niveles[1:]:
        if abs(niv["precio"] - ancla) <= tolerancia:
            cluster.append(niv)
        else:
            _cerrar_cluster()
            cluster = [niv]
            ancla = niv["precio"]
    _cerrar_cluster()

    return fusionados


def _evaluar_estado(velas, nivel, tipo, tolerancia, ultimo_ts, confirmacion_velas=2):
    """Estado del nivel despues de su ultimo toque. Devuelve
    (estado, ts_rotura, ts_flip):
      - "vivo": nunca hubo confirmacion_velas cierres consecutivos del otro
        lado de nivel +/- tolerancia.
      - "roto": se rompio y no hubo retest despues.
      - "flip": se rompio y despues el precio volvio a testear la zona
        desde el otro lado (cambio de rol)."""
    posteriores = [v for v in velas if v[0] > ultimo_ts]

    consecutivos = 0
    ts_rotura = None
    for v in posteriores:
        cierre = v[4]
        cruzo = cierre > nivel + tolerancia if tipo == "techo" else cierre < nivel - tolerancia
        if cruzo:
            consecutivos += 1
            if consecutivos >= confirmacion_velas:
                ts_rotura = v[0]
                break
        else:
            consecutivos = 0

    if ts_rotura is None:
        return "vivo", None, None

    despues_rotura = [v for v in posteriores if v[0] > ts_rotura]
    retoques, _, ts_flip = _contar_toques(despues_rotura, nivel, tolerancia)
    if retoques > 0:
        return "flip", ts_rotura, ts_flip
    return "roto", ts_rotura, None


def detectar_niveles(velas, k, tolerancia_atr, toques_min, periodo_atr=14):
    altos = [v[2] for v in velas]
    bajos = [v[3] for v in velas]
    cierres = [v[4] for v in velas]
    atr_serie = indicadores.atr(altos, bajos, cierres, periodo_atr)
    atr_ref = _mediana([a for a in atr_serie if a is not None])
    if atr_ref is None or atr_ref <= 0:
        raise ValueError("No hay suficiente historia para calcular el ATR de referencia "
                          f"(se necesitan al menos {periodo_atr + 1} velas).")
    tolerancia = tolerancia_atr * atr_ref

    idx_altos, idx_bajos = indicadores.extremos_locales(velas, k)

    candidatos = []
    for idx in idx_altos:
        nivel = altos[idx]
        toques, primero, ultimo = _contar_toques(velas, nivel, tolerancia)
        if toques >= toques_min:
            candidatos.append(dict(tipo="techo", precio=nivel, toques=toques,
                                    primero=primero, ultimo=ultimo))
    for idx in idx_bajos:
        nivel = bajos[idx]
        toques, primero, ultimo = _contar_toques(velas, nivel, tolerancia)
        if toques >= toques_min:
            candidatos.append(dict(tipo="suelo", precio=nivel, toques=toques,
                                    primero=primero, ultimo=ultimo))

    candidatos.sort(key=lambda d: d["precio"])
    return _fusionar(candidatos, tolerancia), atr_ref, tolerancia


def _rol_efectivo(niv):
    """Rol actual del nivel: si hizo flip, invierte el tipo original (un
    techo roto que fue retesteado desde arriba ahora actua de suelo, y
    viceversa). Niveles 'roto' sin retest no tienen rol vigente."""
    if niv["estado"] == "flip":
        return "suelo" if niv["tipo"] == "techo" else "techo"
    return niv["tipo"]


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _abrir_historial(coin, tf):
    """Abre (o crea) historial_<TF>.csv en modo append - mismo patron que
    marcador_tpsl.py/validador_niveles.py: CAMPOS_* como cabecera, header
    solo si el fichero es nuevo, flush por fila (el llamador cierra en su
    propio finally). Solo crece, nunca se reescribe."""
    ruta = _ruta_historial(coin, tf)
    nuevo = not os.path.exists(ruta)
    log = open(ruta, "a", newline="")
    writer = csv.DictWriter(log, fieldnames=CAMPOS_HISTORIAL)
    if nuevo:
        writer.writeheader()
        log.flush()
    return writer, log


def _anotar_historial(writer, log, coin, tf, evento, niv):
    writer.writerow({
        "fecha_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "fecha_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "coin": coin.upper(),
        "tf": tf,
        "evento": evento,
        "tipo": niv["tipo"],
        "precio": niv["precio"],
        "toques": niv["toques"],
        "estado": niv["estado"],
        "primero": _fmt_fecha(niv["primero"]),
        "ultimo": _fmt_fecha(niv["ultimo"]),
        "pid": os.getpid(),
    })
    log.flush()


def _nivel_a_dict(niv):
    """Nivel -> dict serializable para listado_<TF>.json. Solo campos
    PERSISTENTES/atemporales (precio, toques, estado, seguimiento
    incremental) - dist_pct/antig_dias de _analizar() se calculan contra
    el 'ahora' de esa llamada y quedarian obsoletos guardados."""
    return dict(tipo=niv["tipo"], precio=niv["precio"], toques=niv["toques"],
                primero=niv["primero"], ultimo=niv["ultimo"], estado=niv["estado"],
                dentro=niv.get("dentro", False), consecutivos=niv.get("consecutivos", 0))


def _guardar_listado(coin, tf, params, atr_ref, tolerancia, ultimo_ts_procesado, niveles):
    """Escritura atomica (tmp + os.replace), mismo patron que
    cursor_<COIN>.json de grabador_libro.py - un lector nunca ve el
    fichero a medio escribir."""
    ruta = _ruta_listado(coin, tf)
    datos = dict(params=params, atr_ref=atr_ref, tolerancia=tolerancia,
                 ultimo_ts_procesado=ultimo_ts_procesado,
                 niveles=[_nivel_a_dict(n) for n in niveles])
    tmp = ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump(datos, f)
    os.replace(tmp, ruta)


def _cargar_listado(coin, tf):
    ruta = _ruta_listado(coin, tf)
    if not os.path.exists(ruta):
        return None
    with open(ruta) as f:
        return json.load(f)


def _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas):
    velas, ruta = _cargar_velas(coin, tf, desde_dias)
    niveles, atr_ref, tolerancia = detectar_niveles(velas, k, tolerancia_atr, toques_min)
    precio_actual = velas[-1][4] if velas else None
    ts_final = velas[-1][0] if velas else None
    for niv in niveles:
        estado, ts_rotura, ts_flip = _evaluar_estado(
            velas, niv["precio"], niv["tipo"], tolerancia, niv["ultimo"], confirmacion_velas)
        niv["estado"] = estado
        niv["dist_pct"] = (niv["precio"] - precio_actual) / precio_actual * 100
        niv["antig_dias"] = (ts_final - niv["ultimo"]) / 86400000

    vigentes = [niv for niv in niveles if niv["estado"] in ("vivo", "flip")]
    techos = sorted((niv for niv in vigentes if _rol_efectivo(niv) == "techo"),
                     key=lambda d: abs(d["dist_pct"]))
    suelos = sorted((niv for niv in vigentes if _rol_efectivo(niv) == "suelo"),
                     key=lambda d: abs(d["dist_pct"]))

    return dict(ruta=ruta, velas=velas, niveles=niveles, atr_ref=atr_ref, tolerancia=tolerancia,
                precio_actual=precio_actual, techos=techos, suelos=suelos)


def _crear_listado(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas):
    """Barrido inicial de --actualizar: una sola pasada de detectar_niveles()
    + _evaluar_estado() sobre TODO el historico permanente de
    herramientas/velas/ (sin ventana deslizante - ya escanean todo de una
    vez). Crea listado_<TF>.json + historial_<TF>.csv desde cero. Se llama
    cuando no hay listado previo, o cuando los parametros pasados no
    coinciden con los del listado guardado (ver _actualizar)."""
    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias=None,
                  confirmacion_velas=confirmacion_velas)
    velas = r["velas"]
    if not velas:
        print(f"  {coin.upper()} {tf}: sin velas disponibles, nada que analizar.")
        return

    for niv in r["niveles"]:
        niv["dentro"] = False
        niv["consecutivos"] = 0

    params = dict(k=k, tolerancia_atr=tolerancia_atr, toques_min=toques_min,
                  confirmacion_velas=confirmacion_velas)

    writer, log = _abrir_historial(coin, tf)
    try:
        for niv in r["niveles"]:
            _anotar_historial(writer, log, coin, tf, "nivel_inicial", niv)
    finally:
        log.close()

    _guardar_listado(coin, tf, params, r["atr_ref"], r["tolerancia"], velas[-1][0], r["niveles"])
    print(f"  [OK] {coin.upper()} {tf}: barrido inicial, {len(r['niveles'])} niveles -> "
          f"{_ruta_listado(coin, tf)}")


def _actualizar_listado_con_vela(niveles, vela, tolerancia, confirmacion_velas, writer, log, coin, tf):
    """Actualiza el ESTADO de los niveles ya conocidos con UNA vela nueva -
    reproduce las mismas dos reglas que ya usan _contar_toques (agrupar
    toques por entrar/salir de tolerancia) y _evaluar_estado
    (confirmacion_velas cierres consecutivos para romper; primer retoque
    tras rotura = flip), pero aplicadas incrementalmente: O(niveles
    conocidos) por vela, sin re-escanear nada del historico."""
    ts, alto, bajo, cierre = vela[0], vela[2], vela[3], vela[4]
    for niv in niveles:
        if niv["estado"] == "flip":
            continue  # terminal, igual que _evaluar_estado - no sigue vigilando tras el flip

        cerca = (bajo - tolerancia) <= niv["precio"] <= (alto + tolerancia)
        if cerca:
            if not niv["dentro"]:
                niv["toques"] += 1
                niv["dentro"] = True
                if niv["estado"] == "roto":
                    niv["estado"] = "flip"
                    _anotar_historial(writer, log, coin, tf, "flip", niv)
                else:
                    _anotar_historial(writer, log, coin, tf, "toque", niv)
            niv["ultimo"] = ts
        else:
            niv["dentro"] = False

        if niv["estado"] == "vivo":
            cruzo = (cierre > niv["precio"] + tolerancia if niv["tipo"] == "techo"
                     else cierre < niv["precio"] - tolerancia)
            niv["consecutivos"] = niv["consecutivos"] + 1 if cruzo else 0
            if niv["consecutivos"] >= confirmacion_velas:
                niv["estado"] = "roto"
                _anotar_historial(writer, log, coin, tf, "rotura", niv)


def _nuevo_candidato(velas_todas, j, k, tolerancia, toques_min, niveles_existentes):
    """Comprueba si la vela en indice j (necesita k velas ya disponibles a
    cada lado en velas_todas) es un extremo local NUEVO - mismo criterio
    que indicadores.extremos_locales() pero para un solo punto, O(k). Si
    lo es y no coincide con un nivel ya conocido del mismo tipo (dentro de
    tolerancia - mismo criterio de _fusionar), cuenta sus toques sobre el
    historico ya cargado en memoria (velas_todas) - una sola vez por
    candidato nuevo, no en cada vela; los candidatos nuevos son raros
    (hace falta dominar 2k+1 velas), asi que esto se mantiene barato en
    conjunto aunque cada scan individual sea O(n). Devuelve una lista de
    0, 1 o 2 dicts de nivel nuevo (listos para anadir al listado)."""
    alto_j, bajo_j = velas_todas[j][2], velas_todas[j][3]
    vecinos_altos = [velas_todas[x][2] for x in range(j - k, j + k + 1) if x != j]
    vecinos_bajos = [velas_todas[x][3] for x in range(j - k, j + k + 1) if x != j]

    candidatos = []
    if alto_j >= max(vecinos_altos):
        candidatos.append(("techo", alto_j))
    if bajo_j <= min(vecinos_bajos):
        candidatos.append(("suelo", bajo_j))

    nuevos = []
    for tipo, precio in candidatos:
        ya_existe = any(n["tipo"] == tipo and abs(n["precio"] - precio) <= tolerancia
                        for n in niveles_existentes)
        if ya_existe:
            continue
        toques, primero, ultimo = _contar_toques(velas_todas, precio, tolerancia)
        if toques >= toques_min:
            nuevos.append(dict(tipo=tipo, precio=precio, toques=toques, primero=primero,
                               ultimo=ultimo, estado="vivo", dentro=False, consecutivos=0))
    return nuevos


def _actualizar(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas):
    """--actualizar: si no hay listado previo, o si cambio algun parametro
    respecto al guardado (Fran: 'solo lo haria si se cambiase algun
    parametro'), rehace el barrido completo desde cero (_crear_listado).
    Si el listado existe con los MISMOS parametros, solo procesa las velas
    nuevas desde la ultima vez, actualizando estados (barato) y detectando
    posibles niveles nuevos (barato, ver _nuevo_candidato) - nunca vuelve
    a recalcular lo ya anotado."""
    params_nuevos = dict(k=k, tolerancia_atr=tolerancia_atr, toques_min=toques_min,
                         confirmacion_velas=confirmacion_velas)
    estado = _cargar_listado(coin, tf)

    if estado is None:
        print(f"Sin listado previo para {coin.upper()} {tf} - barrido inicial.")
        _crear_listado(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas)
        return

    if estado["params"] != params_nuevos:
        print(f"Parametros distintos a los del listado guardado "
              f"({estado['params']} -> {params_nuevos}) - se rehace desde cero.")
        _crear_listado(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas)
        return

    velas_todas, _ = _cargar_velas(coin, tf, desde_dias=None)
    if not velas_todas:
        print(f"  {coin.upper()} {tf}: sin velas disponibles.")
        return

    ultimo_ts_procesado = estado["ultimo_ts_procesado"]
    nuevas_idx = [i for i, v in enumerate(velas_todas) if v[0] > ultimo_ts_procesado]
    if not nuevas_idx:
        print(f"  {coin.upper()} {tf} ya esta al dia (ultima vela procesada "
              f"{_fmt_fecha(ultimo_ts_procesado)}).")
        return

    niveles = estado["niveles"]
    tolerancia = estado["tolerancia"]

    writer, log = _abrir_historial(coin, tf)
    try:
        for i in nuevas_idx:
            vela = velas_todas[i]
            _actualizar_listado_con_vela(niveles, vela, tolerancia, confirmacion_velas,
                                         writer, log, coin, tf)
            j = i - k
            if k <= j < len(velas_todas) - k:
                for niv_nuevo in _nuevo_candidato(velas_todas, j, k, tolerancia, toques_min, niveles):
                    niveles.append(niv_nuevo)
                    _anotar_historial(writer, log, coin, tf, "nivel_nuevo", niv_nuevo)
    finally:
        log.close()

    _guardar_listado(coin, tf, params_nuevos, estado["atr_ref"], tolerancia,
                     velas_todas[-1][0], niveles)
    print(f"  [OK] {coin.upper()} {tf}: {len(nuevas_idx)} velas nuevas procesadas, "
          f"{len(niveles)} niveles en el listado.")


def _feed_niveles(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas, cada):
    """Modo daemon para --actualizar (2026-08-14): llama a _actualizar() en
    bucle cada 'cada' segundos - mismo patron que _feed_velas() en
    descargar_bit.py. _actualizar() ya es barata cuando no hay velas
    nuevas ('ya esta al dia', sin recalcular nada) - el barrido caro solo
    ocurre en la primera vuelta (o si cambian los parametros), asi que
    este bucle no tiene coste real la mayoria de las veces."""
    print(f"Feed de niveles: {coin.upper()} {tf} (cada={cada:.0f}s). Ctrl+C para parar.")
    try:
        while True:
            try:
                _actualizar(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas)
            except Exception as e:
                print(f"  (aviso) {coin} {tf}: {e}")
            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado por el usuario.")


def _avisos_zona_indecision(techos, suelos):
    """Un techo/suelo NO roto sigue vigente sin importar de que lado quedo
    el precio - pero si el mas cercano ya quedo del lado 'equivocado' (el
    precio lo supero o lo perdio sin ruptura confirmada), no hay una banda
    techo-arriba/suelo-abajo limpia alrededor del precio: es zona de
    indecision, no de rango operable. Mejor esperar a que el precio
    confirme direccion (rompa de verdad, o vuelva a respetar el nivel)
    antes de tomar esa zona como valida para entradas/salidas."""
    avisos = []
    if techos and techos[0]["dist_pct"] < 0:
        avisos.append(f"el techo vigente mas cercano ({techos[0]['precio']:.4f}) ya quedo "
                       f"por DEBAJO del precio, sin ruptura confirmada")
    if suelos and suelos[0]["dist_pct"] > 0:
        avisos.append(f"el suelo vigente mas cercano ({suelos[0]['precio']:.4f}) ya quedo "
                       f"por ENCIMA del precio, sin ruptura confirmada")
    return avisos


def _imprimir_niveles(niv_lista, etiqueta):
    print(f"\n{etiqueta}, del mas cercano al mas lejos:")
    for niv in niv_lista:
        origen_de = "suelo" if niv["tipo"] == "techo" else "techo"
        origen = f"  [flip desde {origen_de}]" if niv["estado"] == "flip" else ""
        print(f"  {niv['precio']:>12.4f}  dist {niv['dist_pct']:>7.2f}%  "
              f"toques {niv['toques']:>3}  antig {niv['antig_dias']:>6.1f}d{origen}")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--actualizar":
        args = args[1:]
        if len(args) < 2:
            print("Uso: python niveles.py --actualizar <coin> <tf> "
                  "--k .. --tolerancia-atr .. --toques-min .. [--confirmacion-velas 2] [--cada segundos]")
            return
        coin, tf = args[0], args[1]
        resto = args[2:]
        k = tolerancia_atr = toques_min = None
        confirmacion_velas = 2
        cada = None
        i = 0
        while i < len(resto):
            if resto[i] == "--k":
                i += 1; k = int(resto[i])
            elif resto[i] == "--tolerancia-atr":
                i += 1; tolerancia_atr = float(resto[i])
            elif resto[i] == "--toques-min":
                i += 1; toques_min = int(resto[i])
            elif resto[i] == "--confirmacion-velas":
                i += 1; confirmacion_velas = int(resto[i])
            elif resto[i] == "--cada":
                i += 1; cada = float(resto[i])
            i += 1
        if k is None or tolerancia_atr is None or toques_min is None:
            print("Faltan parametros obligatorios: --k, --tolerancia-atr, --toques-min")
            print("(sin defaults a proposito - ver cabecera del archivo)")
            return
        if cada is None:
            _actualizar(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas)
        else:
            _feed_niveles(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas, cada)
        return

    if len(args) < 2:
        print(__doc__)
        return
    coin, tf = args[0], args[1]
    resto = args[2:]

    k = tolerancia_atr = toques_min = None
    desde_dias = None
    confirmacion_velas = 2
    tf_macro = None
    i = 0
    while i < len(resto):
        if resto[i] == "--k":
            i += 1; k = int(resto[i])
        elif resto[i] == "--tolerancia-atr":
            i += 1; tolerancia_atr = float(resto[i])
        elif resto[i] == "--toques-min":
            i += 1; toques_min = int(resto[i])
        elif resto[i] == "--desde-dias":
            i += 1; desde_dias = float(resto[i])
        elif resto[i] == "--confirmacion-velas":
            i += 1; confirmacion_velas = int(resto[i])
        elif resto[i] == "--tf-macro":
            i += 1; tf_macro = resto[i]
        i += 1

    if k is None or tolerancia_atr is None or toques_min is None:
        print("Faltan parametros obligatorios: --k, --tolerancia-atr, --toques-min")
        print("(sin defaults a proposito - ver cabecera del archivo)")
        return

    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    velas, niveles = r["velas"], r["niveles"]

    print(f"Historico: {r['ruta']} ({len(velas)} velas"
          + (f", ultimos {desde_dias:.0f} dias" if desde_dias else ", todo") + ")")
    if velas:
        print(f"Rango: {_fmt_fecha(velas[0][0])} -> {_fmt_fecha(velas[-1][0])}")

    print(f"\nk={k}  tolerancia_atr={tolerancia_atr}  toques_min={toques_min}  "
          f"confirmacion_velas={confirmacion_velas}  "
          f"(ATR referencia={r['atr_ref']:.4f}, tolerancia en precio={r['tolerancia']:.4f})")
    print(f"Precio actual: {r['precio_actual']:.4f}")
    print(f"\n{len(niveles)} niveles confirmados:\n")
    print(f"{'tipo':<7} {'precio':>12} {'toques':>7}  {'estado':<12} {'dist%':>8}  {'antig(d)':>8}  "
          f"{'primer toque':>16}   {'ultimo toque':>16}")
    for niv in sorted(niveles, key=lambda d: -d["precio"]):
        if niv["estado"] == "flip":
            rol_nuevo = "suelo" if niv["tipo"] == "techo" else "techo"
            estado_txt = f"flip->{rol_nuevo}"
        else:
            estado_txt = niv["estado"]
        print(f"{niv['tipo']:<7} {niv['precio']:>12.4f} {niv['toques']:>7}  {estado_txt:<12} "
              f"{niv['dist_pct']:>7.2f}%  {niv['antig_dias']:>8.1f}  "
              f"{_fmt_fecha(niv['primero']):>16}   {_fmt_fecha(niv['ultimo']):>16}")

    print(f"\n=== Niveles vigentes ahora (vivos/flip, roto excluido) ===")
    _imprimir_niveles(r["techos"], "Techos (resistencia)")
    _imprimir_niveles(r["suelos"], "Suelos (soporte)")
    for aviso in _avisos_zona_indecision(r["techos"], r["suelos"]):
        print(f"\nAVISO: {aviso} -> zona de indecision, no de rango operable. "
              f"Esperar a que el precio confirme direccion antes de operar aca.")

    if tf_macro is None:
        return

    rm = _analizar(coin, tf_macro, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas)
    techo_macro = min((n for n in rm["techos"] if n["dist_pct"] > 0),
                       key=lambda d: d["dist_pct"], default=None)
    suelo_macro = max((n for n in rm["suelos"] if n["dist_pct"] < 0),
                       key=lambda d: d["dist_pct"], default=None)

    print(f"\n=== Rango macro (tf {tf_macro}, mismos k/tolerancia-atr/toques-min) ===")
    print(f"Tope superior: {techo_macro['precio']:.4f} (dist {techo_macro['dist_pct']:.2f}%)"
          if techo_macro else "Tope superior: sin techo macro vivo por encima del precio")
    print(f"Tope inferior: {suelo_macro['precio']:.4f} (dist {suelo_macro['dist_pct']:.2f}%)"
          if suelo_macro else "Tope inferior: sin suelo macro vivo por debajo del precio")

    lo = suelo_macro["precio"] if suelo_macro else float("-inf")
    hi = techo_macro["precio"] if techo_macro else float("inf")
    techos_ajustados = [n for n in r["techos"] if lo <= n["precio"] <= hi]
    suelos_ajustados = [n for n in r["suelos"] if lo <= n["precio"] <= hi]

    print(f"\n=== Niveles de {tf} ajustados al rango macro ===")
    _imprimir_niveles(techos_ajustados, "Techos (resistencia)")
    _imprimir_niveles(suelos_ajustados, "Suelos (soporte)")
    for aviso in _avisos_zona_indecision(techos_ajustados, suelos_ajustados):
        print(f"\nAVISO: {aviso} -> zona de indecision, no de rango operable. "
              f"Esperar a que el precio confirme direccion antes de operar aca.")


if __name__ == "__main__":
    main()
