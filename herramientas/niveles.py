#!/usr/bin/env python3
"""
DETECTAR NIVELES DE SOPORTE/RESISTENCIA EN VIVO

EJEMPLOS DE USO:

=== BARRIDO INICIAL (una sola vez) ===
Windows:
  python herramientas/niveles.py eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3

Linux/NAS:
  python herramientas/niveles.py eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3

=== MODO VIVO (updates cada N segundos) ===
Windows:
  python herramientas/niveles.py --actualizar eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3 --cada 300

Linux/NAS:
  python herramientas/niveles.py --actualizar eth 4h --k 3 --tolerancia-atr 0.25 --toques-min 3 --cada 300

=== CONFIGURACIÓN DESDE JSON ===
Edita: json/niveles.json
Cambios se aplican en el siguiente ciclo (sin reiniciar)

=== AUDITAR NIVELES ===
Windows/Linux/NAS:
  python herramientas/auditar_niveles.py eth 4h

=== ESTRUCTURA ESPERADA ===
herramientas/velas/ETH/4h_bitget.csv      (historico velas, descargado con descargar_bit.py)
herramientas/niveles/ETH/listado_4h.json  (guardado automaticamente)
herramientas/niveles/ETH/historial_4h.csv (log de cambios)
json/niveles.json                          (config parametros)
"""

import csv
import json
import os
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import indicadores
from herramientas.descargar_bit import _archivo_velas as _archivo_bitget

DIR_NIVELES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "niveles")
RUTA_JSON_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "json", "niveles.json")


def _verificar_directorio_niveles(coin):
    """Verifica que exista directorio. Si no, muestra instrucciones y detiene."""
    carpeta = os.path.join(DIR_NIVELES, coin.upper())
    if not os.path.exists(carpeta):
        print(f"\n❌ ERROR: Directorio de guardado no existe:")
        print(f"   {carpeta}\n")
        print(f"Ejecuta uno de estos comandos según tu SO:\n")
        print(f"  Windows (cmd/PowerShell):")
        print(f"    mkdir \"{carpeta}\"\n")
        print(f"  Linux/macOS/NAS:")
        print(f"    mkdir -p \"{carpeta}\"\n")
        sys.exit(1)

Vela = namedtuple("Vela", ["ts", "h", "l", "c", "vol"])
Nivel = namedtuple("Nivel", ["tipo", "precio", "toques", "primero", "ultimo", "estado", "dentro", "consecutivos"])

CAMPOS_HISTORIAL = [
    "fecha_ms", "fecha_utc", "coin", "tf", "evento", "tipo", "precio",
    "toques", "estado", "primero", "ultimo", "pid",
]

PARAMS_DEFECTO = {
    "k": 3, "tolerancia_atr": 0.25, "toques_min": 3, "confirmacion_velas": 2, "gap_multiplier": 1.5,
}

LIMITES_PARAMS = {
    "k": (1, 100), "tolerancia_atr": (0.01, 10.0), "toques_min": (1, 1000),
    "confirmacion_velas": (1, 100), "gap_multiplier": (0.5, 5.0),
}

def cargar_config():
    """Carga config desde niveles.json + defaults (sin caché - lee siempre)."""
    config = {"parametros": dict(PARAMS_DEFECTO), "atr": {"periodo": 14},
              "logica": {"ponderar_volumen": True, "detectar_gaps": True, "usar_open_ruptura": True},
              "historial": {"registrar_transiciones_positivas": True}}

    if not os.path.exists(RUTA_JSON_CONFIG):
        return config

    try:
        with open(RUTA_JSON_CONFIG) as f:
            guardado = json.load(f)
        if "parametros" in guardado:
            for k, v in guardado["parametros"].items():
                if k in PARAMS_DEFECTO:
                    lo, hi = LIMITES_PARAMS[k]
                    if lo <= v <= hi:
                        config["parametros"][k] = v
        if "atr" in guardado and "periodo" in guardado["atr"]:
            config["atr"]["periodo"] = int(guardado["atr"]["periodo"])
        if "logica" in guardado:
            config["logica"].update(guardado["logica"])
        if "historial" in guardado:
            config["historial"].update(guardado["historial"])
    except (OSError, ValueError, json.JSONDecodeError):
        pass

    return config


