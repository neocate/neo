# Detecta soportes y resistencias con backtest + live multi-timeframe.
#
# BACKTEST (histórico):
#   python niveles.py eth 5m --cada1m --loop 300
#   Alimenta velas 1m históricas → agrupa a 5m → calcula niveles
#   Output: snapshots_eth_5m_cada1m.csv (vela-por-vela)
#
# LIVE (tiempo real):
#   Automático. Cuando se agota histórico, espera nuevas velas.
# ---------------------------------------------------------------

import csv
import os
import sys
import json
import time
import tempfile
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Este fichero vive en neo/niveles/. Las carpetas se resuelven por __file__,
# asi que mover o copiar el arbol no rompe nada (igual que velas/velas_bit.py).
DIR_NIVELES = Path(__file__).resolve().parent
DIR_LOGS = DIR_NIVELES / "logs"
DIR_JSON = DIR_NIVELES / "json"
DIR_NEO = DIR_NIVELES.parent

sys.path.insert(0, str(DIR_NEO))
from mercado import indicadores
from velas.velas_bit import _archivo as _archivo_bitget


def _crear_directorios():
    """logs/ y json/ se crean solas en cualquier equipo."""
    for d in (DIR_LOGS, DIR_JSON):
        d.mkdir(parents=True, exist_ok=True)

_debe_terminar = False

def _handler_signals(signum, frame):
    global _debe_terminar
    _debe_terminar = True
    print(f"\n[SIGNAL] Terminando gracefully...", flush=True)

signal.signal(signal.SIGTERM, _handler_signals)
signal.signal(signal.SIGINT, _handler_signals)


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


def _cargar_velas(coin, tf, desde_dias=None):
    ruta = _archivo_bitget(coin, tf)
    if not os.path.exists(ruta):
        raise FileNotFoundError(
            f"No hay historico de {coin.upper()} {tf} - "
            f"bajalo primero con: python herramientas/descargar_bit.py {coin} {tf}")

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


def _agregar_velas(velas_pequeñas, tf_pequeño_ms, tf_grande_ms):
    """Agrupa velas menores a mayores. Ej: 1m → 5m."""
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


def _evaluar_estado(velas, nivel, tipo, tolerancia, ultimo_ts, confirmacion_velas=2):
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


def detectar_niveles(velas, k, tolerancia_atr, toques_min, periodo_atr=14, verbose=False):
    ts_inicio = time.time()

    altos = [v[2] for v in velas]
    bajos = [v[3] for v in velas]
    cierres = [v[4] for v in velas]

    if verbose:
        print(f"  [1/5] Datos: {len(velas)} velas", flush=True)

    ts = time.time()
    atr_serie = indicadores.atr(altos, bajos, cierres, periodo_atr)
    if verbose:
        print(f"  [2/5] ATR: {time.time()-ts:.1f}s", flush=True)

    atr_ref = _mediana([a for a in atr_serie if a is not None])
    if atr_ref is None or atr_ref <= 0:
        raise ValueError(f"ATR insuficiente ({periodo_atr + 1} velas mín)")
    tolerancia = tolerancia_atr * atr_ref

    ts = time.time()
    idx_altos, idx_bajos = indicadores.extremos_locales(velas, k)
    if verbose:
        print(f"  [3/5] Extremos: {len(idx_altos)}↑ {len(idx_bajos)}↓ ({time.time()-ts:.1f}s)", flush=True)

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
        print(f"  [4/5] Toques: {len(candidatos)} candidatos ({time.time()-ts:.1f}s)", flush=True)

    ts = time.time()
    candidatos.sort(key=lambda d: d["precio"])
    fusionados = _fusionar(candidatos, tolerancia)
    if verbose:
        print(f"  [5/5] Fusión: {len(fusionados)} niveles ({time.time()-ts:.1f}s)", flush=True)

    if verbose:
        print(f"  DONE {time.time()-ts_inicio:.1f}s", flush=True)

    return fusionados, atr_ref, tolerancia


def _evaluar_niveles(velas, niveles, confirmacion_velas, tolerancia=0):
    precio_actual = velas[-1][4] if velas else None
    ts_final = velas[-1][0] if velas else None

    for niv in niveles:
        estado, ts_rotura, ts_flip = _evaluar_estado(
            velas, niv["precio"], niv["tipo"], tolerancia, niv["ultimo"], confirmacion_velas)
        niv["estado"] = estado
        niv["dist_pct"] = (niv["precio"] - precio_actual) / precio_actual * 100 if precio_actual and precio_actual > 0 else 0
        niv["antig_dias"] = (ts_final - niv["ultimo"]) / 86400000 if ts_final and ts_final > niv["ultimo"] else 0

    return precio_actual, ts_final


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


