
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
FLUJO_DIR = os.path.join(BASE_DIR, "libro", "datos", "flujo")

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
