import sys
import time
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

DIR_NIVELES = Path(__file__).resolve().parent
DIR_NEO = DIR_NIVELES.parent
sys.path.insert(0, str(DIR_NEO))
from indicadores import indicadores

PRIORIDAD_ESTADO = {"vivo": 3, "flip": 2, "roto": 1}


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _contar_toques(velas, nivel, tol_serie):
    toques = 0
    dentro = False
    velas_dentro = 0
    primero = ultimo = None
    for i, v in enumerate(velas):
        tol = tol_serie[i]
        alto, bajo = v[2], v[3]
        cerca = (bajo - tol) <= nivel <= (alto + tol)
        if cerca:
            velas_dentro += 1
            if not dentro:
                toques += 1
                dentro = True
                if primero is None:
                    primero = v[0]
            ultimo = v[0]
        else:
            dentro = False
    return toques, primero, ultimo, velas_dentro


def _fuerza(toques, velas_dentro):
    if not velas_dentro:
        return 0.0
    return toques / velas_dentro


def _fusionar(niveles, tolerancia):
    if not niveles:
        return []

    fusionados = []
    for tipo in ("techo", "suelo"):
        del_tipo = sorted((n for n in niveles if n["tipo"] == tipo),
                          key=lambda d: d["precio"])
        if not del_tipo:
            continue

        cluster = [del_tipo[0]]
        ancla = del_tipo[0]["precio"]
        for niv in del_tipo[1:]:
            if abs(niv["precio"] - ancla) <= tolerancia:
                cluster.append(niv)
            else:
                fusionados.append(max(cluster, key=lambda d: (d["fuerza"], d["toques"])))
                cluster = [niv]
                ancla = niv["precio"]
        fusionados.append(max(cluster, key=lambda d: (d["fuerza"], d["toques"])))

    fusionados.sort(key=lambda d: d["precio"])
    return fusionados


def _imponer_separacion(niveles, separacion_min):
    if separacion_min <= 0 or not niveles:
        return list(niveles)

    ordenados = sorted(niveles, key=lambda d: d["precio"])
    salida = [ordenados[0]]
    clave = lambda d: (PRIORIDAD_ESTADO.get(d.get("estado"), 0), d["fuerza"], d["toques"])
    for niv in ordenados[1:]:
        if niv["precio"] - salida[-1]["precio"] >= separacion_min:
            salida.append(niv)
        elif clave(niv) > clave(salida[-1]):
            salida[-1] = niv
    return salida


def _evaluar_estado(velas, ts_velas, nivel, tipo, tol_serie, ts_formacion, confirmacion_velas=2):
    i0 = bisect_right(ts_velas, ts_formacion)

    ultima_rotura = None
    consecutivos = 0
    for j in range(i0, len(velas)):
        cierre = velas[j][4]
        tol = tol_serie[j]
        cruzo = cierre > nivel + tol if tipo == "techo" else cierre < nivel - tol
        if cruzo:
            consecutivos += 1
            if consecutivos == confirmacion_velas:
                ultima_rotura = velas[j][0]
        else:
            consecutivos = 0

    if ultima_rotura is None:
        return "vivo", None, None

    i1 = bisect_right(ts_velas, ultima_rotura)
    retoques, _, ts_flip, _ = _contar_toques(velas[i1:], nivel, tol_serie[i1:])
    if retoques > 0:
        return "flip", ultima_rotura, ts_flip
    return "roto", ultima_rotura, None


def _serie_atr(altos, bajos, cierres, periodo_atr):
    serie = indicadores.atr(altos, bajos, cierres, periodo_atr)
    primero = next((a for a in serie if a is not None and a > 0), None)
    if primero is None:
        return None
    return [a if a is not None and a > 0 else primero for a in serie]


