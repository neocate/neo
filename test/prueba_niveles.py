#!/usr/bin/env python3
"""
PRUEBA DE NIVELES - Análisis offline desde históricos Binance

=== EJEMPLOS DE USO ===

Windows/Linux/NAS - Análisis de un coin/tf:
  cd D:\neocat\neo
  python test/prueba_niveles.py eth 1m --k 3 --tolerancia-atr 0.25 --toques-min 3
  python test/prueba_niveles.py btc 4h --k 3 --tolerancia-atr 0.25 --toques-min 3

Windows/Linux/NAS - Con filtro de días:
  python test/prueba_niveles.py eth 1h --k 3 --tolerancia-atr 0.25 --toques-min 3 --confirmacion-velas 2

=== DATOS ESPERADOS ===
Carga desde: historicos/[date]_[coin]_[tf]_binance.csv
Ejemplo: historicos/17-08-26_ETH_1m_binance.csv

=== SALIDA ===
- Niveles detectados
- Estatísticas (ATR, tolerancia)
- Precio actual vs niveles
- Techos y suelos vigentes
"""

import csv
import os
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mercado import indicadores
from herramientas import niveles

Vela = namedtuple("Vela", ["ts", "h", "l", "c", "vol"])

DIR_HISTORICOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "historicos")


def cargar_velas_historicos(coin, tf):
    """Carga velas de archivos en historicos/"""
    archivos = list(Path(DIR_HISTORICOS).glob(f"*_{coin}_{tf}_binance.csv"))

    if not archivos:
        raise FileNotFoundError(
            f"No hay archivos para {coin} {tf} en {DIR_HISTORICOS}")

    archivo = archivos[-1]  # toma el más reciente si hay varios
    print(f"Leyendo: {archivo}")

    velas = []
    with open(archivo, newline='', encoding='utf-8') as f:
        r = csv.reader(f)
        next(r)  # salta header
        for row in r:
            try:
                ts = int(row[0])
                velas.append(Vela(ts=ts, h=float(row[3]), l=float(row[4]),
                                 c=float(row[5]), vol=float(row[6])))
            except (ValueError, IndexError) as e:
                print(f"  (error en fila) {e}")
                continue

    return velas, str(archivo)


def prueba(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas=2):
    """Prueba análisis de niveles con históricos."""
    config = niveles.cargar_config()

    velas, ruta = cargar_velas_historicos(coin, tf)
    print(f"Cargadas {len(velas)} velas\n")

    if velas:
        print(f"Rango: {niveles._fmt_fecha(velas[0].ts)} -> {niveles._fmt_fecha(velas[-1].ts)}")

    niveles_detectados, atr_ref, tolerancia = niveles.detectar_niveles(
        velas, k, tolerancia_atr, toques_min, config)

    precio_actual = velas[-1].c if velas else None
    ts_final = velas[-1].ts if velas else None

    if precio_actual is None or precio_actual <= 0:
        print("Error: precio actual inválido")
        return

    print(f"\nATR ref={atr_ref:.4f}  tolerancia precio={tolerancia:.4f}")
    print(f"Precio actual: {precio_actual:.4f}")
    print(f"\nDetectados {len(niveles_detectados)} niveles:\n")

    for niv in niveles_detectados:
        estado, _, _ = niveles._evaluar_estado(
            velas, niv["precio"], niv["tipo"], tolerancia, niv["ultimo"], confirmacion_velas)
        niv["estado"] = estado
        niv["dist_pct"] = (niv["precio"] - precio_actual) / precio_actual * 100
        niv["antig_dias"] = (ts_final - niv["ultimo"]) / 86400000 if ts_final and niv["ultimo"] else 0

    vigentes = [niv for niv in niveles_detectados if niv["estado"] in ("vivo", "flip")]

    print(f"{'tipo':<7} {'precio':>12} {'toques':>8} {'estado':<8} {'dist%':>8}")
    for niv in sorted(niveles_detectados, key=lambda d: -d["precio"]):
        print(f"{niv['tipo']:<7} {niv['precio']:>12.4f} {niv['toques']:>8.1f} "
              f"{niv['estado']:<8} {niv['dist_pct']:>8.2f}%")

    print(f"\n=== Vigentes ({len(vigentes)}) ===")
    techos = sorted((niv for niv in vigentes if niv["tipo"] == "techo"),
                    key=lambda d: abs(d["dist_pct"]))
    suelos = sorted((niv for niv in vigentes if niv["tipo"] == "suelo"),
                    key=lambda d: abs(d["dist_pct"]))

    if techos:
        print("\nTechos:")
        for niv in techos:
            print(f"  {niv['precio']:>12.4f}  dist {niv['dist_pct']:>7.2f}%  toques {niv['toques']:>6.1f}")

    if suelos:
        print("\nSuelos:")
        for niv in suelos:
            print(f"  {niv['precio']:>12.4f}  dist {niv['dist_pct']:>7.2f}%  toques {niv['toques']:>6.1f}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python prueba_niveles.py <coin> <tf> --k N --tolerancia-atr N --toques-min N")
        print("Ejemplo: python prueba_niveles.py BTC 1m --k 3 --tolerancia-atr 0.25 --toques-min 3")
        sys.exit(1)

    coin = sys.argv[1].upper()
    tf = sys.argv[2]

    k = tolerancia_atr = toques_min = None
    confirmacion_velas = 2

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--k":
            k = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--tolerancia-atr":
            tolerancia_atr = float(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--toques-min":
            toques_min = int(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--confirmacion-velas":
            confirmacion_velas = int(sys.argv[i + 1]); i += 2
        else:
            i += 1

    if k is None or tolerancia_atr is None or toques_min is None:
        print("Faltan parámetros: --k, --tolerancia-atr, --toques-min")
        sys.exit(1)

    try:
        prueba(coin, tf, k, tolerancia_atr, toques_min, confirmacion_velas)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
