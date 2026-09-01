#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ic.py - ¿Alguna variable del flujo predice el retorno futuro?

Contesta con un t-stat, no con una corazonada. Esta pensado para relanzarse
cada pocas semanas segun se acumula muestra: la pregunta "¿hay ya senal?" tiene
respuesta numerica, y hasta que la tenga no merece la pena disenar estrategia.

No depende de analyzer ni de sus umbrales. Mide directamente sobre los CSV de
flujo.py: para cada variable candidata y cada horizonte, si el valor en t dice
algo del retorno entre t y t+h.

Por que existe:

  En la primera medicion (605 evaluaciones del 25/08 al 01/09/2026, ya con el
  tape correcto de flujo.py) el generador de senales de analyzer daba 47,3% de
  acierto BRUTO y profit factor 0,962 antes de costes: una moneda al aire. No
  era mala calibracion de umbrales, era ausencia de senal. Este script separa
  las dos cosas de una vez por todas.

Metodo, y por que cada pieza:

  - Muestras NO SOLAPADAS por horizonte. Con solape el t-stat se infla:
    observaciones consecutivas comparten casi todo el movimiento.

  - IC de Spearman (rangos), no Pearson. Los retornos tienen colas gordas y
    un solo minuto extremo mueve una correlacion lineal.

  - Error estandar y t explicitos. Un IC de 0,15 sobre 185 muestras no
    significa nada; sin el t no se puede saber.

  - Correccion por PRUEBAS MULTIPLES. Con 9 variables x 6 horizontes son 54
    pruebas: con ruido puro se esperan ~2,5 resultados con |t|>2. Solo cuenta
    lo que supera el umbral de Bonferroni. Sin esto se "encuentran" senales
    que no existen -- paso en la primera version de este script.

  - Estabilidad entre mitades EXIGENTE: mismo signo y ambas mitades con al
    menos la mitad del IC global. El criterio laxo (solo mismo signo) daba por
    estable un +0,235 seguido de un +0,007.

  - Prueba economica con intervalo de confianza: el spread entre deciles
    extremos tiene que superar el coste de ida y vuelta CON su IC 95% por
    encima, no solo su media. El coste por defecto (0,18%) es taker 0,06% +
    slippage 0,03% por lado, que es lo que aplica backtest.py.

Uso:
  python analizador/ic.py
  python analizador/ic.py --coin ETH --mercado futuros
  python analizador/ic.py --horizontes 5,15,60 --coste 0.18
  python analizador/ic.py --dir /otra/ruta/flujo

Lectura del resultado:
  Si la ultima linea dice SIN SENAL, no hay nada sobre lo que construir y
  cualquier estrategia ajustada a estos datos es sobreajuste. Volver a correrlo
  cuando haya bastante mas muestra.

Referencia de potencia (horizonte 60 min, muestras no solapadas):
    7 dias  ->   185 muestras  -> detecta |IC| > 0,15 aprox
   30 dias  ->   720 muestras  -> detecta |IC| > 0,08
   60 dias  -> 1.440 muestras  -> detecta |IC| > 0,05
