
import argparse
import csv
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

DIR_HISTORICOS = os.path.dirname(os.path.abspath(__file__))
DIR_NEO = os.path.dirname(DIR_HISTORICOS)
sys.path.insert(0, os.path.join(DIR_NEO, "niveles"))
from algoritmo_niveles import calcular

CAMPOS = ["ts_calculo", "fecha_calculo", "precio_actual", "precio", "tipo",
          "estado", "toques", "fuerza", "primero", "ts_ultimo_toque",
          "ts_rotura", "dist_pct", "antig_dias"]

VELAS_POR_DIA = {'1m': 1440, '3m': 480, '5m': 288, '15m': 96,
                 '30m': 48, '1h': 24, '4h': 6, '1d': 1}

CFG_BASE = {"k": 5, "tolerancia_atr": 0.15, "toques_min": 3,
            "confirmacion_velas": 2, "periodo_atr": 14,
            "max_dist_pct": 10.0, "max_antig_dias": 180.0,
            "separacion_min_atr": 0.3}


def _config(coin, tf):
    cfg = dict(CFG_BASE)
    ruta = os.path.join(DIR_NEO, "niveles", "params_%s_%s.json" % (coin.lower(), tf))
    if os.path.exists(ruta):
        try:
            with open(ruta, encoding='utf-8-sig') as f:
                cfg.update({k: v for k, v in json.load(f).items() if k in cfg})
        except (OSError, ValueError):
            pass
    return cfg


def _velas(coin, tf):
    patron = os.path.join(DIR_HISTORICOS, "*_%s_%s_binance.csv" % (coin.upper(), tf))
    c = glob.glob(patron)
    if not c:
        raise SystemExit("No hay velas de %s %s en %s\n"
                         "  Bajalas antes: python historicos/descargar_bin.py %s %s"
                         % (coin.upper(), tf, DIR_HISTORICOS, coin.lower(), tf))
    v = []
    with open(max(c, key=os.path.getmtime), encoding='utf-8') as f:
        rd = csv.reader(f); next(rd)
        for x in rd:
            try:
                v.append([int(x[0]), float(x[2]), float(x[3]),
                          float(x[4]), float(x[5]), float(x[6])])
            except (ValueError, IndexError):
                continue
    return v


OVERRIDES = [
    ("k", "k", "k"),
    ("toques_min", "toques_min", "t"),
    ("tolerancia_atr", "tolerancia_atr", "tol"),
    ("confirmacion_velas", "confirmacion_velas", "conf"),
    ("periodo_atr", "periodo_atr", "patr"),
    ("max_dist_pct", "max_dist_pct", "maxd"),
    ("max_antig_dias", "max_antig_dias", "maxa"),
    ("separacion_min_atr", "separacion_min_atr", "sep"),
]


def _sufijo(args):
    partes = []
    for clave_cfg, clave_arg, codigo in OVERRIDES:
        valor = getattr(args, clave_arg)
        if valor is not None:
            partes.append("%s%s" % (codigo, valor))
    return ("_" + "_".join(partes)) if partes else ""


def _existente(coin, tf, sufijo=""):
    c = glob.glob(os.path.join(DIR_HISTORICOS, "*_%s_niveles_%s%s.csv" % (coin.upper(), tf, sufijo)))
    return max(c, key=os.path.getmtime) if c else None


