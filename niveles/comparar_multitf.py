#!/usr/bin/env python3
"""
Procesa múltiples combinaciones de TF para detectar niveles y comparar coincidencias.

Uso:
  python comparar_multitf.py eth

Genera:
  - json/niveles_<coin>_<tf_replay>_<tf_estructura>.json (cada combo)
  - json/coincidencias_<coin>.json (análisis de superposición)
"""

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DIR_NIVELES = Path(__file__).resolve().parent
DIR_JSON = DIR_NIVELES / "json"
DIR_HISTORICOS = DIR_NIVELES.parent / "historicos"
DIR_NEO = DIR_NIVELES.parent

sys.path.insert(0, str(DIR_NEO))
from mercado import indicadores


def _tf_a_ms(tf):
    """Convierte timeframe a milisegundos."""
    if tf.endswith('m'):
        return int(tf[:-1]) * 60 * 1000
    elif tf.endswith('h'):
        return int(tf[:-1]) * 3600 * 1000
    elif tf.endswith('d'):
        return int(tf[:-1]) * 86400 * 1000
    raise ValueError(f"Timeframe inválido: {tf}")


def _ms_a_tf(ms):
    """Convierte milisegundos a timeframe."""
    if ms % (3600 * 1000) == 0:
        h = ms // (3600 * 1000)
        return f"{h}h"
    elif ms % (60 * 1000) == 0:
        m = ms // (60 * 1000)
        return f"{m}m"
    elif ms % (86400 * 1000) == 0:
        d = ms // (86400 * 1000)
        return f"{d}d"
    return str(ms)


def _cargar_csv_historico(coin, tf, desde_ms=None):
    """Carga CSV de Binance desde historicos/."""
    # Busca archivo con patrón: *_<COIN>_<TF>_binance.csv
    patron = f"*_{coin.upper()}_{tf}_binance.csv"
    matches = list(DIR_HISTORICOS.glob(patron))

    if not matches:
        raise FileNotFoundError(f"No hay histórico de {coin} {tf} en {DIR_HISTORICOS}")

    ruta = matches[0]
    velas = []

    with open(ruta, newline='') as f:
        r = csv.reader(f)
        next(r)  # Skip header
        for row in r:
            ts = int(row[0])
            if desde_ms and ts < desde_ms:
                continue
            velas.append([
                ts,
                float(row[2]),  # open
                float(row[3]),  # high
                float(row[4]),  # low
                float(row[5]),  # close
                float(row[6])   # volumen
            ])

    return velas, ruta