def _ruta_listado(coin, tf):
    return os.path.join(os.path.join(DIR_NIVELES, coin.upper()), f"listado_{tf}.json")


def _ruta_historial(coin, tf):
    return os.path.join(os.path.join(DIR_NIVELES, coin.upper()), f"historial_{tf}.csv")


def _carpeta_niveles(coin):
    carpeta = os.path.join(DIR_NIVELES, coin.upper())
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _cargar_velas(coin, tf, desde_dias=None, desde_ts=None, verbose=False):
    """Carga velas. Soporta Bitget (herramientas/velas/) o Binance (historicos/)."""
    ruta = _archivo_bitget(coin, tf)

    # Si no está en velas/, busca en historicos/
    if not os.path.exists(ruta):
        from pathlib import Path
        dir_hist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "historicos")
        archivos = list(Path(dir_hist).glob(f"*_{coin}_{tf}_binance.csv"))
        if archivos:
            ruta = str(archivos[-1])  # toma el más reciente
        else:
            raise FileNotFoundError(
                f"No hay historico de {coin.upper()} {tf} en herramientas/velas/ ni en historicos/")

    if verbose:
        print(f"  Leyendo {ruta}...", end="", flush=True)

    corte_ms = None
    if desde_dias is not None:
        corte_ms = int((datetime.now(timezone.utc) - timedelta(days=desde_dias)).timestamp() * 1000)
    elif desde_ts is not None:
        corte_ms = desde_ts

    velas = []
    con_barra = False
    with open(ruta, newline='') as f:
        r = csv.reader(f)
        next(r)
        for i, row in enumerate(r):
            if verbose and i % 500000 == 0 and i > 0:
                if not con_barra:
                    print()
                    con_barra = True
                print(f"    {i:,} velas cargadas...", flush=True)

            ts = int(row[0])
            if corte_ms is not None and ts <= corte_ms:
                continue
            velas.append(Vela(ts=ts, h=float(row[3]), l=float(row[4]),
                             c=float(row[5]), vol=float(row[6])))

    if verbose:
        print(f" ✓ {len(velas):,} velas")

    return velas, ruta


def _mediana(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _ruta_extremos(coin, tf):
    """Ruta para guardar extremos confirmados."""
    return os.path.join(_carpeta_niveles(coin), f"extremos_{tf}.json")


def _cargar_extremos_guardados(coin, tf):
    """Carga extremos ya encontrados."""
    ruta = _ruta_extremos(coin, tf)
    if not os.path.exists(ruta):
        return [], None
    try:
        with open(ruta) as f:
            datos = json.load(f)
        return datos.get("extremos", []), datos.get("ultimo_ts", None)
    except (OSError, ValueError, json.JSONDecodeError):
        return [], None


def _guardar_extremos(coin, tf, extremos, ultimo_ts):
    """Guarda extremos encontrados."""
    ruta = _ruta_extremos(coin, tf)
    _carpeta_niveles(coin)
    tmp = ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"extremos": extremos, "ultimo_ts": ultimo_ts}, f)
    os.replace(tmp, ruta)


def _buscar_extremos_velas_nuevas(velas_todas, velas_nuevas, k, extremos_anteriores):
    """Busca extremos solo en velas nuevas, comparando con contexto."""
    if not velas_nuevas:
        return []

    idx_inicio_nuevas = len(velas_todas) - len(velas_nuevas)
    idx_altos, idx_bajos = indicadores.extremos_locales(velas_todas, k)

    nuevos_extremos = []
    for idx in idx_altos:
        if idx >= idx_inicio_nuevas - k:  # extremos cerca de la frontera
            precio = velas_todas[idx].h
            ya_existe = any(e["idx"] == idx for e in extremos_anteriores)
            if not ya_existe:
                nuevos_extremos.append({"idx": idx, "tipo": "alto", "precio": precio})

    for idx in idx_bajos:
        if idx >= idx_inicio_nuevas - k:
            precio = velas_todas[idx].l
            ya_existe = any(e["idx"] == idx for e in extremos_anteriores)
            if not ya_existe:
                nuevos_extremos.append({"idx": idx, "tipo": "bajo", "precio": precio})

    return nuevos_extremos


