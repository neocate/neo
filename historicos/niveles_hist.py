# ---------------------------------------------------------------
# niveles_hist.py - Serie HISTORICA de niveles, calculada progresivamente.
#
# Por que no valen los JSON de niveles/json:
#
#   Esos ficheros son una FOTO del estado de hoy, y para mirar hacia atras
#   tienen dos defectos que los invalidan:
#
#   1. Sesgo de supervivencia. Contienen los niveles que siguen existiendo
#      AHORA. Los que se formaron en 2019, funcionaron un tiempo y luego
#      desaparecieron no estan. Medir con esa lista es usar solo los niveles
#      que resultaron duraderos, cosa que entonces no se podia saber.
#
#   2. Campos acumulados hasta hoy. Un nivel formado en 2023 aparece con 55
#      toques y su ultimo toque en 2026. Usar ese recuento para decidir algo
#      en 2024 es darle al pasado informacion del futuro.
#
#   Aqui se recalcula en cada punto usando SOLO velas anteriores. Es mas
#   lento, pero es la unica forma de que una prueba historica signifique
#   algo. (Es lo mismo que ya hace analyzer.cargar_niveles_hist en cada
#   as_of, y por identico motivo.)
#
# Que guarda, y por que mas que los JSON:
#
#   Ademas del precio, el tipo y el estado, se guardan las MARCAS DE TIEMPO
#   (primero, ultimo toque, rotura) y la distancia al precio. Sin ellas no
#   se puede filtrar por antiguedad, y un nivel de hace dos meses al 30% del
#   precio no dice lo mismo que uno de esta semana al 5%. La configuracion
#   de produccion filtra a max_dist_pct=10 y max_antig_dias=180, que para
#   esto ultimo es muy laxo: seis meses.
#
# Salida (CSV, una fila por nivel y recalculo, en <raiz>/historicos/):
#     DD-MM-AA_<COIN>_niveles_<TF>.csv
#
#   ts_calculo, fecha_calculo  cuando se calculo (cierre de la ultima vela usada)
#   precio_actual              precio en ese momento
#   precio, tipo, estado       el nivel
#   toques, fuerza             cuantas veces se respeto y con que consistencia
#   primero, ts_ultimo_toque   marcas de formacion y ultimo contacto
#   ts_rotura                  cuando se rompio (vacio si no)
#   dist_pct, antig_dias       distancia al precio y edad, EN ESE MOMENTO
#
# Incremental: si el fichero existe, se lee la ultima marca de su contenido
# y solo se calcula lo que falta. Mismo patron que descargar_bin.py.
#
# Uso:
#   python historicos/niveles_hist.py <coin> <tf> [--cada N] [--ventana N]
#
#     --cada     velas entre recalculos (default: las de un dia segun el tf)
#     --ventana  velas que ve cada recalculo (default 4000)
#     --desde    YYYY-MM-DD, por defecto el origen del CSV de velas
#
# Ejemplos:
#   python historicos/niveles_hist.py eth 1h
#   python historicos/niveles_hist.py btc 4h --ventana 3000
#   python historicos/niveles_hist.py icp 1h --cada 12
#
# Coste: ~0,7 s por recalculo con ventana de 4000. Un activo en 1h desde
# 2019 son ~2.400 recalculos diarios, unos 30 min. Lanzalo en segundo plano.
# ---------------------------------------------------------------

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

# Config de niveles. Se lee del params_<coin>_<tf>.json del proyecto si existe,
# para que el historico se calcule con los MISMOS parametros que produccion;
# si no, estos valores, que son los que usa niveles.py por defecto.
CFG_BASE = {"k": 5, "tolerancia_atr": 0.15, "toques_min": 3,
            "confirmacion_velas": 2, "periodo_atr": 14,
            "max_dist_pct": 10.0, "max_antig_dias": 180.0,
            "separacion_min_atr": 0.3}


def _config(coin, tf):
    """Parametros de produccion si los hay; si no, los de por defecto."""
    cfg = dict(CFG_BASE)
    ruta = os.path.join(DIR_NEO, "niveles", "params_%s_%s.json" % (coin.lower(), tf))
    if os.path.exists(ruta):
        try:
            # utf-8-sig: estos ficheros llevan BOM
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


def _existente(coin, tf):
    c = glob.glob(os.path.join(DIR_HISTORICOS, "*_%s_niveles_%s.csv" % (coin.upper(), tf)))
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
    a = p.parse_args()

    coin, tf = a.coin.upper(), a.tf
    cada = a.cada or VELAS_POR_DIA[tf]
    cfg = _config(coin, tf)
    v = _velas(coin, tf)
    print("velas %s %s: %d  (%s -> %s)"
          % (coin, tf, len(v),
             time.strftime('%Y-%m-%d', time.gmtime(v[0][0] / 1000)),
             time.strftime('%Y-%m-%d', time.gmtime(v[-1][0] / 1000))))
    print("config: %s" % {k: cfg[k] for k in ('k', 'tolerancia_atr', 'toques_min',
                                              'max_dist_pct', 'max_antig_dias')})

    desde_ms = 0
    previo = _existente(coin, tf)
    if previo:
        ult = _ultimo_calculo(previo)
        if ult:
            desde_ms = ult + 1
            print("Historico previo hasta %s; sigo desde ahi."
                  % time.strftime('%Y-%m-%d', time.gmtime(ult / 1000)))
    if a.desde:
        desde_ms = max(desde_ms, int(datetime.strptime(a.desde, "%Y-%m-%d")
                                     .replace(tzinfo=timezone.utc).timestamp() * 1000))

    # el primer recalculo necesita historia suficiente para que el ATR y los
    # toques signifiquen algo; por debajo de 300 velas el resultado es ruido
    ini = max(300, next((i for i, x in enumerate(v) if x[0] >= desde_ms), len(v)))

    nueva = os.path.join(DIR_HISTORICOS, "%s_%s_niveles_%s.csv"
                         % (datetime.now(timezone.utc).strftime("%d-%m-%y"), coin, tf))
    if previo and previo != nueva:
        os.replace(previo, nueva)
    modo = 'a' if previo else 'w'

    t0 = time.time(); n_calc = n_filas = 0
    with open(nueva, modo, newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if modo == 'w':
            w.writeheader()
        for i in range(ini, len(v), cada):
            ventana = v[max(0, i - a.ventana):i + 1]     # SOLO pasado, vela i incluida y cerrada
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
