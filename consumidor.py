#!/usr/bin/env python3
"""
Consumidor de datos frescos: niveles + velas + libro.
Cruza indicadores y genera señales de confluencia.

Uso:
  python consumidor.py [coin] [tf_objetivo]
  python consumidor.py eth 1h
  python consumidor.py eth 1h --loop 60
"""

import json
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

DIR_BASE = Path(__file__).resolve().parent
DIR_VELAS = DIR_BASE / "velas"
DIR_NIVELES = DIR_BASE / "niveles"
DIR_LIBRO = DIR_BASE / "libro"


@dataclass
class Nivel:
    tipo: str
    precio: float
    toques: int
    estado: str
    dist_pct: float

    def es_valido(self, toques_min=5):
        return self.estado == "vivo" and self.toques >= toques_min


@dataclass
class Vela:
    ts: int
    open: float
    high: float
    low: float
    close: float
    vol: float

    def contiene(self, precio, tolerancia=0):
        return (self.low - tolerancia) <= precio <= (self.high + tolerancia)


def leer_niveles_json(coin, tf, k, toques):
    """Lee niveles frescos del JSON."""
    archivo = DIR_NIVELES / "json" / f"nivel_{coin.upper()}_{tf}_k{k}_toques{toques}.json"
    if not archivo.exists():
        return None, None, None
    try:
        with open(archivo, encoding='utf-8-sig') as f:
            data = json.load(f)
        return data.get("niveles", []), data.get("precio_actual"), data.get("timestamp")
    except Exception as e:
        print(f"ERROR leer niveles: {e}")
        return None, None, None