def _contar_toques(velas, nivel, tolerancia, ponderar_volumen=True, detectar_gaps=True, gap_multiplier=1.5):
    """Cuenta toques ponderando por volumen y detectando gaps (saltos sin tocar)."""
    toques = 0.0
    dentro = False
    primero = ultimo = None

    for i, v in enumerate(velas):
        # Toque dentro del rango de la vela
        toco = (v.l - tolerancia) <= nivel <= (v.h + tolerancia)

        if toco:
            if not dentro:
                peso = v.vol if ponderar_volumen else 1.0
                toques += peso
                dentro = True
                if primero is None:
                    primero = v.ts
            ultimo = v.ts
        else:
            dentro = False

        # Detectar gap: nivel saltado entre close anterior y open actual
        if detectar_gaps and i > 0:
            vela_anterior = velas[i - 1]
            close_anterior = vela_anterior.c
            open_actual = v.c if i == 0 else velas[i-1].c  # simulamos open con close anterior

            # ¿El nivel está entre close anterior y close actual (gap simulado)?
            if (close_anterior < nivel < v.c) or (v.c < nivel < close_anterior):
                peso = v.vol * gap_multiplier if ponderar_volumen else gap_multiplier
                toques += peso
                if primero is None:
                    primero = v.ts
                ultimo = v.ts

    return toques, primero, ultimo


def _fusionar(niveles, tolerancia):
    """Fusiona niveles cercanos, mantiene el más reciente."""
    if not niveles:
        return []

    fusionados = []
    cluster = [niveles[0]]
    ancla = niveles[0]["precio"]

    def _cerrar_cluster():
        mejor = max(cluster, key=lambda d: d["ultimo"])
        fusionados.append(mejor)

    for niv in niveles[1:]:
        if abs(niv["precio"] - ancla) <= tolerancia:
            cluster.append(niv)
        else:
            _cerrar_cluster()
            cluster = [niv]
            ancla = niv["precio"]
    _cerrar_cluster()

    return fusionados


def _evaluar_estado(velas, nivel, tipo, tolerancia, ultimo_ts, confirmacion_velas=2, usar_open_ruptura=True):
    """Evalúa si un nivel está vivo, roto o en flip. Detecta rupturas precoces con open."""
    posteriores = [v for v in velas if v.ts > ultimo_ts]

    consecutivos = 0
    ts_rotura = None

    for i, v in enumerate(posteriores):
        cierre = v.c

        if tipo == "techo":
            # Rotura por debajo: close baja de nivel - tolerancia
            cruzo = cierre < nivel - tolerancia
        else:  # suelo
            # Rotura por encima: close sube por encima de nivel + tolerancia
            cruzo = cierre > nivel + tolerancia

        if cruzo:
            consecutivos += 1
            if consecutivos >= confirmacion_velas:
                ts_rotura = v.ts
                break
        else:
            consecutivos = 0

    if ts_rotura is None:
        return "vivo", None, None

    despues_rotura = [v for v in posteriores if v.ts > ts_rotura]
    retoques, _, ts_flip = _contar_toques(despues_rotura, nivel, tolerancia)

    if retoques > 0:
        return "flip", ts_rotura, ts_flip
    return "roto", ts_rotura, None


