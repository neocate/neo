# Uso:
#   python herramientas/niveles.py <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 3 [--desde-dias 90] [--tf-macro 4h]
#   python herramientas/niveles.py --actualizar <coin> <tf> --k .. --tolerancia-atr .. --toques-min ..
#                                   [--confirmacion-velas 2] [--cada segundos]
#
# Ejemplos:
#   python herramientas/niveles.py btc 4h --k 2 --tolerancia-atr 0.25 --toques-min 3
#   python herramientas/niveles.py btc 15m --k 5 --tolerancia-atr 0.15 --toques-min 4 --desde-dias 30
#   python herramientas/niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 4 --desde-dias 90 --tf-macro 4h
#   python herramientas/niveles.py --actualizar eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 4
#   python herramientas/niveles.py --actualizar btc 4h --k 3 --tolerancia-atr 0.25 --toques-min 4 --cada 60

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

PARAMS_DEFECTO = {
    "k": 3, "tolerancia_atr": 0.25, "toques_min": 4, "confirmacion_velas": 2,
}

LIMITES_PARAMS = {
    "k": (1, 100), "tolerancia_atr": (0.01, 10.0), "toques_min": (1, 1000),
    "confirmacion_velas": (1, 100),
}


def _ruta_config():
    return os.path.join(DIR_NIVELES, "niveles_config.json")


_cache_params = None
_cache_mtime = None
_cache_verificado = 0.0

INTERVALO_RECOMPROBAR = 2.0


def cargar_params():
    """cargar_params() -> dict, PARAMS_DEFECTO + niveles_config.json (si existe)"""
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
    """guardar_params(params: dict) -> None. Escribe en niveles_config.json, valida contra LIMITES_PARAMS."""
    a_guardar = dict(cargar_params())
    for k, v in params.items():
        if k not in PARAMS_DEFECTO:
            continue
        lo, hi = LIMITES_PARAMS[k]
        if not (lo <= v <= hi):
            raise ValueError(f"{k}={v} fuera de rango [{lo},{hi}]")
        a_guardar[k] = v
    os.makedirs(DIR_NIVELES, exist_ok=True)
    ruta = _ruta_config()
    tmp = ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump(a_guardar, f)
    os.replace(tmp, ruta)


def _cargar_velas(coin, tf, desde_dias=None):
    """_cargar_velas(coin, tf, desde_dias=None) -> (velas: list, ruta: str)"""
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
        next(r)
        for row in r:
            ts = int(row[0])
            if corte_ms is not None and ts < corte_ms:
                continue
            velas.append([ts, float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])])
    return velas, ruta


def _carpeta_niveles(coin):
    """_carpeta_niveles(coin: str) -> str"""
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
    """_contar_toques(velas, nivel: float, tolerancia: float) -> (toques: int, ts_primero, ts_ultimo)"""
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
    """_fusionar(niveles: list[dict], tolerancia: float) -> list[dict]"""
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
    """_evaluar_estado(velas, nivel, tipo, tolerancia, ultimo_ts, confirmacion_velas=2) -> (estado, ts_rotura, ts_flip)"""
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
    """detectar_niveles(velas, k, tolerancia_atr, toques_min, periodo_atr=14) -> (niveles: list[dict], atr_ref: float, tolerancia: float)"""
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
    """_rol_efectivo(niv: dict) -> 'techo'|'suelo'"""
    if niv["estado"] == "flip":
        return "suelo" if niv["tipo"] == "techo" else "techo"
    return niv["tipo"]


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _abrir_historial(coin, tf):
    """_abrir_historial(coin, tf) -> (writer, log)"""
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
    return dict(tipo=niv["tipo"], precio=niv["precio"], toques=niv["toques"],
                primero=niv["primero"], ultimo=niv["ultimo"], estado=niv["estado"],
                dentro=niv.get("dentro", False), consecutivos=niv.get("consecutivos", 0))


def _guardar_listado(coin, tf, params, atr_ref, tolerancia, ultimo_ts_procesado, niveles):
    """_guardar_listado(coin, tf, params, atr_ref, tolerancia, ultimo_ts_procesado, niveles) -> None"""
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
    """_analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas) -> dict"""
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
    """_crear_listado(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas) -> None"""
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


