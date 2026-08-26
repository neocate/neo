import argparse
import math
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import io_velas
from algoritmo_niveles import _fmt_fecha, calcular
from io_velas import CacheVelas, TIMEFRAMES, _tf_a_ms, _tfs_disponibles, validar_coin
from persistencia import DIR_JSON, LockFile, _cargar_json, _crear_directorios, _guardar_atomico, _log

DIR_NIVELES = Path(__file__).resolve().parent

MERCADO_POR_DEFECTO = "futuros"

DEFAULTS = {
    "confirmacion_velas": 2,
    "max_dist_pct": 10.0,
    "max_antig_dias": 180.0,
    "separacion_min_atr": None,
    "periodo_atr": 14,
}

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


_debe_terminar = False


def _handler_signals(signum, frame):
    global _debe_terminar
    _debe_terminar = True
    print("\n[SIGNAL] Terminando gracefully...", flush=True)


def _instalar_handlers_signals():
    signal.signal(signal.SIGTERM, _handler_signals)
    signal.signal(signal.SIGINT, _handler_signals)


def _tipo_coin(valor):
    try:
        return validar_coin(valor)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _entero_positivo(valor):
    try:
        n = int(valor)
    except ValueError:
        raise argparse.ArgumentTypeError(f"debe ser un entero: {valor!r}")
    if n <= 0:
        raise argparse.ArgumentTypeError(f"debe ser > 0: {valor!r}")
    return n


def _flotante_positivo(valor):
    try:
        n = float(valor)
    except ValueError:
        raise argparse.ArgumentTypeError(f"debe ser un numero: {valor!r}")
    if not math.isfinite(n) or n <= 0:
        raise argparse.ArgumentTypeError(f"debe ser un numero finito > 0: {valor!r}")
    return n


def _confirmacion_velas_cli(valor):
    try:
        n = int(valor)
    except ValueError:
        raise argparse.ArgumentTypeError(f"debe ser un entero: {valor!r}")
    if not 1 <= n <= 10:
        raise argparse.ArgumentTypeError(f"debe estar entre 1 y 10: {valor!r}")
    return n


def _construir_parser():
    p = argparse.ArgumentParser(
        prog="niveles.py",
        description="Detecta soportes y resistencias sobre las velas de una moneda.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_USO)
    p.add_argument("coin", type=_tipo_coin)
    p.add_argument("tf", nargs="?", choices=TIMEFRAMES, default=None)
    p.add_argument("--loop", type=_entero_positivo, default=None)
    p.add_argument("--una-vez", action="store_true")
    p.add_argument("--mercado", choices=["spot", "futuros"], default=MERCADO_POR_DEFECTO)
    p.add_argument("--desde-dias", type=_flotante_positivo, default=None)
    p.add_argument("--confirmacion-velas", type=_confirmacion_velas_cli, default=None)
    return p


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
            print(f"ERROR: no hay ningun CSV en {io_velas.DIR_VELAS} para {coin} {mercado}")
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
                    _log(coin, f"[ERROR] {vig.tf} iter {iteracion}: {type(e).__name__}: {e}", exc_info=True)

            _dormir(max(0.0, intervalo_seg - (time.time() - t0)))

    finally:
        _log(coin, f"[PARADA] PID {os.getpid()}")
        lock.liberar()
        print(f"[{_fmt_fecha(int(datetime.now(timezone.utc).timestamp() * 1000))}] Finalizado")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(_USO)
        return

    parser = _construir_parser()
    args = parser.parse_args(argv)

    coin = args.coin
    solo_tf = args.tf
    mercado = args.mercado

    if args.una_vez:
        _crear_directorios()
        for tf in _tfs_disponibles(coin, mercado, solo_tf) or []:
            vig = Vigilante(coin, tf, mercado)
            t = time.time()
            try:
                res = vig.procesar(args.confirmacion_velas, args.desde_dias)
            except Exception as e:
                print(f"{tf}: ERROR {type(e).__name__}: {e}")
                _log(coin, f"[ERROR] {tf} --una-vez: {type(e).__name__}: {e}", exc_info=True)
                continue
            _marca, niveles, meta = res
            print(f"{tf:>4}  {len(niveles):>3} niveles  precio {meta['precio_actual']:.4f}  "
                  f"vela {_fmt_fecha(meta['ts_ultima_vela'])}  "
                  f"ATR {meta['atr_actual']:.2f}  banda +-{meta['tolerancia_actual']:.2f}  "
                  f"sep {meta['separacion_min']:.2f}  {meta['velas_usadas']} velas  {time.time()-t:.1f}s")
        return

    if not args.una_vez and args.loop is None:
        print("Requiere: --loop <seg>   (o --una-vez para una sola pasada)")
        return

    loop_principal(coin, mercado, args.loop, args.desde_dias, args.confirmacion_velas, solo_tf)


if __name__ == "__main__":
    main()