class LockFile:
    def __init__(self, path):
        self.path = path

    def adquirir(self, timeout=5):
        inicio = time.time()
        while time.time() - inicio < timeout:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.5)
        return False

    def liberar(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def _guardar_atomico(ruta, datos):
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(ruta) or ".")
    try:
        os.write(fd, json.dumps(datos, indent=2).encode())
        os.close(fd)
        os.replace(temp_path, ruta)
    except:
        try:
            os.unlink(temp_path)
        except:
            pass
        raise


def _cargar_json(ruta, default=None):
    if not os.path.exists(ruta):
        return default
    try:
        with open(ruta) as f:
            return json.load(f)
    except:
        return default


def _hash_dict(d):
    return hash(json.dumps(d, sort_keys=True))


def _guardar_snapshot_csv(ruta, timestamp, precio, tf_objetivo, niveles, modo, iteracion):
    """Guarda o append snapshot CSV."""
    agregar_cabecera = not os.path.exists(ruta)

    with open(ruta, 'a', newline='') as f:
        writer = csv.writer(f)

        if agregar_cabecera:
            cabecera = ['timestamp', 'precio', 'tf_objetivo', 'num_niveles']
            for i in range(len(niveles)):
                cabecera.extend([f'niv{i}_tipo', f'niv{i}_precio', f'niv{i}_toques', f'niv{i}_estado', f'niv{i}_dist_pct'])
            cabecera.extend(['modo', 'iteracion'])
            writer.writerow(cabecera)

        fila = [_fmt_fecha(timestamp), f"{precio:.4f}", tf_objetivo, len(niveles)]
        for niv in niveles:
            fila.extend([niv.get('tipo', ''), f"{niv.get('precio', 0):.4f}", niv.get('toques', 0), niv.get('estado', ''), f"{niv.get('dist_pct', 0):.2f}"])
        fila.extend([modo, iteracion])
        writer.writerow(fila)