def detectar_niveles(velas, k, tolerancia_atr, toques_min, config):
    """Detecta niveles de soporte/resistencia."""
    cfg_params = config["parametros"]
    cfg_logica = config["logica"]
    cfg_atr = config["atr"]

    altos = [v.h for v in velas]
    bajos = [v.l for v in velas]
    cierres = [v.c for v in velas]

    atr_serie = indicadores.atr(altos, bajos, cierres, cfg_atr["periodo"])
    atr_ref = _mediana([a for a in atr_serie if a is not None])

    if atr_ref is None or atr_ref <= 0:
        raise ValueError(f"No hay suficiente historia para ATR (necesita {cfg_atr['periodo'] + 1} velas)")

    tolerancia = tolerancia_atr * atr_ref
    idx_altos, idx_bajos = indicadores.extremos_locales(velas, k)

    candidatos = []
    for idx in idx_altos:
        nivel = altos[idx]
        toques, primero, ultimo = _contar_toques(
            velas, nivel, tolerancia,
            ponderar_volumen=cfg_logica.get("ponderar_volumen", True),
            detectar_gaps=cfg_logica.get("detectar_gaps", True),
            gap_multiplier=cfg_params.get("gap_multiplier", 1.5))

        if toques >= toques_min:
            candidatos.append({
                "tipo": "techo", "precio": nivel, "toques": toques,
                "primero": primero, "ultimo": ultimo
            })

    for idx in idx_bajos:
        nivel = bajos[idx]
        toques, primero, ultimo = _contar_toques(
            velas, nivel, tolerancia,
            ponderar_volumen=cfg_logica.get("ponderar_volumen", True),
            detectar_gaps=cfg_logica.get("detectar_gaps", True),
            gap_multiplier=cfg_params.get("gap_multiplier", 1.5))

        if toques >= toques_min:
            candidatos.append({
                "tipo": "suelo", "precio": nivel, "toques": toques,
                "primero": primero, "ultimo": ultimo
            })

    candidatos.sort(key=lambda d: d["precio"])
    return _fusionar(candidatos, tolerancia), atr_ref, tolerancia


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _abrir_historial(coin, tf):
    ruta = _ruta_historial(coin, tf)
    nuevo = not os.path.exists(ruta)
    _carpeta_niveles(coin)
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
        "toques": round(niv["toques"], 2),
        "estado": niv["estado"],
        "primero": _fmt_fecha(niv["primero"]) if niv["primero"] else "",
        "ultimo": _fmt_fecha(niv["ultimo"]) if niv["ultimo"] else "",
        "pid": os.getpid(),
    })
    log.flush()


def _guardar_listado(coin, tf, params, atr_ref, tolerancia, ultimo_ts_procesado, niveles, usar_atr_guardado=False):
    ruta = _ruta_listado(coin, tf)
    _carpeta_niveles(coin)
    datos = {
        "params": params,
        "atr_ref": atr_ref,
        "tolerancia": tolerancia,
        "ultimo_ts_procesado": ultimo_ts_procesado,
        "usar_atr_guardado": usar_atr_guardado,
        "niveles": [dict(tipo=n["tipo"], precio=n["precio"], toques=round(n["toques"], 2),
                        primero=n["primero"], ultimo=n["ultimo"], estado=n["estado"],
                        dentro=n.get("dentro", False), consecutivos=n.get("consecutivos", 0))
                    for n in niveles]
    }
    tmp = ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump(datos, f, indent=2)
    os.replace(tmp, ruta)


def _cargar_listado(coin, tf):
    ruta = _ruta_listado(coin, tf)
    if not os.path.exists(ruta):
        return None
    with open(ruta) as f:
        return json.load(f)


def _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas, config, verbose=False):
    velas, ruta = _cargar_velas(coin, tf, desde_dias, verbose=verbose)
    niveles, atr_ref, tolerancia = detectar_niveles(velas, k, tolerancia_atr, toques_min, config)

    precio_actual = velas[-1].c if velas else None
    ts_final = velas[-1].ts if velas else None

    if precio_actual is None or precio_actual <= 0:
        raise ValueError("Precio actual inválido")

    for niv in niveles:
        estado, ts_rotura, ts_flip = _evaluar_estado(
            velas, niv["precio"], niv["tipo"], tolerancia, niv["ultimo"],
            confirmacion_velas, config["logica"].get("usar_open_ruptura", True))
        niv["estado"] = estado
        niv["dist_pct"] = (niv["precio"] - precio_actual) / precio_actual * 100
        niv["antig_dias"] = (ts_final - niv["ultimo"]) / 86400000 if ts_final and niv["ultimo"] else 0

    vigentes = [niv for niv in niveles if niv["estado"] in ("vivo", "flip")]
    techos = sorted((niv for niv in vigentes if niv["tipo"] == "techo"),
                    key=lambda d: abs(d["dist_pct"]))
    suelos = sorted((niv for niv in vigentes if niv["tipo"] == "suelo"),
                    key=lambda d: abs(d["dist_pct"]))

    return {
        "ruta": ruta, "velas": velas, "niveles": niveles, "atr_ref": atr_ref,
        "tolerancia": tolerancia, "precio_actual": precio_actual, "techos": techos, "suelos": suelos
    }