def detectar_niveles(velas, k, tolerancia_atr, toques_min, periodo_atr=14, verbose=False):
    ts_inicio = time.time()

    altos = [v[2] for v in velas]
    bajos = [v[3] for v in velas]
    cierres = [v[4] for v in velas]

    if verbose:
        print(f"  [1/5] Datos: {len(velas)} velas", flush=True)

    ts = time.time()
    atr_serie = _serie_atr(altos, bajos, cierres, periodo_atr)
    if atr_serie is None:
        raise ValueError(f"ATR insuficiente ({periodo_atr + 1} velas min)")
    tol_serie = [tolerancia_atr * a for a in atr_serie]

    atr_actual = atr_serie[-1]
    tol_actual = tol_serie[-1]
    if verbose:
        print(f"  [2/5] ATR actual: {atr_actual:.2f} -> banda +-{tol_actual:.2f} "
              f"(min {min(tol_serie):.2f} / max {max(tol_serie):.2f}) ({time.time()-ts:.1f}s)", flush=True)

    ts = time.time()
    idx_altos, idx_bajos = indicadores.extremos_locales(velas, k)
    if verbose:
        print(f"  [3/5] Extremos: {len(idx_altos)}up {len(idx_bajos)}down ({time.time()-ts:.1f}s)", flush=True)

    ts = time.time()
    candidatos = []
    for idx_lista, tipo, precios in ((idx_altos, "techo", altos), (idx_bajos, "suelo", bajos)):
        for idx in idx_lista:
            nivel = precios[idx]
            toques, primero, ultimo, dentro = _contar_toques(velas, nivel, tol_serie)
            if toques >= toques_min:
                candidatos.append(dict(
                    tipo=tipo, precio=nivel, toques=toques,
                    primero=primero, ultimo=ultimo,
                    velas_dentro=dentro, fuerza=_fuerza(toques, dentro)))
    if verbose:
        print(f"  [4/5] Toques: {len(candidatos)} candidatos ({time.time()-ts:.1f}s)", flush=True)

    ts = time.time()
    fusionados = _fusionar(candidatos, tol_actual)
    if verbose:
        print(f"  [5/5] Fusion: {len(fusionados)} niveles ({time.time()-ts:.1f}s)", flush=True)
        print(f"  DONE {time.time()-ts_inicio:.1f}s", flush=True)

    return fusionados, atr_serie, tol_serie


def _evaluar_niveles(velas, niveles, confirmacion_velas, tol_serie):
    precio_actual = velas[-1][4] if velas else None
    ts_final = velas[-1][0] if velas else None
    ts_velas = [v[0] for v in velas]
    tol_actual = tol_serie[-1] if tol_serie else 0.0

    for niv in niveles:
        estado, ts_rotura, ts_flip = _evaluar_estado(
            velas, ts_velas, niv["precio"], niv["tipo"], tol_serie,
            niv["primero"], confirmacion_velas)
        niv["estado"] = estado
        niv["ts_rotura"] = ts_rotura
        niv["ts_flip"] = ts_flip
        niv["fecha_rotura"] = _fmt_fecha(ts_rotura) if ts_rotura else None
        niv["dias_desde_rotura"] = (ts_final - ts_rotura) / 86400000 if ts_rotura and ts_final else None
        niv["dist_pct"] = (niv["precio"] - precio_actual) / precio_actual * 100 if precio_actual and precio_actual > 0 else 0
        niv["antig_dias"] = (ts_final - niv["ultimo"]) / 86400000 if ts_final and ts_final > niv["ultimo"] else 0
        if precio_actual is None:
            niv["vigente"] = False
        elif niv["tipo"] == "techo":
            niv["vigente"] = (niv["precio"] - precio_actual) > tol_actual
        else:
            niv["vigente"] = (precio_actual - niv["precio"]) > tol_actual

    return precio_actual, ts_final


def _filtrar_niveles(niveles, max_dist_pct, max_antig_dias):
    salida = niveles
    if max_dist_pct is not None:
        salida = [n for n in salida if abs(n["dist_pct"]) <= max_dist_pct]
    if max_antig_dias is not None:
        salida = [n for n in salida if n["antig_dias"] <= max_antig_dias]
    return salida


def calcular(velas, cfg):
    niveles, atr_serie, tol_serie = detectar_niveles(
        velas, cfg["k"], cfg["tolerancia_atr"], cfg["toques_min"],
        periodo_atr=cfg["periodo_atr"])

    precio_actual, ts_final = _evaluar_niveles(
        velas, niveles, cfg["confirmacion_velas"], tol_serie)

    niveles = _filtrar_niveles(niveles, cfg["max_dist_pct"], cfg["max_antig_dias"])
    separacion_min = cfg["separacion_min_atr"] * atr_serie[-1]
    niveles = _imponer_separacion(niveles, separacion_min)
    niveles.sort(key=lambda d: d["precio"])

    return niveles, {
        "atr_actual": round(atr_serie[-1], 4),
        "tolerancia_actual": round(tol_serie[-1], 4),
        "tolerancia_min": round(min(tol_serie), 4),
        "tolerancia_max": round(max(tol_serie), 4),
        "separacion_min": round(separacion_min, 4),
        "precio_actual": precio_actual,
        "ts_ultima_vela": ts_final,
        "velas_usadas": len(velas),
    }