"""

import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
FLUJO_DIR = os.path.join(BASE_DIR, "libro", "datos", "flujo")

# Coste de ida y vuelta en % del nocional: taker 0,06% + slippage 0,03%, por
# lado. Mismo criterio que backtest.get_fees, para que las dos herramientas
# hablen del mismo coste.
COSTE_IDA_VUELTA_PCT = 0.18

HORIZONTES = (1, 5, 15, 30, 60, 240)


def cargar(directorio, coin, mercado):
    patron = os.path.join(directorio, "flujo_%s_%s_*.csv" % (coin.upper(), mercado))
    ficheros = sorted(glob.glob(patron))
    if not ficheros:
        raise SystemExit("no hay CSV de flujo en %s (%s)" % (directorio, patron))
    d = pd.concat([pd.read_csv(f) for f in ficheros], ignore_index=True)
    d = d.sort_values("ventana_fin_ms").drop_duplicates("ventana_fin_ms").reset_index(drop=True)
    return d, len(ficheros)


def variables(d):
    """Candidatas derivadas del flujo. Todas son conocidas EN t: nada que use
    informacion posterior, o el resultado seria mirar el futuro."""
    vol = d["vol_buy"] + d["vol_sell"]
    v = pd.DataFrame(index=d.index)
    v["delta"] = d["delta_vol"]
    v["delta_norm"] = d["delta_vol"] / vol.replace(0, np.nan)
    v["dvol_z"] = (vol - vol.rolling(60).mean()) / vol.rolling(60).std()
    v["ntrades"] = d["n_trades"]
    v["tam_medio"] = vol / d["n_trades"].replace(0, np.nan)
    v["rango"] = (d["precio_max"] - d["precio_min"]) / d["precio_cierre"] * 100
    v["desv_vwap"] = (d["precio_cierre"] - d["vwap"]) / d["vwap"] * 100
    v["ret_prev"] = d["precio_cierre"].pct_change() * 100
    v["cvd_pend"] = d["cvd"].diff(15)
    return v


def spearman(x, y, minimo=30):
    m = x.notna() & y.notna()
    n = int(m.sum())
    if n < minimo:
        return np.nan, np.nan, n
    r = x[m].rank().corr(y[m].rank())
    if pd.isna(r):
        return np.nan, np.nan, n
    return r, r * np.sqrt(n - 1), n


def prueba_economica(x, fwd, coste):
    """Spread entre deciles extremos, con su intervalo de confianza."""
    m = x.notna() & fwd.notna()
    if m.sum() < 100:
        return None
    xx, yy = x[m], fwd[m]
    try:
        q = pd.qcut(xx, 10, labels=False, duplicates="drop")
    except ValueError:
        return None
    alto, bajo = yy[q == q.max()], yy[q == 0]
    if len(alto) < 5 or len(bajo) < 5:
        return None
    sp = alto.mean() - bajo.mean()
    se = np.sqrt(alto.var(ddof=1) / len(alto) + bajo.var(ddof=1) / len(bajo))
    return {"spread": sp, "se": se, "n_decil": min(len(alto), len(bajo)),
            "lo": sp - 1.96 * se, "hi": sp + 1.96 * se,
            "supera": (abs(sp) - 1.96 * se) > coste}


def main():
    p = argparse.ArgumentParser(
        description="Mide si alguna variable del flujo predice el retorno futuro.")
    p.add_argument("--coin", default="ETH")
    p.add_argument("--mercado", default="futuros")
    p.add_argument("--dir", default=FLUJO_DIR, help="carpeta de los CSV de flujo")
    p.add_argument("--horizontes", default=",".join(str(h) for h in HORIZONTES),
                   help="horizontes en minutos, separados por coma")
    p.add_argument("--coste", type=float, default=COSTE_IDA_VUELTA_PCT,
                   help="coste ida+vuelta en %% del nocional (default: 0.18)")
    a = p.parse_args()

    horizontes = [int(h) for h in a.horizontes.split(",") if h.strip()]
    d, nf = cargar(a.dir, a.coin, a.mercado)
    v = variables(d)
    nombres = list(v.columns)

    dur_h = (d["ventana_fin_ms"].iloc[-1] - d["ventana_fin_ms"].iloc[0]) / 3600000.0
    n_pruebas = len(nombres) * len(horizontes)
    # Bonferroni: p=0,05 repartido entre todas las pruebas -> z equivalente.
    from statistics import NormalDist
    umbral = NormalDist().inv_cdf(1 - 0.05 / (2 * n_pruebas))

    print("=" * 78)
    print("IC DEL FLUJO  |  %s %s  |  %d ficheros, %d ventanas, %.1f dias"
          % (a.coin.upper(), a.mercado, nf, len(d), dur_h / 24))
    print("%d variables x %d horizontes = %d pruebas  ->  umbral Bonferroni |t| > %.2f"
          % (len(nombres), len(horizontes), n_pruebas, umbral))
    print("coste ida+vuelta usado en la prueba economica: %.2f%%" % a.coste)
    print("=" * 78)

    hallazgos = []
    for h in horizontes:
        fwd = (d["precio_cierre"].shift(-h) / d["precio_cierre"] - 1) * 100
        sub, fsub = v.iloc[::h], fwd.iloc[::h]
        mitad = len(sub) // 2
        print("\n--- HORIZONTE %d min  (n no solapado = %d) ---" % (h, len(sub)))
        print("  %-12s %8s %7s %9s %9s  %s" % ("variable", "IC", "t", "1a mitad", "2a mitad", "veredicto"))
        for nom in nombres:
            r, t, n = spearman(sub[nom], fsub)
            r1, _, _ = spearman(sub[nom].iloc[:mitad], fsub.iloc[:mitad])
            r2, _, _ = spearman(sub[nom].iloc[mitad:], fsub.iloc[mitad:])
            if np.isnan(r):
                print("  %-12s %8s" % (nom, "sin datos"))
                continue
            # Exigente: supera Bonferroni Y las dos mitades van en el mismo
            # sentido con al menos la mitad de la magnitud global.
            estable = (not np.isnan(r1) and not np.isnan(r2)
                       and np.sign(r1) == np.sign(r2) == np.sign(r)
                       and min(abs(r1), abs(r2)) >= abs(r) / 2)
            if abs(t) > umbral and estable:
                ver, hallazgos = "CANDIDATO", hallazgos + [(nom, h, r, t)]
            elif abs(t) > umbral:
                ver = "signif. pero inestable"
            elif estable and abs(t) > 2:
                ver = "estable pero no signif."
            else:
                ver = "-"
            # "n/d" y no 0,000: una mitad sin muestra suficiente no es un IC
            # de cero, y presentarla asi invita justo al error que este script
            # existe para evitar.
            f1 = "%+9.3f" % r1 if not np.isnan(r1) else "%9s" % "n/d"
            f2 = "%+9.3f" % r2 if not np.isnan(r2) else "%9s" % "n/d"
            print("  %-12s %+8.3f %+7.2f %s %s  %s" % (nom, r, t, f1, f2, ver))

        eco = prueba_economica(sub["delta_norm"], fsub, a.coste)
        if eco:
            print("  economico (delta_norm, %d por decil): spread %+.4f%% +- %.4f  "
                  "IC95 [%+.4f%%, %+.4f%%]  -> %s"
                  % (eco["n_decil"], eco["spread"], eco["se"], eco["lo"], eco["hi"],
                     "SUPERA el coste" if eco["supera"] else "no cubre costes"))

    print("\n" + "=" * 78)
    if hallazgos:
        print("CANDIDATOS (superan Bonferroni y son estables entre mitades):")
        for nom, h, r, t in hallazgos:
            print("   %-12s horizonte %3d min   IC %+.3f   t %+.2f" % (nom, h, r, t))
        print("\nSiguiente paso: verificar fuera de muestra antes de construir nada")
        print("encima. Un candidato en muestra no es una senal.")
    else:
        print("SIN SENAL: ninguna variable supera el umbral con estabilidad.")
        print("No hay base para disenar una estrategia con estos datos. Ajustar")
        print("umbrales sobre esto seria sobreajustar ruido.")
        print("Volver a correrlo con mas muestra (ver referencia de potencia en la")
        print("cabecera de este fichero).")
    print("=" * 78)


if __name__ == "__main__":
    main()