def _anotar_diferencias(niveles_viejos, niveles_nuevos, tolerancia, writer, log, coin, tf, config):
    usados = set()
    n_nuevos = n_toques = n_transiciones = n_desaparecidos = n_recuperados = 0
    cfg_historial = config.get("historial", {})

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

        if nuevo["estado"] != viejo["estado"]:
            if nuevo["estado"] in ("roto", "flip"):
                evento = "flip" if nuevo["estado"] == "flip" else "rotura"
                _anotar_historial(writer, log, coin, tf, evento, nuevo)
                n_transiciones += 1
            elif cfg_historial.get("registrar_transiciones_positivas", True):
                if viejo["estado"] in ("roto", "flip") and nuevo["estado"] == "vivo":
                    _anotar_historial(writer, log, coin, tf, "recuperado", nuevo)
                    n_recuperados += 1
        elif nuevo["estado"] == viejo["estado"] and nuevo["ultimo"] > viejo["ultimo"]:
            _anotar_historial(writer, log, coin, tf, "toque", nuevo)
            n_toques += 1

    n_desaparecidos = len(niveles_viejos) - len(usados)
    return n_nuevos, n_toques, n_transiciones, n_desaparecidos, n_recuperados


_verificado_esta_sesion = set()


def _actualizar_incremental(coin, tf, velas_nuevas, listado_anterior, confirmacion_velas, config):
    """Actualiza solo con velas nuevas. Usa ATR y tolerancia guardados (muy rápido: <1s)."""
    if not velas_nuevas:
        return listado_anterior

    precio_actual = velas_nuevas[-1].c if velas_nuevas else None
    ts_final = velas_nuevas[-1].ts if velas_nuevas else None

    if precio_actual is None or precio_actual <= 0:
        raise ValueError("Precio actual inválido")

    atr_ref = listado_anterior["atr_ref"]
    tolerancia = listado_anterior["tolerancia"]

    niveles_viejos = listado_anterior["niveles"]

    # Evalúa estado actual de cada nivel (compara contra velas nuevas)
    for niv in niveles_viejos:
        estado, _, _ = _evaluar_estado(
            velas_nuevas, niv["precio"], niv["tipo"], tolerancia, niv["ultimo"],
            confirmacion_velas, config["logica"].get("usar_open_ruptura", True))
        if estado != niv["estado"]:  # solo actualiza si cambió
            niv["estado"] = estado
        niv["dist_pct"] = (niv["precio"] - precio_actual) / precio_actual * 100
        niv["antig_dias"] = (ts_final - niv["ultimo"]) / 86400000 if ts_final and niv["ultimo"] else 0

    vigentes = [niv for niv in niveles_viejos if niv["estado"] in ("vivo", "flip")]
    techos = sorted((niv for niv in vigentes if niv["tipo"] == "techo"),
                    key=lambda d: abs(d["dist_pct"]))
    suelos = sorted((niv for niv in vigentes if niv["tipo"] == "suelo"),
                    key=lambda d: abs(d["dist_pct"]))

    return {
        "velas": velas_nuevas, "niveles": niveles_viejos, "atr_ref": atr_ref,
        "tolerancia": tolerancia, "precio_actual": precio_actual, "techos": techos, "suelos": suelos
    }