def _ultimo_calculo(ruta):
    ultimo = None
    try:
        with open(ruta, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                try:
                    ultimo = int(r["ts_calculo"])
                except (KeyError, ValueError):
                    continue
    except OSError:
        return None
    return ultimo


def main():
    p = argparse.ArgumentParser(description="Serie historica de niveles, sin lookahead.")
    p.add_argument("coin")
    p.add_argument("tf", choices=sorted(VELAS_POR_DIA))
    p.add_argument("--cada", type=int, default=None,
                   help="velas entre recalculos (default: un dia del tf)")
    p.add_argument("--ventana", type=int, default=4000,
                   help="velas que ve cada recalculo (default 4000)")
    p.add_argument("--desde", default=None, help="YYYY-MM-DD")
    p.add_argument("--k", type=int, default=None)
    p.add_argument("--toques_min", type=int, default=None)
    p.add_argument("--tolerancia_atr", type=float, default=None)
    p.add_argument("--confirmacion_velas", type=int, default=None)
    p.add_argument("--periodo_atr", type=int, default=None)
    p.add_argument("--max_dist_pct", type=float, default=None)
    p.add_argument("--max_antig_dias", type=float, default=None)
    p.add_argument("--separacion_min_atr", type=float, default=None)
    a = p.parse_args()

    coin, tf = a.coin.upper(), a.tf
    cada = a.cada or VELAS_POR_DIA[tf]
    cfg = _config(coin, tf)
    for clave_cfg, clave_arg, _codigo in OVERRIDES:
        valor = getattr(a, clave_arg)
        if valor is not None:
            cfg[clave_cfg] = valor
    sufijo = _sufijo(a)
    v = _velas(coin, tf)
    print("velas %s %s: %d  (%s -> %s)"
          % (coin, tf, len(v),
             time.strftime('%Y-%m-%d', time.gmtime(v[0][0] / 1000)),
             time.strftime('%Y-%m-%d', time.gmtime(v[-1][0] / 1000))))
    print("config: %s%s" % ({k: cfg[k] for k in ('k', 'tolerancia_atr', 'toques_min',
                                                  'max_dist_pct', 'max_antig_dias')},
                            " (sufijo salida: %s)" % sufijo[1:] if sufijo else ""))

    desde_ms = 0
    previo = _existente(coin, tf, sufijo)
    if previo:
        ult = _ultimo_calculo(previo)
        if ult:
            desde_ms = ult + 1
            print("Historico previo hasta %s; sigo desde ahi."
                  % time.strftime('%Y-%m-%d', time.gmtime(ult / 1000)))
    if a.desde:
        desde_ms = max(desde_ms, int(datetime.strptime(a.desde, "%Y-%m-%d")
                                     .replace(tzinfo=timezone.utc).timestamp() * 1000))

    ini = max(300, next((i for i, x in enumerate(v) if x[0] >= desde_ms), len(v)))

    nueva = os.path.join(DIR_HISTORICOS, "%s_%s_niveles_%s%s.csv"
                         % (datetime.now(timezone.utc).strftime("%d-%m-%y"), coin, tf, sufijo))
    if previo and previo != nueva:
        os.replace(previo, nueva)
    modo = 'a' if previo else 'w'

    t0 = time.time(); n_calc = n_filas = 0
    with open(nueva, modo, newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if modo == 'w':
            w.writeheader()
        for i in range(ini, len(v), cada):
            ventana = v[max(0, i - a.ventana):i + 1]
            if len(ventana) < 300:
                continue
            try:
                niv, meta = calcular(ventana, cfg)
            except Exception as e:
                print("  aviso: fallo en %s: %s"
                      % (time.strftime('%Y-%m-%d', time.gmtime(v[i][0] / 1000)), str(e)[:60]))
                continue
            n_calc += 1
            ts_c = v[i][0]
            for n in niv:
                w.writerow({
                    "ts_calculo": ts_c,
                    "fecha_calculo": time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts_c / 1000)),
                    "precio_actual": meta["precio_actual"],
                    "precio": n["precio"], "tipo": n["tipo"], "estado": n["estado"],
                    "toques": n["toques"], "fuerza": round(n["fuerza"], 4),
                    "primero": n.get("primero", ""),
                    "ts_ultimo_toque": n.get("ts_ultimo_toque", ""),
                    "ts_rotura": n.get("ts_rotura") or "",
                    "dist_pct": round(n.get("dist_pct", 0), 4),
                    "antig_dias": round(n.get("antig_dias", 0), 3),
                })
                n_filas += 1
            if n_calc % 200 == 0:
                print("  %d recalculos, %d filas, %.0f s"
                      % (n_calc, n_filas, time.time() - t0), flush=True)

    print("[OK] %d recalculos, %d filas en %.0f s -> %s"
          % (n_calc, n_filas, time.time() - t0, os.path.basename(nueva)))


if __name__ == "__main__":
    main()