def leer_ultima_vela(coin, tf):
    """Lee última vela de archivo CSV."""
    archivo = DIR_VELAS / coin.upper() / f"bitget_{coin.upper()}_{tf}.csv"
    if not archivo.exists():
        return None
    try:
        with open(archivo, encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            vela = None
            for row in reader:
                vela = row
            if vela and len(vela) >= 7:
                return Vela(
                    ts=int(vela[0]),
                    open=float(vela[2]),
                    high=float(vela[3]),
                    low=float(vela[4]),
                    close=float(vela[5]),
                    vol=float(vela[6])
                )
    except Exception as e:
        print(f"ERROR leer velas: {e}")
    return None


def leer_ultimo_libro(coin):
    """Lee último registro del libro.csv."""
    dir_datos = DIR_LIBRO / "datos"
    if not dir_datos.exists():
        return None

    patron = f"libro_*_{coin.upper()}.csv"
    archivos = list(dir_datos.glob(patron))
    if not archivos:
        return None

    archivo = sorted(archivos, key=lambda x: x.stat().st_mtime, reverse=True)[0]
    try:
        with open(archivo, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            fila = None
            for fila in reader:
                pass
            if fila:
                return {
                    "timestamp_ms": int(fila.get("timestamp_local_ms", 0)),
                    "imbalance": float(fila.get("imbalance", 0)),
                    "cvd": float(fila.get("cvd", 0)),
                    "vol_buy": float(fila.get("vol_buy", 0)),
                    "vol_sell": float(fila.get("vol_sell", 0)),
                    "bids_json": fila.get("bids_json", "[]"),
                    "asks_json": fila.get("asks_json", "[]"),
                }
    except Exception as e:
        print(f"ERROR leer libro: {e}")
    return None


def filtrar_niveles(niveles, toques_min=5):
    """Filtra niveles válidos (vivos + suficientes toques)."""
    if not niveles:
        return []
    validos = []
    for n in niveles:
        niv = Nivel(
            tipo=n.get("tipo"),
            precio=n.get("precio"),
            toques=n.get("toques"),
            estado=n.get("estado"),
            dist_pct=n.get("dist_pct", 0)
        )
        if niv.es_valido(toques_min):
            validos.append(niv)
    return validos


def imbalance_extremo(libro, umbral=0.7):
    """Detecta imbalance extremo."""
    if not libro:
        return None
    imb = libro["imbalance"]
    if abs(imb) >= umbral:
        return "compra" if imb > 0 else "venta"
    return None


def nivel_cerca_precio(nivel, precio, tolerancia_pct=0.1):
    """Verifica si nivel está cerca del precio actual (confluencia)."""
    dist = abs(nivel.precio - precio) / precio * 100
    return dist <= tolerancia_pct, dist


def confluencia_vela_nivel(vela, nivel, tolerancia_atr=0.02):
    """Verifica si vela 15m confirma nivel técnico."""
    tol = nivel.precio * tolerancia_atr
    return vela.contiene(nivel.precio, tol)


def procesar(coin, tf_objetivo, k=5, toques_min=3, umbral_imbalance=0.7):
    """Procesa señales de confluencia (cruzar tf_objetivo con 15m)."""
    print(f"\n[{_fmt_hora()}] PROCESANDO {coin.upper()} {tf_objetivo}")

    # Leer datos
    niveles_raw, precio_actual, ts_niveles = leer_niveles_json(coin, tf_objetivo, k, toques_min)
    vela_tf = leer_ultima_vela(coin, tf_objetivo)
    vela_15m = leer_ultima_vela(coin, "15m")
    libro = leer_ultimo_libro(coin)

    if not all([niveles_raw, precio_actual, vela_tf, vela_15m, libro]):
        print("  [ERROR] Datos incompletos")
        return

    # Filtrar ruido
    niveles_vivos = filtrar_niveles(niveles_raw, toques_min=5)
    if not niveles_vivos:
        print(f"  Sin niveles válidos (filtro: estado=vivo, toques≥5)")
        return

    print(f"  Precio: {precio_actual:.2f} | Niveles válidos: {len(niveles_vivos)}")

    # Detectar setup
    imb_tipo = imbalance_extremo(libro, umbral_imbalance)
    if not imb_tipo:
        print(f"  Imbalance normal ({libro['imbalance']:+.3f}, umbral: ±{umbral_imbalance})")
        return

    print(f"  >>> IMBALANCE EXTREMO: {imb_tipo.upper()} ({libro['imbalance']:+.3f})")

    # Buscar confluencias
    señales = []
    for niv in niveles_vivos[:10]:  # Top 10 por validez
        cerca, dist = nivel_cerca_precio(niv, precio_actual, tolerancia_pct=0.5)
        if not cerca:
            continue

        confirma_tf = confluencia_vela_nivel(vela_tf, niv)
        confirma_15m = confluencia_vela_nivel(vela_15m, niv)
        cvd_estado = "div" if abs(libro["cvd"]) > 5000 else "ok"

        señal = {
            "nivel_tipo": niv.tipo,
            "nivel_precio": niv.precio,
            "toques": niv.toques,
            "dist_pct": dist,
            "imbalance": libro["imbalance"],
            "imbalance_tipo": imb_tipo,
            "vela_tf_confirma": confirma_tf,
            "vela_15m_confirma": confirma_15m,
            "cvd": libro["cvd"],
            "cvd_estado": cvd_estado,
            "confianza": "ALTA" if (confirma_tf and confirma_15m and cvd_estado == "ok") else "MEDIA" if (confirma_tf or confirma_15m) else "BAJA",
        }

        if confirma_tf or confirma_15m:
            señales.append(señal)
            print(f"  SEÑAL {niv.tipo.upper()} @ {niv.precio:.2f}")
            print(f"    - Dist: {dist:.3f}%, Toques: {niv.toques}")
            print(f"    - Vela {tf_objetivo}: {'DENTRO' if confirma_tf else 'FUERA'} | Vela 15m: {'DENTRO' if confirma_15m else 'FUERA'}")
            print(f"    - CVD: {libro['cvd']:+.0f} ({cvd_estado}), Confianza: {señal['confianza']}")

    if not señales:
        print(f"  Sin confluencias (imbalance {imb_tipo}, pero ni {tf_objetivo} ni 15m confirman)")

    return señales


def _fmt_hora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def main():
    args = sys.argv[1:]
    coin = args[0] if len(args) > 0 else "eth"
    tf = args[1] if len(args) > 1 else "1h"

    intervalo = None
    i = 2
    while i < len(args):
        if args[i] == "--loop":
            intervalo = int(args[i+1])
            i += 2
        else:
            i += 1

    if intervalo:
        print(f"Loop cada {intervalo}s. Ctrl+C para parar.")
        while True:
            try:
                procesar(coin, tf)
                time.sleep(intervalo)
            except KeyboardInterrupt:
                print("\nParado.")
                break
            except Exception as e:
                print(f"ERROR: {e}")
                time.sleep(intervalo)
    else:
        procesar(coin, tf)


if __name__ == "__main__":
    main()