def _actualizar(coin, tf, k=None, tolerancia_atr=None, toques_min=None, confirmacion_velas=None):
    _verificar_directorio_niveles(coin)

    clave_sesion = (coin, tf)
    forzar_recalculo = clave_sesion not in _verificado_esta_sesion
    _verificado_esta_sesion.add(clave_sesion)

    config = cargar_config()
    params_cfg = config["parametros"]

    if k is None:
        k = params_cfg["k"]
    if tolerancia_atr is None:
        tolerancia_atr = params_cfg["tolerancia_atr"]
    if toques_min is None:
        toques_min = params_cfg["toques_min"]
    if confirmacion_velas is None:
        confirmacion_velas = params_cfg["confirmacion_velas"]

    params_nuevos = {
        "k": k, "tolerancia_atr": tolerancia_atr, "toques_min": toques_min,
        "confirmacion_velas": confirmacion_velas
    }

    listado_anterior = _cargar_listado(coin, tf)

    if listado_anterior is None:
        print(f"Sin listado previo para {coin.upper()} {tf} - barrido inicial.")
        r = _analizar(coin, tf, k, tolerancia_atr, toques_min, None, confirmacion_velas, config, verbose=True)
        velas = r["velas"]
        if not velas:
            print(f"  {coin.upper()} {tf}: sin velas disponibles.")
            return

        for niv in r["niveles"]:
            niv["dentro"] = False
            niv["consecutivos"] = 0

        writer, log = _abrir_historial(coin, tf)
        try:
            for niv in r["niveles"]:
                _anotar_historial(writer, log, coin, tf, "nivel_inicial", niv)
        finally:
            log.close()

        _guardar_listado(coin, tf, params_nuevos, r["atr_ref"], r["tolerancia"], velas[-1].ts, r["niveles"])
        print(f"  [OK] {coin.upper()} {tf}: barrido inicial, {len(r['niveles'])} niveles")
        return

    # Carga velas nuevas desde último procesado
    velas_nuevas, _ = _cargar_velas(coin, tf, desde_ts=listado_anterior["ultimo_ts_procesado"])

    if not velas_nuevas:
        print(f"  {coin.upper()} {tf} al día ({_fmt_fecha(listado_anterior['ultimo_ts_procesado'])})")
        return

    # Usa procesamiento incremental si parámetros no cambiaron
    if listado_anterior["params"] == params_nuevos and not forzar_recalculo:
        r = _actualizar_incremental(coin, tf, velas_nuevas, listado_anterior, confirmacion_velas, config)
        n_cambios = sum(1 for i, niv in enumerate(r["niveles"])
                       if i < len(listado_anterior["niveles"]) and niv["estado"] != listado_anterior["niveles"][i]["estado"])
        print(f"  [OK] {coin.upper()} {tf}: {len(r['niveles'])} niveles ({n_cambios} cambios de estado)")
    else:
        # Recalcula todo si parámetros cambiaron
        if listado_anterior["params"] != params_nuevos:
            print(f"Parametros distintos - se recalcula completo.")

        r = _analizar(coin, tf, k, tolerancia_atr, toques_min, None, confirmacion_velas, config)

        writer, log = _abrir_historial(coin, tf)
        try:
            n_nuevos, n_toques, n_transiciones, n_desaparecidos, n_recuperados = _anotar_diferencias(
                listado_anterior["niveles"], r["niveles"], r["tolerancia"], writer, log, coin, tf, config)
        finally:
            log.close()

        aviso = f", {n_desaparecidos} desaparecidos" if n_desaparecidos else ""
        aviso += f", {n_recuperados} recuperados" if n_recuperados else ""
        print(f"  [OK] {coin.upper()} {tf}: {len(r['niveles'])} niveles ({n_nuevos} nuevos, {n_toques} toques, "
              f"{n_transiciones} rupturas{aviso})")

    velas = r["velas"]
    usar_atr_guardado = listado_anterior is not None and listado_anterior["params"] == params_nuevos
    _guardar_listado(coin, tf, params_nuevos, r["atr_ref"], r["tolerancia"], velas[-1].ts, r["niveles"], usar_atr_guardado)