def loop_principal(coin, tf_objetivo, tf_alimentacion, intervalo_seg, desde_dias, confirmacion_velas):
    _crear_directorios()
    directorio_base = DIR_JSON

    lock = LockFile(str(directorio_base / f".niveles_{coin}_{tf_objetivo}.lock"))
    if not lock.adquirir():
        print(f"ERROR: Ya hay instancia para {coin} {tf_objetivo}")
        return

    try:
        tf_alimentacion_ms = _tf_a_ms(tf_alimentacion)
        tf_objetivo_ms = _tf_a_ms(tf_objetivo)
        
        if tf_alimentacion_ms >= tf_objetivo_ms:
            print(f"ERROR: --cada{tf_alimentacion} debe ser MENOR que {tf_objetivo}")
            print(f"Ej: python niveles_soporte.py eth 5m --cada1m --loop 300")
            return

        state_file = str(directorio_base / f"state_{coin}_{tf_objetivo}.json")
        params_file = str(directorio_base / f"params_{coin}_{tf_objetivo}.json")
        csv_file = str(directorio_base / f"snapshots_{coin}_{tf_objetivo}_cada{tf_alimentacion}.csv")
        nivel_file_tpl = str(directorio_base / f"nivel_{coin.upper()}_k{{}}_toques{{}}.json")

        print(f"[{_fmt_fecha(int(datetime.now(timezone.utc).timestamp() * 1000))}] Iniciando LOOP")
        print(f"  Objetivo: {tf_objetivo}, Alimentación: {tf_alimentacion}, Intervalo: {intervalo_seg}s")
        print(f"  CSV: {csv_file}\n")

        velas_alimentacion, ruta_alimentacion = _cargar_velas(coin, tf_alimentacion, desde_dias)
        ts_hoy = int(datetime.now(timezone.utc).timestamp() * 1000)

        state = _cargar_json(state_file, {
            "modo": "backtest",
            "última_vela_procesada_idx": 0,
            "params_hash": None,
            "precio_actual": None,
            "num_niveles": 0
        })

        iteracion = 0

        while not _debe_terminar:
            iteracion += 1
            ts_ahora = int(datetime.now(timezone.utc).timestamp() * 1000)

            try:
                # Cargar parámetros
                params = _cargar_json(params_file, {})
                params_hash = _hash_dict(params)

                if not params:
                    print(f"[{_fmt_fecha(ts_ahora)}] ⏳ Esperando {params_file}")
                    time.sleep(intervalo_seg)
                    continue

                # Detectar cambio de parámetros
                if params_hash != state.get("params_hash"):
                    if state.get("params_hash") is not None:
                        print(f"\n[{_fmt_fecha(ts_ahora)}] ⚠️  PARÁMETROS CAMBIARON → recalculando...\n")
                    state["params_hash"] = params_hash

                k = params.get("k")
                tolerancia_atr = params.get("tolerancia_atr")
                toques_min = params.get("toques_min")

                if k is None or tolerancia_atr is None or toques_min is None:
                    print(f"[{_fmt_fecha(ts_ahora)}] ERROR Parámetros incompletos")
                    time.sleep(intervalo_seg)
                    continue

                if not (1 <= k <= 20 and 0 < tolerancia_atr <= 5 and 1 <= toques_min <= 10):
                    print(f"[{_fmt_fecha(ts_ahora)}] ERROR Parámetros fuera de rango: k={k} (1-20), tolerancia_atr={tolerancia_atr} (0-5), toques_min={toques_min} (1-10)")
                    time.sleep(intervalo_seg)
                    continue

                # Procesar velas
                idx_desde = state.get("última_vela_procesada_idx", 0)

                if state["modo"] == "backtest":
                    if idx_desde < len(velas_alimentacion):
                        velas_slice = velas_alimentacion[idx_desde:idx_desde + 300]
                        if not velas_slice:
                            velas_slice = velas_alimentacion[idx_desde:]

                        velas_objetivo = _agregar_velas(velas_slice, tf_alimentacion_ms, tf_objetivo_ms)

                        if len(velas_objetivo) >= 50:
                            ts_inicio = time.time()
                            niveles, atr_ref, tolerancia = detectar_niveles(
                                velas_objetivo, k, tolerancia_atr, toques_min)

                            _evaluar_niveles(velas_objetivo, niveles, confirmacion_velas, tolerancia)

                            precio_actual = velas_slice[-1][4] if velas_slice else None

                            # Guardar snapshot
                            ts_vela = velas_slice[-1][0] if velas_slice else ts_ahora
                            _guardar_snapshot_csv(csv_file, ts_vela, precio_actual, tf_objetivo, niveles, "backtest", iteracion)

                            # Guardar niveles
                            nivel_file = nivel_file_tpl.format(k, toques_min)
                            datos_nivel = {
                                "timestamp": _fmt_fecha(ts_ahora),
                                "params": params,
                                "niveles": niveles,
                                "precio_actual": precio_actual,
                                "num_niveles": len(niveles),
                                "modo": "backtest"
                            }
                            _guardar_atomico(nivel_file, datos_nivel)

                            state["última_vela_procesada_idx"] = idx_desde + len(velas_slice)
                            state["precio_actual"] = precio_actual
                            state["num_niveles"] = len(niveles)

                            if iteracion % 10 == 0:
                                print(f"[{_fmt_fecha(ts_ahora)}] OK Iter {iteracion}: {len(niveles)} niveles, precio={precio_actual:.4f}", flush=True)

                            # Detectar si llegó a HOY
                            if velas_slice[-1][0] >= ts_hoy - 300000:  # Últimas 5 min
                                print(f"\n[{_fmt_fecha(ts_ahora)}] TRANSITION BACKTEST -> LIVE\n")
                                state["modo"] = "live"

                        _guardar_atomico(state_file, state)
                    else:
                        state["modo"] = "live"
                        _guardar_atomico(state_file, state)

                elif state["modo"] == "live":
                    # En live, esperaría nuevas velas desde descargar_bit.py
                    # Por ahora, simula que llegó a fin
                    print(f"[{_fmt_fecha(ts_ahora)}] LIVE (esperando nuevas velas)")
                    break

                time.sleep(intervalo_seg)

            except Exception as e:
                print(f"[{_fmt_fecha(ts_ahora)}] ERROR {e}", flush=True)
                import traceback
                traceback.print_exc()
                time.sleep(intervalo_seg)

    finally:
        lock.liberar()
        print(f"[{_fmt_fecha(int(datetime.now(timezone.utc).timestamp() * 1000))}] Finalizado")


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return

    coin, tf_objetivo = args[0], args[1]

    tf_alimentacion = None
    intervalo_seg = None
    desde_dias = None
    confirmacion_velas = 2

    i = 2
    while i < len(args):
        if args[i].startswith("--cada"):
            tf_alimentacion = args[i][6:]
        elif args[i] == "--loop":
            i += 1
            intervalo_seg = int(args[i])
        elif args[i] == "--desde-dias":
            i += 1
            desde_dias = float(args[i])
        elif args[i] == "--confirmacion-velas":
            i += 1
            confirmacion_velas = int(args[i])
        i += 1

    if intervalo_seg is None or tf_alimentacion is None:
        print("Requiere: --cada<TF> --loop <seg>")
        return

    loop_principal(coin, tf_objetivo, tf_alimentacion, intervalo_seg, desde_dias, confirmacion_velas)


if __name__ == "__main__":
    main()
