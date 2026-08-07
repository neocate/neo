# ---------------------------------------------------------------
# niveles_soporte.py - Detecta suelos/techos (soporte/resistencia) de UN TF
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
#   python herramientas/niveles_soporte.py <coin> <tf> --k 3 --tolerancia-atr 0.25 --toques-min 3 [--desde-dias 90] [--tf-macro 4h]
#
# Ejemplos:
#   python herramientas/niveles_soporte.py btc 4h --k 2 --tolerancia-atr 0.25 --toques-min 3
#   python herramientas/niveles_soporte.py btc 15m --k 5 --tolerancia-atr 0.15 --toques-min 4 --desde-dias 30
#   python herramientas/niveles_soporte.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 4 --desde-dias 90 --tf-macro 4h
# ---------------------------------------------------------------

import csv
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import indicadores
from herramientas.descargar_bit import _archivo as _archivo_bitget

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
            f"No hay historico de {coin.upper()} {tf} en herramientas/libro/ - "
            f"bajalo primero con: python herramientas/descargar_bit.py {coin} {tf}")

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