def _feed_niveles(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas, cada):
    print(f"Feed de niveles: {coin.upper()} {tf} (cada={cada:.0f}s). Ctrl+C para parar.\n")
    try:
        while True:
            try:
                _actualizar(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas)

                # Muestra estado actual para análisis en vivo
                listado = _cargar_listado(coin, tf)
                if listado:
                    precio = None
                    config = cargar_config()
                    try:
                        velas, _ = _cargar_velas(coin, tf, desde_dias=None)
                        if velas:
                            precio = velas[-1].c
                    except:
                        pass

                    if precio:
                        niveles = listado["niveles"]
                        vigentes = [n for n in niveles if n["estado"] in ("vivo", "flip")]

                        if vigentes:
                            # Techo más cercano
                            techos_vivos = [n for n in vigentes if n["tipo"] == "techo"]
                            suelos_vivos = [n for n in vigentes if n["tipo"] == "suelo"]

                            techo_cercano = min((n for n in techos_vivos if n["precio"] > precio),
                                              key=lambda x: x["precio"], default=None)
                            suelo_cercano = max((n for n in suelos_vivos if n["precio"] < precio),
                                              key=lambda x: x["precio"], default=None)

                            print(f"  Precio: {precio:.4f}")
                            if techo_cercano:
                                dist = ((techo_cercano["precio"] - precio) / precio) * 100
                                print(f"  Resistencia: {techo_cercano['precio']:.4f} (+{dist:.2f}%)")
                            if suelo_cercano:
                                dist = ((precio - suelo_cercano["precio"]) / precio) * 100
                                print(f"  Soporte: {suelo_cercano['precio']:.4f} (-{dist:.2f}%)")
                            print()

            except Exception as e:
                print(f"  (error) {coin} {tf}: {e}")
            time.sleep(cada)
    except KeyboardInterrupt:
        print("\nParado.")


def _avisos_zona_indecision(techos, suelos):
    avisos = []
    if techos and techos[0]["dist_pct"] < 0:
        avisos.append(f"Precio ARRIBA del techo {techos[0]['precio']:.4f} sin ruptura confirmada")
    if suelos and suelos[0]["dist_pct"] > 0:
        avisos.append(f"Precio DEBAJO del suelo {suelos[0]['precio']:.4f} sin ruptura confirmada")
    return avisos


def _imprimir_niveles(niv_lista, etiqueta):
    print(f"\n{etiqueta}:")
    for niv in niv_lista:
        estado_txt = f"flip" if niv["estado"] == "flip" else niv["estado"]
        print(f"  {niv['precio']:>12.4f}  dist {niv['dist_pct']:>7.2f}%  "
              f"toques {niv['toques']:>6.1f}  antig {niv['antig_dias']:>6.1f}d  {estado_txt}")