def _agregar_velas(velas_pequeñas, tf_pequeño_ms, tf_grande_ms):
    """Agrupa velas menores a mayores."""
    if not velas_pequeñas:
        return []

    velas_grandes = []
    vela_actual = None

    for v in velas_pequeñas:
        ts, open_, high, low, close, volumen = v
        ts_rounded = (ts // tf_grande_ms) * tf_grande_ms

        if vela_actual is None or vela_actual["ts"] != ts_rounded:
            if vela_actual is not None:
                velas_grandes.append([
                    vela_actual["ts"],
                    vela_actual["open"],
                    vela_actual["high"],
                    vela_actual["low"],
                    vela_actual["close"],
                    vela_actual["volumen"]
                ])
            vela_actual = {
                "ts": ts_rounded,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volumen": volumen
            }
        else:
            vela_actual["high"] = max(vela_actual["high"], high)
            vela_actual["low"] = min(vela_actual["low"], low)
            vela_actual["close"] = close
            vela_actual["volumen"] += volumen

    if vela_actual is not None:
        velas_grandes.append([
            vela_actual["ts"],
            vela_actual["open"],
            vela_actual["high"],
            vela_actual["low"],
            vela_actual["close"],
            vela_actual["volumen"]
        ])

    return velas_grandes


def _mediana(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _contar_toques(velas, nivel, tolerancia):
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


def detectar_niveles(velas, k, tolerancia_atr, toques_min, periodo_atr=14, verbose=False):
    """Detecta soportes y resistencias."""
    ts_inicio = time.time()

    altos = [v[2] for v in velas]
    bajos = [v[3] for v in velas]
    cierres = [v[4] for v in velas]

    if verbose:
        print(f"    [1/5] Datos: {len(velas)} velas", flush=True)

    ts = time.time()
    atr_serie = indicadores.atr(altos, bajos, cierres, periodo_atr)
    if verbose:
        print(f"    [2/5] ATR: {time.time()-ts:.1f}s", flush=True)

    atr_ref = _mediana([a for a in atr_serie if a is not None])
    if atr_ref is None or atr_ref <= 0:
        raise ValueError(f"ATR insuficiente ({periodo_atr + 1} velas mín)")
    tolerancia = tolerancia_atr * atr_ref

    ts = time.time()
    idx_altos, idx_bajos = indicadores.extremos_locales(velas, k)
    if verbose:
        print(f"    [3/5] Extremos: {len(idx_altos)}↑ {len(idx_bajos)}↓ ({time.time()-ts:.1f}s)", flush=True)

    ts = time.time()
    candidatos = []
    for idx in idx_altos:
        nivel = altos[idx]
        toques, primero, ultimo = _contar_toques(velas, nivel, tolerancia)
        if toques >= toques_min:
            candidatos.append(dict(tipo="techo", precio=nivel, toques=toques, primero=primero, ultimo=ultimo))
    for idx in idx_bajos:
        nivel = bajos[idx]
        toques, primero, ultimo = _contar_toques(velas, nivel, tolerancia)
        if toques >= toques_min:
            candidatos.append(dict(tipo="suelo", precio=nivel, toques=toques, primero=primero, ultimo=ultimo))
    if verbose:
        print(f"    [4/5] Toques: {len(candidatos)} candidatos ({time.time()-ts:.1f}s)", flush=True)

    ts = time.time()
    candidatos.sort(key=lambda d: d["precio"])
    fusionados = _fusionar(candidatos, tolerancia)
    if verbose:
        print(f"    [5/5] Fusión: {len(fusionados)} niveles ({time.time()-ts:.1f}s)", flush=True)

    if verbose:
        print(f"    DONE {time.time()-ts_inicio:.1f}s total", flush=True)

    return fusionados, atr_ref, tolerancia


def procesar_combo(coin, tf_replay, tf_estructura, params, desde_ms=None):
    """Procesa una combinacion TF_replay -> TF_estructura."""
    print(f"  {tf_replay} -> {tf_estructura}...", end=" ", flush=True)

    try:
        # Cargar velas de replay
        velas_replay, ruta_replay = _cargar_csv_historico(coin, tf_replay, desde_ms)

        # Agrupar a estructura
        tf_replay_ms = _tf_a_ms(tf_replay)
        tf_estructura_ms = _tf_a_ms(tf_estructura)
        velas_estructura = _agregar_velas(velas_replay, tf_replay_ms, tf_estructura_ms)

        if len(velas_estructura) < 20:
            print(f"SKIP Datos insuficientes ({len(velas_estructura)} velas)")
            return None

        # Detectar niveles
        niveles, atr_ref, tolerancia = detectar_niveles(
            velas_estructura,
            k=params["k"],
            tolerancia_atr=params["tolerancia_pct"] / 100,  # Convertir % a decimal
            toques_min=params["toques_min"],
            periodo_atr=params.get("periodo_atr", 14)
        )

        resultado = {
            "combo": f"{tf_replay}->{tf_estructura}",
            "tf_replay": tf_replay,
            "tf_estructura": tf_estructura,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "velas_cargadas": len(velas_replay),
            "velas_agrupadas": len(velas_estructura),
            "niveles_detectados": len(niveles),
            "atr_ref": atr_ref,
            "tolerancia_abs": tolerancia,
            "niveles": [
                {
                    "tipo": n["tipo"],
                    "precio": round(n["precio"], 4),
                    "toques": n["toques"],
                    "primero": n.get("primero"),
                    "ultimo": n.get("ultimo")
                }
                for n in niveles
            ]
        }

        print(f"OK {len(niveles)} niveles")
        return resultado

    except Exception as e:
        print(f"ERROR: {e}")
        return None


def analizar_coincidencias(resultados):
    """Analiza qué niveles coinciden entre combos."""
    if not resultados:
        return {}

    # Agrupar niveles por precio (con tolerancia)
    tolerancia_precio = 0.0001  # 0.01% de tolerancia para agrupar

    clusters = {}  # precio → [combos que detectaron este nivel]

    for result in resultados:
        if result is None:
            continue
        combo = result["combo"]
        for niv in result["niveles"]:
            precio = niv["precio"]

            # Buscar cluster cercano
            cluster_encontrado = False
            for precio_cluster in list(clusters.keys()):
                if abs(precio - precio_cluster) / precio_cluster < tolerancia_precio:
                    clusters[precio_cluster].append({
                        "combo": combo,
                        "tipo": niv["tipo"],
                        "toques": niv["toques"]
                    })
                    cluster_encontrado = True
                    break

            # Nuevo cluster si no encontró uno
            if not cluster_encontrado:
                clusters[precio] = [{
                    "combo": combo,
                    "tipo": niv["tipo"],
                    "toques": niv["toques"]
                }]

    # Filtrar solo coincidencias (aparecen en 2+ combos)
    coincidencias = {
        precio: datos
        for precio, datos in clusters.items()
        if len(datos) >= 2
    }

    return {
        "total_niveles_por_combo": sum(r["niveles_detectados"] for r in resultados if r),
        "niveles_unicos": len(clusters),
        "coincidencias": len(coincidencias),
        "detalle": {
            f"{round(precio, 4)}": {
                "apariciones": len(datos),
                "combos": datos
            }
            for precio, datos in sorted(coincidencias.items())
        }
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    coin = sys.argv[1].lower()

    DIR_JSON.mkdir(parents=True, exist_ok=True)

    # Cargar parámetros
    params_file = DIR_NIVELES / "parametros.json"
    with open(params_file) as f:
        params_global = json.load(f)
    params = params_global["niveles"]

    # Combinaciones a procesar (Opción A)
    combos = [
        ("3m", "5m"),
        ("3m", "15m"),
        ("3m", "1h"),
        ("5m", "15m"),
        ("5m", "1h"),
        ("5m", "4h"),
        ("15m", "1h"),
        ("15m", "4h"),
        ("15m", "1d"),
        ("1h", "4h"),
        ("1h", "8h"),
        ("1h", "1d"),
        ("4h", "8h"),
        ("4h", "1d"),
        ("8h", "1d"),
    ]

    # Mediados de 2025 como corte (1 ano de datos con ciclos alcista/bajista)
    desde_ms = int(datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

    print(f"\n[Procesando {coin.upper()}]")
    print(f"Rango: desde 2025-06-01 (mediados 2025)")
    print(f"Parámetros: k={params['k']}, toques_min={params['toques_min']}, tolerancia={params['tolerancia_pct']}%\n")

    resultados = []
    for tf_replay, tf_estructura in combos:
        result = procesar_combo(coin, tf_replay, tf_estructura, params, desde_ms)
        if result:
            resultados.append(result)

            # Guardar resultado individual
            output_file = DIR_JSON / f"niveles_{coin}_{tf_replay}_{tf_estructura}.json"
            with open(output_file, 'w') as f:
                json.dump(result, f, indent=2)

    print()

    # Analizar coincidencias
    análisis = analizar_coincidencias(resultados)

    # Guardar análisis
    análisis_file = DIR_JSON / f"coincidencias_{coin}.json"
    with open(análisis_file, 'w') as f:
        json.dump(análisis, f, indent=2)

    # Resumen
    print(f"RESUMEN")
    print(f"  Combos procesadas: {len(resultados)}")
    print(f"  Niveles únicos detectados: {análisis['niveles_unicos']}")
    print(f"  Coincidencias (2+ combos): {análisis['coincidencias']}")
    print(f"\n  Guardado:")
    print(f"    - {len(resultados)} JSONs individuales en {DIR_JSON}/")
    print(f"    - Análisis: {análisis_file.name}\n")


if __name__ == "__main__":
    main()
