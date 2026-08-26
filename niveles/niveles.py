import csv
import os
import sys
import json
import time
import tempfile
import signal
import socket
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIR_NIVELES = Path(__file__).resolve().parent
DIR_LOGS = DIR_NIVELES / "logs"
DIR_JSON = DIR_NIVELES / "json"
DIR_NEO = DIR_NIVELES.parent
DIR_VELAS = DIR_NEO / "velas"

sys.path.insert(0, str(DIR_NEO))
from indicadores import indicadores

TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']
MERCADO_POR_DEFECTO = "futuros"

DEFAULTS = {
    "confirmacion_velas": 2,
    "max_dist_pct": 10.0,
    "max_antig_dias": 180.0,
    "separacion_min_atr": None,
    "periodo_atr": 14,
}

PRIORIDAD_ESTADO = {"vivo": 3, "flip": 2, "roto": 1}

VELAS_AVISO = 20000
VELAS_OBJETIVO = 10000

_USO = """Detecta soportes y resistencias sobre las velas de una moneda.

    python niveles.py eth --loop 60                  todos los TF disponibles
    python niveles.py eth --loop 60 --mercado spot
    python niveles.py eth 1h --una-vez               un TF suelto, para probar

Parametros en params_<coin>_<tf>.json:

    k                   velas vecinas que debe dominar un pivote (1-20)
    tolerancia_atr      ancho de la banda de toque, en ATR (0-5)
    toques_min          toques minimos para aceptar un nivel (1-10)
    confirmacion_velas  cierres al otro lado que confirman una rotura (1-10)
    desde_dias          ventana de velas a cargar; null = todo el historico
    max_dist_pct        descarta niveles a mas de X% del precio; null = sin filtro
    max_antig_dias      descarta niveles cuyo ultimo toque sea mas viejo; null = sin filtro
    separacion_min_atr  separacion minima garantizada entre niveles, en ATR
    periodo_atr         periodo del ATR de Wilder (subirlo suaviza la banda)

Entrada:  velas/<COIN>/bitget_<COIN>_<tf>_<mercado>.csv   (solo velas CERRADAS)
Salida:   niveles/json/nivel_<COIN>_<tf>_<mercado>_k<N>_toques<M>.json

Ver niveles/README.md para la explicacion completa.
"""


def _desde_dias_default(tf):
    return VELAS_OBJETIVO * _tf_a_ms(tf) / 86400000.0


def _crear_directorios():
    for d in (DIR_LOGS, DIR_JSON):
        d.mkdir(parents=True, exist_ok=True)


_debe_terminar = False


def _handler_signals(signum, frame):
    global _debe_terminar
    _debe_terminar = True
    print("\n[SIGNAL] Terminando gracefully...", flush=True)


def _instalar_handlers_signals():
    signal.signal(signal.SIGTERM, _handler_signals)
    signal.signal(signal.SIGINT, _handler_signals)


def _tf_a_ms(tf):
    if tf.endswith('m'):
        return int(tf[:-1]) * 60 * 1000
    elif tf.endswith('h'):
        return int(tf[:-1]) * 3600 * 1000
    elif tf.endswith('d'):
        return int(tf[:-1]) * 86400 * 1000
    raise ValueError(f"Timeframe invalido: {tf}")


def _ruta_csv(coin, tf, mercado):
    return DIR_VELAS / f"{coin.upper()}" / f"bitget_{coin.upper()}_{tf}_{mercado}.csv"


def _fila_vela(row):
    return [int(row[0]), float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])]


def _parsear(lineas):
    velas = []
    for row in csv.reader(lineas):
        if not row or not row[0].strip().isdigit():
            continue
        try:
            velas.append(_fila_vela(row))
        except (ValueError, IndexError):
            continue
    return velas


def _leer_cola(ruta, corte_ms, tf):
    tam = os.path.getsize(ruta)

    if corte_ms is None:
        with open(ruta, newline='') as f:
            return _parsear(f)

    ahora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    faltan = max(1, (ahora_ms - corte_ms) // _tf_a_ms(tf))
    leer = min(tam, int(faltan * 70 * 2) + 65536)

    while True:
        with open(ruta, 'rb') as f:
            f.seek(tam - leer)
            crudo = f.read(leer)
        lineas = crudo.decode('utf-8', 'replace').splitlines()[1:]
        velas = _parsear(lineas)

        if leer >= tam or (velas and velas[0][0] <= corte_ms):
            return [v for v in velas if v[0] >= corte_ms]
        leer = min(tam, leer * 4)


def _cargar_velas(coin, tf, mercado, desde_dias=None):
    ruta = _ruta_csv(coin, tf, mercado)
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"No hay velas de {coin.upper()} {tf} {mercado}: {ruta}")

    corte_ms = None
    if desde_dias is not None:
        corte_ms = int((datetime.now(timezone.utc) - timedelta(days=desde_dias)).timestamp() * 1000)

    return _leer_cola(ruta, corte_ms, tf), ruta