def _anotar_diferencias(niveles_viejos, niveles_nuevos, tolerancia, writer, log, coin, tf):
    """_anotar_diferencias(niveles_viejos, niveles_nuevos, tolerancia, writer, log, coin, tf) -> (n_nuevos, n_toques, n_transiciones, n_desaparecidos)"""
    usados = set()
    n_nuevos = n_toques = n_transiciones = 0
    for nuevo in niveles_nuevos:
        pareja = None
        for i, viejo in enumerate(niveles_viejos):
            if i in usados or viejo["tipo"] != nuevo["tipo"]:
                continue
            if abs(viejo["precio"] - nuevo["precio"]) <= tolerancia:
                pareja = i
                break
        if pareja is None:
            _anotar_historial(writer, log, coin, tf, "nivel_nuevo", nuevo)
            n_nuevos += 1
            continue
        usados.add(pareja)
        viejo = niveles_viejos[pareja]
        if nuevo["estado"] != viejo["estado"] and nuevo["estado"] in ("roto", "flip"):
            _anotar_historial(writer, log, coin, tf,
                              "flip" if nuevo["estado"] == "flip" else "rotura", nuevo)
            n_transiciones += 1
        elif nuevo["estado"] == viejo["estado"] and nuevo["ultimo"] > viejo["ultimo"]:
            _anotar_historial(writer, log, coin, tf, "toque", nuevo)
            n_toques += 1

    n_desaparecidos = len(niveles_viejos) - len(usados)
    return n_nuevos, n_toques, n_transiciones, n_desaparecidos


_verificado_esta_sesion = set()


def _actualizar(coin, tf, k=None, tolerancia_atr=None, toques_min=None, confirmacion_velas=None):
    """_actualizar(coin, tf, k=None, tolerancia_atr=None, toques_min=None, confirmacion_velas=None) -> None"""
    clave_sesion = (coin, tf)
    forzar_recalculo = clave_sesion not in _verificado_esta_sesion
    _verificado_esta_sesion.add(clave_sesion)

    params_actuales = cargar_params()
    if k is None:
        k = params_actuales["k"]
    if tolerancia_atr is None:
        tolerancia_atr = params_actuales["tolerancia_atr"]
    if toques_min is None:
        toques_min = params_actuales["toques_min"]
    if confirmacion_velas is None:
        confirmacion_velas = params_actuales["confirmacion_velas"]

    params_nuevos = dict(k=k, tolerancia_atr=tolerancia_atr, toques_min=toques_min,
                         confirmacion_velas=confirmacion_velas)
    listado_anterior = _cargar_listado(coin, tf)

    if listado_anterior is None:
        print(f"Sin listado previo para {coin.upper()} {tf} - barrido inicial.")
        _crear_listado(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas)
        return

    if listado_anterior["params"] != params_nuevos:
        print(f"Parametros distintos a los del listado guardado "
              f"({listado_anterior['params']} -> {params_nuevos}) - se recalcula con los nuevos.")

    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias=None,
                  confirmacion_velas=confirmacion_velas)
    velas = r["velas"]
    if not velas:
        print(f"  {coin.upper()} {tf}: sin velas disponibles.")
        return

    if (not forzar_recalculo
            and velas[-1][0] <= listado_anterior["ultimo_ts_procesado"]
            and listado_anterior["params"] == params_nuevos):
        print(f"  {coin.upper()} {tf} ya esta al dia (ultima vela procesada "
              f"{_fmt_fecha(listado_anterior['ultimo_ts_procesado'])}).")
        return

    writer, log = _abrir_historial(coin, tf)
    try:
        n_nuevos, n_toques, n_transiciones, n_desaparecidos = _anotar_diferencias(
            listado_anterior["niveles"], r["niveles"], r["tolerancia"], writer, log, coin, tf)
    finally:
        log.close()

    _guardar_listado(coin, tf, params_nuevos, r["atr_ref"], r["tolerancia"], velas[-1][0], r["niveles"])
    aviso_desaparecidos = f", {n_desaparecidos} ya no reproducibles (se sueltan)" if n_desaparecidos else ""
    print(f"  [OK] {coin.upper()} {tf}: recalculado ({len(velas)} velas) - {len(r['niveles'])} niveles "
          f"({n_nuevos} nuevos, {n_toques} con toque nuevo, {n_transiciones} con cambio de "
          f"estado{aviso_desaparecidos}).")


def _feed_niveles(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas, cada):
    """_feed_niveles(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas, cada) -> None, daemon"""
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
    """_avisos_zona_indecision(techos, suelos) -> list[str]"""
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
                  "[--k ..] [--tolerancia-atr ..] [--toques-min ..] [--confirmacion-velas ..] [--cada segundos]")
            print("Los 4 primeros son opcionales - si se omiten se toman de "
                  f"{_ruta_config()} (o de PARAMS_DEFECTO si ese fichero no existe).")
            return
        coin, tf = args[0], args[1]
        resto = args[2:]
        k = tolerancia_atr = toques_min = confirmacion_velas = None
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
