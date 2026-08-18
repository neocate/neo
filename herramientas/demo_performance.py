#!/usr/bin/env python3
"""Demo: compara rendimiento - recalc completo vs incremental."""
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from herramientas import niveles

def demo_rendimiento(coin, tf):
    print(f"\n=== DEMO: {coin.upper()} {tf} ===\n")

    config = niveles.cargar_config()

    # ==================== PRIMER BARRIDO (COMPLETO) ====================
    print("1️⃣  PRIMER BARRIDO (recalcula TODO):")
    t0 = time.time()
    try:
        niveles._actualizar(coin, tf, k=3, tolerancia_atr=0.25, toques_min=3)
    except FileNotFoundError as e:
        print(f"  Error: {e}")
        return
    t1 = time.time()
    print(f"  ⏱️  Tiempo: {t1 - t0:.2f}s\n")

    # ==================== SEGUNDO CICLO (INCREMENTAL) ====================
    print("2️⃣  SEGUNDO CICLO (solo velas nuevas - debería ser >10x más rápido):")
    t0 = time.time()
    try:
        niveles._actualizar(coin, tf, k=3, tolerancia_atr=0.25, toques_min=3)
    except Exception as e:
        print(f"  Error: {e}")
    t1 = time.time()
    print(f"  ⏱️  Tiempo: {t1 - t0:.2f}s\n")

    print("✅ Si ves diferencia >10x entre ciclos 1 y 2, la optimización funciona.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python demo_performance.py <coin> <tf>")
        print("Ejemplo: python demo_performance.py eth 1m")
        sys.exit(1)

    coin = sys.argv[1]
    tf = sys.argv[2]

    demo_rendimiento(coin, tf)