def main():
    args = sys.argv[1:]
    config = cargar_config()

    if args and args[0] == "--actualizar":
        args = args[1:]
        if len(args) < 2:
            print("Uso: python niveles.py --actualizar <coin> <tf> [--k ..] [--tolerancia-atr ..] "
                  "[--toques-min ..] [--confirmacion-velas ..] [--cada segundos]")
            return

        coin, tf = args[0], args[1]
        resto = args[2:]
        k = tolerancia_atr = toques_min = confirmacion_velas = cada = None

        i = 0
        while i < len(resto):
            if resto[i] == "--k":
                k = int(resto[i + 1]); i += 2
            elif resto[i] == "--tolerancia-atr":
                tolerancia_atr = float(resto[i + 1]); i += 2
            elif resto[i] == "--toques-min":
                toques_min = int(resto[i + 1]); i += 2
            elif resto[i] == "--confirmacion-velas":
                confirmacion_velas = int(resto[i + 1]); i += 2
            elif resto[i] == "--cada":
                cada = float(resto[i + 1]); i += 2
            else:
                i += 1

        if cada is None:
            _actualizar(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas)
        else:
            _feed_niveles(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas, cada)
        return

    if len(args) < 2:
        print("Uso: python niveles.py <coin> <tf> --k N --tolerancia-atr N --toques-min N "
              "[--desde-dias N] [--confirmacion-velas N] [--tf-macro TF]")
        return

    coin, tf = args[0], args[1]
    _verificar_directorio_niveles(coin)
    resto = args[2:]

    k = tolerancia_atr = toques_min = None
    desde_dias = None
    confirmacion_velas = 2
    tf_macro = None

    i = 0
    while i < len(resto):
        if resto[i] == "--k":
            k = int(resto[i + 1]); i += 2
        elif resto[i] == "--tolerancia-atr":
            tolerancia_atr = float(resto[i + 1]); i += 2
        elif resto[i] == "--toques-min":
            toques_min = int(resto[i + 1]); i += 2
        elif resto[i] == "--desde-dias":
            desde_dias = int(resto[i + 1]); i += 2
        elif resto[i] == "--confirmacion-velas":
            confirmacion_velas = int(resto[i + 1]); i += 2
        elif resto[i] == "--tf-macro":
            tf_macro = resto[i + 1]; i += 2
        else:
            i += 1

    if k is None or tolerancia_atr is None or toques_min is None:
        print("Faltan parámetros obligatorios: --k, --tolerancia-atr, --toques-min")
        return

    r = _analizar(coin, tf, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas, config)
    velas = r["velas"]

    params = {"k": k, "tolerancia_atr": tolerancia_atr, "toques_min": toques_min, "confirmacion_velas": confirmacion_velas}
    if velas:
        _guardar_listado(coin, tf, params, r["atr_ref"], r["tolerancia"], velas[-1].ts, r["niveles"])

    print(f"Histórico: {r['ruta']} ({len(velas)} velas"
          + (f", últimos {desde_dias:.0f} días" if desde_dias else ", todo") + ")")
    if velas:
        print(f"Rango: {_fmt_fecha(velas[0].ts)} -> {_fmt_fecha(velas[-1].ts)}")

    print(f"\nk={k}  tolerancia_atr={tolerancia_atr}  toques_min={toques_min}  confirmacion_velas={confirmacion_velas}")
    print(f"ATR ref={r['atr_ref']:.4f}  tolerancia precio={r['tolerancia']:.4f}")
    print(f"Precio actual: {r['precio_actual']:.4f}")
    print(f"\n{len(r['niveles'])} niveles:\n")

    for niv in sorted(r["niveles"], key=lambda d: -d["precio"]):
        print(f"{niv['tipo']:<7} {niv['precio']:>12.4f} toques {niv['toques']:>6.1f}  "
              f"{niv['estado']:<8} dist {niv['dist_pct']:>7.2f}%")

    print(f"\n=== Vigentes (vivos/flip) ===")
    _imprimir_niveles(r["techos"], "Techos")
    _imprimir_niveles(r["suelos"], "Suelos")

    for aviso in _avisos_zona_indecision(r["techos"], r["suelos"]):
        print(f"\nAVISO: {aviso}")

    if tf_macro is None:
        return

    rm = _analizar(coin, tf_macro, k, tolerancia_atr, toques_min, desde_dias, confirmacion_velas, config)
    techo_macro = min((n for n in rm["techos"] if n["dist_pct"] > 0),
                      key=lambda d: d["dist_pct"], default=None)
    suelo_macro = max((n for n in rm["suelos"] if n["dist_pct"] < 0),
                      key=lambda d: d["dist_pct"], default=None)

    print(f"\n=== Rango macro ({tf_macro}) ===")
    if techo_macro:
        print(f"Tope superior: {techo_macro['precio']:.4f} (dist {techo_macro['dist_pct']:.2f}%)")
    else:
        print(f"Tope superior: ninguno por encima")

    if suelo_macro:
        print(f"Tope inferior: {suelo_macro['precio']:.4f} (dist {suelo_macro['dist_pct']:.2f}%)")
    else:
        print(f"Tope inferior: ninguno por debajo")

    lo = suelo_macro["precio"] if suelo_macro else float("-inf")
    hi = techo_macro["precio"] if techo_macro else float("inf")
    techos_ajustados = [n for n in r["techos"] if lo <= n["precio"] <= hi]
    suelos_ajustados = [n for n in r["suelos"] if lo <= n["precio"] <= hi]

    print(f"\n=== Niveles de {tf} dentro del rango macro ===")
    _imprimir_niveles(techos_ajustados, "Techos")
    _imprimir_niveles(suelos_ajustados, "Suelos")

    print(f"\n✓ Guardado en: {_ruta_listado(coin, tf)}")


if __name__ == "__main__":
    main()