class CacheVelas:
    def __init__(self, coin, tf, mercado):
        self.coin = coin
        self.tf = tf
        self.mercado = mercado
        self.velas = []
        self.ruta = _ruta_csv(coin, tf, mercado)
        self._firma = None

    def cambio(self, desde_dias):
        try:
            st = os.stat(self.ruta)
        except OSError:
            return True
        return (st.st_mtime_ns, st.st_size, desde_dias) != self._firma

    def obtener(self, desde_dias):
        try:
            st = os.stat(self.ruta)
            firma = (st.st_mtime_ns, st.st_size, desde_dias)
        except OSError:
            firma = None

        if firma is not None and firma == self._firma:
            return self.velas, False

        self.velas, self.ruta = _cargar_velas(self.coin, self.tf, self.mercado, desde_dias)
        self._firma = firma
        return self.velas, True


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


def _fmt_fecha(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


class LockFile:
    def __init__(self, path, intervalo=60):
        self.path = path
        self.intervalo = intervalo

    def _contenido(self):
        try:
            with open(self.path) as f:
                partes = f.read().strip().split("|")
            return int(partes[0]), (partes[1] if len(partes) > 1 else "")
        except (OSError, ValueError, IndexError):
            return None, ""

    def _caducado(self):
        margen = max(300.0, self.intervalo * 3)
        try:
            return (time.time() - os.stat(self.path).st_mtime) > margen
        except OSError:
            return True

    def _vivo(self):
        pid, host = self._contenido()
        if pid is not None and host == socket.gethostname():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except OSError:
                pass
        return not self._caducado()

    def latir(self):
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def adquirir(self, timeout=5):
        inicio = time.time()
        while time.time() - inicio < timeout:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(f"{os.getpid()}|{socket.gethostname()}")
                return True
            except FileExistsError:
                if not self._vivo():
                    pid, host = self._contenido()
                    print(f"  [AVISO] lock huerfano (PID {pid} de {host or 'equipo desconocido'}), se retira",
                          flush=True)
                    self.liberar()
                    continue
                time.sleep(0.5)
        return False

    def liberar(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def _guardar_atomico(ruta, datos):
    ruta = Path(ruta)
    fd, tmp = tempfile.mkstemp(dir=str(ruta.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(datos, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _cargar_json(ruta, default=None):
    if not os.path.exists(ruta):
        return default
    try:
        with open(ruta, encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def leer_ultimo(coin, tf, mercado):
    candidatos = sorted(DIR_JSON.glob(f"nivel_{coin.upper()}_{tf}_{mercado}_k*_toques*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidatos:
        return None
    if len(candidatos) > 1:
        print(f"  [AVISO] {tf}: {len(candidatos)} ficheros con distinto k/toques, "
              f"uso el mas reciente ({candidatos[0].name})", flush=True)
    return _cargar_json(candidatos[0])


def _log(coin, mensaje, consola=False):
    ruta_log = DIR_LOGS / f"niveles_{coin}.log"
    if consola:
        print(f"  {mensaje}", flush=True)
    try:
        with open(ruta_log, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {mensaje}\n")
    except OSError as e:
        print(f"  [AVISO] no se pudo escribir el log: {e}", flush=True)


def _num(params, clave):
    valor = params.get(clave, DEFAULTS.get(clave))
    if valor is None or valor == "":
        return None
    return float(valor)


def _leer_params(params_file, override_confirmacion=None, override_desde_dias=None):
    params = _cargar_json(params_file)
    if params is None:
        raise FileNotFoundError(f"Parametros no encontrados: {params_file}")
    if not params:
        raise ValueError(f"Parametros vacios en: {params_file}")

    k = params.get("k")
    tolerancia_atr = params.get("tolerancia_atr")
    toques_min = params.get("toques_min")
    if k is None or tolerancia_atr is None or toques_min is None:
        raise ValueError(f"Parametros incompletos: k={k}, tolerancia_atr={tolerancia_atr}, toques_min={toques_min}")
    if not (1 <= k <= 20 and 0 < tolerancia_atr <= 5 and 1 <= toques_min <= 10):
        raise ValueError(f"Parametros fuera de rango: k={k} (1-20), "
                         f"tolerancia_atr={tolerancia_atr} (0-5), toques_min={toques_min} (1-10)")

    cfg = {
        "k": int(k),
        "tolerancia_atr": float(tolerancia_atr),
        "toques_min": int(toques_min),
        "confirmacion_velas": int(_num(params, "confirmacion_velas")),
        "desde_dias": _num(params, "desde_dias"),
        "max_dist_pct": _num(params, "max_dist_pct"),
        "max_antig_dias": _num(params, "max_antig_dias"),
        "periodo_atr": int(_num(params, "periodo_atr")),
    }

    sep = _num(params, "separacion_min_atr")
    cfg["separacion_min_atr"] = sep if sep is not None else cfg["tolerancia_atr"] * 2

    if override_confirmacion is not None:
        cfg["confirmacion_velas"] = override_confirmacion
    if override_desde_dias is not None:
        cfg["desde_dias"] = override_desde_dias

    if not 1 <= cfg["confirmacion_velas"] <= 10:
        raise ValueError(f"confirmacion_velas fuera de rango: {cfg['confirmacion_velas']} (1-10)")

    return cfg, params


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


def _dormir(segundos):
    fin = time.time() + segundos
    while not _debe_terminar and time.time() < fin:
        time.sleep(min(1.0, fin - time.time()))


class Vigilante:
    def __init__(self, coin, tf, mercado):
        self.coin = coin
        self.tf = tf
        self.mercado = mercado
        self.cache = CacheVelas(coin, tf, mercado)
        self.params_file = str(DIR_NIVELES / f"params_{coin}_{tf}.json")
        self.cfg_previa = None
        self.avisado_velas = False

    def _salida(self, cfg):
        return str(DIR_JSON / f"nivel_{self.coin.upper()}_{self.tf}_{self.mercado}"
                              f"_k{cfg['k']}_toques{cfg['toques_min']}.json")

    def procesar(self, override_confirmacion, override_desde_dias):
        cfg, params_crudos = _leer_params(self.params_file, override_confirmacion, override_desde_dias)

        if cfg["desde_dias"] is None:
            cfg["desde_dias"] = _desde_dias_default(self.tf)

        hay_velas = self.cache.cambio(cfg["desde_dias"])
        cambio_cfg = cfg != self.cfg_previa
        if not (hay_velas or cambio_cfg):
            return None

        velas, recargado = self.cache.obtener(cfg["desde_dias"])
        if len(velas) < 50:
            raise ValueError(f"Solo {len(velas)} velas en la ventana; se necesitan 50")
        if len(velas) > VELAS_AVISO and not self.avisado_velas:
            print(f"  [AVISO] {self.tf}: {len(velas)} velas en la ventana. El coste crece como "
                  f"el cuadrado y el numero de niveles se estabiliza mucho antes; "
                  f"revisa desde_dias en {os.path.basename(self.params_file)}", flush=True)
            self.avisado_velas = True

        self.cfg_previa = dict(cfg)
        niveles, meta = calcular(velas, cfg)

        ts_ahora = int(datetime.now(timezone.utc).timestamp() * 1000)
        _guardar_atomico(self._salida(cfg), {
            "timestamp": _fmt_fecha(ts_ahora),
            "coin": self.coin.upper(),
            "tf": self.tf,
            "mercado": self.mercado,
            "fecha_ultima_vela": _fmt_fecha(meta["ts_ultima_vela"]),
            "ts_ultima_vela": meta["ts_ultima_vela"],
            "params": params_crudos,
            "config": cfg,
            "atr_actual": meta["atr_actual"],
            "tolerancia_actual": meta["tolerancia_actual"],
            "tolerancia_min": meta["tolerancia_min"],
            "tolerancia_max": meta["tolerancia_max"],
            "separacion_min": meta["separacion_min"],
            "velas_usadas": meta["velas_usadas"],
            "niveles": niveles,
            "precio_actual": meta["precio_actual"],
            "num_niveles": len(niveles),
        })

        return ("VELA" if recargado and not cambio_cfg else "PARAMS"), niveles, meta


def _tfs_disponibles(coin, mercado, solo_tf=None):
    candidatos = [solo_tf] if solo_tf else TIMEFRAMES
    return [tf for tf in candidatos if os.path.exists(_ruta_csv(coin, tf, mercado))]


def loop_principal(coin, mercado, intervalo_seg, desde_dias, confirmacion_velas, solo_tf=None):
    _instalar_handlers_signals()
    _crear_directorios()

    lock = LockFile(str(DIR_JSON / f".niveles_{coin}.lock"), intervalo_seg)
    if not lock.adquirir():
        print(f"ERROR: Ya hay instancia para {coin}")
        return

    try:
        tfs = _tfs_disponibles(coin, mercado, solo_tf)
        if not tfs:
            print(f"ERROR: no hay ningun CSV en {DIR_VELAS} para {coin} {mercado}")
            return

        vigilantes = [Vigilante(coin, tf, mercado) for tf in tfs]

        print(f"[{_fmt_fecha(int(datetime.now(timezone.utc).timestamp() * 1000))}] Iniciando LOOP")
        print(f"  Moneda: {coin.upper()} | Mercado: {mercado} | TF: {', '.join(tfs)}")
        print(f"  Intervalo: {intervalo_seg}s\n")
        _log(coin, f"[ARRANQUE] PID {os.getpid()} | {mercado} | TF {','.join(tfs)} | "
                   f"intervalo {intervalo_seg}s")

        iteracion = 0

        while not _debe_terminar:
            iteracion += 1
            t0 = time.time()
            lock.latir()

            for vig in vigilantes:
                if _debe_terminar:
                    break
                t_tf = time.time()
                try:
                    res = vig.procesar(confirmacion_velas, desde_dias)
                    if res is None:
                        continue
                    marca, niveles, meta = res
                    _log(coin, f"[{marca}] {vig.tf} | {len(niveles)} niveles | "
                               f"precio {meta['precio_actual']:.4f} | "
                               f"vela {_fmt_fecha(meta['ts_ultima_vela'])} | "
                               f"{time.time()-t_tf:.1f}s", consola=True)
                except Exception as e:
                    print(f"  [ERROR] {vig.tf}: {e}", flush=True)
                    _log(coin, f"[ERROR] {vig.tf} iter {iteracion}: {type(e).__name__}: {e}")

            _dormir(max(0.0, intervalo_seg - (time.time() - t0)))

    finally:
        _log(coin, f"[PARADA] PID {os.getpid()}")
        lock.liberar()
        print(f"[{_fmt_fecha(int(datetime.now(timezone.utc).timestamp() * 1000))}] Finalizado")


def main():
    args = sys.argv[1:]
    if not args:
        print(_USO)
        return

    coin = args[0]
    solo_tf = None
    resto = args[1:]
    if resto and resto[0] in TIMEFRAMES:
        solo_tf = resto[0]
        resto = resto[1:]

    intervalo_seg = None
    desde_dias = None
    confirmacion_velas = None
    mercado = MERCADO_POR_DEFECTO
    una_vez = False

    i = 0
    while i < len(resto):
        if resto[i] == "--loop":
            i += 1
            intervalo_seg = int(resto[i])
        elif resto[i] == "--desde-dias":
            i += 1
            desde_dias = float(resto[i])
        elif resto[i] == "--confirmacion-velas":
            i += 1
            confirmacion_velas = int(resto[i])
        elif resto[i] == "--mercado":
            i += 1
            mercado = resto[i]
        elif resto[i] == "--una-vez":
            una_vez = True
        i += 1

    if una_vez:
        _crear_directorios()
        for tf in _tfs_disponibles(coin, mercado, solo_tf) or []:
            vig = Vigilante(coin, tf, mercado)
            t = time.time()
            try:
                res = vig.procesar(confirmacion_velas, desde_dias)
            except Exception as e:
                print(f"{tf}: ERROR {type(e).__name__}: {e}")
                continue
            _marca, niveles, meta = res
            print(f"{tf:>4}  {len(niveles):>3} niveles  precio {meta['precio_actual']:.4f}  "
                  f"vela {_fmt_fecha(meta['ts_ultima_vela'])}  "
                  f"ATR {meta['atr_actual']:.2f}  banda +-{meta['tolerancia_actual']:.2f}  "
                  f"sep {meta['separacion_min']:.2f}  {meta['velas_usadas']} velas  {time.time()-t:.1f}s")
        return

    if intervalo_seg is None:
        print("Requiere: --loop <seg>   (o --una-vez para una sola pasada)")
        return

    loop_principal(coin, mercado, intervalo_seg, desde_dias, confirmacion_velas, solo_tf)


if __name__ == "__main__":
    main()
