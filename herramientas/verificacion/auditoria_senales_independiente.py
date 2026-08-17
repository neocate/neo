# Uso:
#   python herramientas/verificacion/auditoria_senales_independiente.py <coin> <tf> [--desde YYYY-MM-DD]

import csv
import os
import re
import sys
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from mercado import senales
from herramientas import niveles as niv


def cargar_velas(ruta, desde_ms=None, hasta_ms=None):
    velas = []
    with open(ruta, newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 7:
                continue
            ts = int(row[0])
            if desde_ms is not None and ts < desde_ms:
                continue
            if hasta_ms is not None and ts > hasta_ms:
                continue
            velas.append([ts, float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])])
    return velas


def niveles_vigentes_en(velas, hasta_idx, lookback_ms, k, tolerancia_atr, toques_min, confirmacion_velas):
    """niveles_vigentes_en(velas, hasta_idx, lookback_ms, k, tolerancia_atr, toques_min, confirmacion_velas) -> (list[(precio, tipo, rol)], tolerancia: float)"""
    ts_ref = velas[hasta_idx][0]
    previas = [v for v in velas[:hasta_idx] if v[0] >= ts_ref - lookback_ms]
    if len(previas) < 100:
        return [], 0.0
    niveles, atr_ref, tolerancia = niv.detectar_niveles(previas, k, tolerancia_atr, toques_min)
    resultado = []
    for n in niveles:
        estado, _, _ = niv._evaluar_estado(previas, n["precio"], n["tipo"], tolerancia,
                                            n["ultimo"], confirmacion_velas)
        n["estado"] = estado
        if estado in ("vivo", "flip"):
            resultado.append((n["precio"], n["tipo"], niv._rol_efectivo(n)))
    return resultado, tolerancia


def auditar(coin, tf, ruta, k=3, tolerancia_atr=0.25, toques_min=4, confirmacion_velas=2,
            refresco_dias=30.0, lookback_dias=90.0, horizontes=(5, 15, 30),
            desde_ms=None, hasta_ms=None):
    print(f"\n{'=' * 100}\n{coin.upper()} {tf}  (k={k} tolerancia_atr={tolerancia_atr} "
          f"toques_min={toques_min} confirmacion_velas={confirmacion_velas})\n{'=' * 100}")

    lookback_ms = int(lookback_dias * 86_400_000)
    desde_carga = (desde_ms - lookback_ms) if desde_ms else None
    velas = cargar_velas(ruta, desde_carga, hasta_ms)
    print(f"{len(velas)} velas cargadas: "
          f"{datetime.fromtimestamp(velas[0][0] / 1000, timezone.utc)} -> "
          f"{datetime.fromtimestamp(velas[-1][0] / 1000, timezone.utc)}")

    idx_inicio = 0
    if desde_ms is not None:
        idx_inicio = next((i for i, v in enumerate(velas) if v[0] >= desde_ms), len(velas))
    idx_inicio = max(idx_inicio, senales.cargar_params()["ventana_maxima"], 200)

    refresco_ms = int(refresco_dias * 86_400_000)
    ultimo_recalculo = None
    niveles_vig = []
    tolerancia_nivel = 0.0

    disparos = {}
    baseline = {h: [] for h in horizontes}
    max_h = max(horizontes)

    ventana_max = senales.cargar_params()["ventana_maxima"]
    n = len(velas)
    for i in range(idx_inicio, n - max_h):
        ts_i, cierre_i = velas[i][0], velas[i][4]

        if ultimo_recalculo is None or ts_i - ultimo_recalculo >= refresco_ms:
            niveles_vig, tolerancia_nivel = niveles_vigentes_en(
                velas, i, lookback_ms, k, tolerancia_atr, toques_min, confirmacion_velas)
            ultimo_recalculo = ts_i

        for h in horizontes:
            baseline[h].append((velas[i + h][4] - cierre_i) / cierre_i * 100)

        ventana = velas[max(0, i + 1 - ventana_max):i + 1]
        activas = senales.detectar(ventana, k)
        if not activas:
            continue

        techo_cerca = any(rol == "techo" and abs(cierre_i - precio) <= tolerancia_nivel
                           for precio, tipo_orig, rol in niveles_vig)
        suelo_cerca = any(rol == "suelo" and abs(cierre_i - precio) <= tolerancia_nivel
                           for precio, tipo_orig, rol in niveles_vig)

        for nombre in activas:
            for h in horizontes:
                retorno = (velas[i + h][4] - cierre_i) / cierre_i * 100
                disparos.setdefault(nombre, {}).setdefault(h, []).append((retorno, techo_cerca, suelo_cerca))

    return disparos, baseline


def _media(xs):
    return sum(xs) / len(xs) if xs else None


def informar(disparos, baseline, horizontes):
    baseline_media = {h: _media(baseline[h]) for h in horizontes}
    print(f"\nbaseline (retorno medio de CUALQUIER vela, sin signo): "
          + "  ".join(f"@{h}={baseline_media[h]:+.3f}%" for h in horizontes))

    print(f"\n{'señal':<16}{'n':>7}   ", end="")
    for h in horizontes:
        print(f"ret@{h}(sin signo)  ", end="")
    print()
    for nombre in sorted(disparos):
        n_total = len(disparos[nombre][horizontes[0]])
        print(f"{nombre:<16}{n_total:>7}   ", end="")
        for h in horizontes:
            retornos = [r for r, _, _ in disparos[nombre][h]]
            print(f"{_media(retornos):>+16.3f}  ", end="")
        print()

    print(f"\n--- Desglose por contexto de nivel (SIN asumir direccion - solo lo que hay) ---")
    print(f"{'señal':<16}{'ctx':<8}{'n':>6}   ", end="")
    for h in horizontes:
        print(f"ret@{h}      ", end="")
    print()
    for nombre in sorted(disparos):
        for ctx_nombre, filtro in [("techo", lambda t, s: t and not s),
                                     ("suelo", lambda t, s: s and not t),
                                     ("ambos", lambda t, s: t and s),
                                     ("ninguno", lambda t, s: not t and not s)]:
            retornos_h0 = [r for r, t, s in disparos[nombre][horizontes[0]] if filtro(t, s)]
            if not retornos_h0:
                continue
            print(f"{nombre:<16}{ctx_nombre:<8}{len(retornos_h0):>6}   ", end="")
            for h in horizontes:
                retornos = [r for r, t, s in disparos[nombre][h] if filtro(t, s)]
                m = _media(retornos)
                print(f"{m:>+10.3f}  " if m is not None else f"{'--':>10}  ", end="")
            print()


def _localizar_historicos(coin, tf):
    d = os.path.join(RAIZ, "historicos")
    patron = re.compile(rf"^\d{{2}}-\d{{2}}-\d{{2}}_{coin.upper()}_{tf}_binance\.csv$")
    candidatos = [os.path.join(d, f) for f in os.listdir(d) if patron.match(f)]
    if not candidatos:
        return None
    return max(candidatos, key=os.path.getmtime)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("coin")
    p.add_argument("tf")
    p.add_argument("--desde", default="2018-01-01")
    args = p.parse_args()

    desde_ms = int(datetime.strptime(args.desde, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    ruta = _localizar_historicos(args.coin, args.tf)
    if ruta is None:
        print(f"No hay CSV en historicos/ para {args.coin.upper()} {args.tf}")
        sys.exit(1)

    disparos, baseline = auditar(args.coin, args.tf, ruta, desde_ms=desde_ms)
    informar(disparos, baseline, (5, 15, 30))
