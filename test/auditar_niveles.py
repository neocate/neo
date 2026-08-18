#!/usr/bin/env python3
"""
AUDITOR DE NIVELES - Valida JSONs de soporte/resistencia

=== EJEMPLOS DE USO ===

Windows/Linux/NAS - Auditar un coin/tf:
  cd D:\neocat\neo
  python test/auditar_niveles.py eth 4h
  python test/auditar_niveles.py btc 1h

Windows/Linux/NAS - Auditar todos los coins (mismo TF):
  python test/auditar_niveles.py * 4h
  python test/auditar_niveles.py * 1h

=== QUÉ VALIDA ===
✓ Estructura JSON válida
✓ Campos requeridos (params, atr_ref, tolerancia, niveles)
✓ Estados válidos (vivo, roto, flip)
✓ Tipos válidos (techo, suelo)
✓ Coherencia de precios (techos > suelos)
✓ Toques lógicos (no negativos)
✓ Timestamps válidos
✓ Distribución de toques (min/mediana/max)
✓ Antigüedad de niveles

=== ESTRUCTURA DE DATOS ===
Se espera: test/niveles/[COIN]/listado_[TF].json
Ejemplo: test/niveles/ETH/listado_4h.json
"""

import json
import os
import sys
from pathlib import Path

DIR_NIVELES = Path(__file__).parent / "niveles"


def auditar_json(coin, tf):
    """Audita un JSON de niveles."""
    ruta = DIR_NIVELES / coin.upper() / f"listado_{tf}.json"

    if not ruta.exists():
        print(f"❌ No existe: {ruta}")
        return False

    print(f"\n{'='*60}")
    print(f"AUDITORÍA: {coin.upper()} {tf}")
    print(f"{'='*60}\n")

    try:
        with open(ruta) as f:
            datos = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ JSON inválido: {e}")
        return False

    # Validar estructura
    campos_req = ["params", "atr_ref", "tolerancia", "ultimo_ts_procesado", "niveles"]
    for campo in campos_req:
        if campo not in datos:
            print(f"❌ Falta campo: {campo}")
            return False

    print("✓ Estructura JSON válida\n")

    # Estadísticas
    niveles = datos["niveles"]
    print(f"Total niveles: {len(niveles)}")

    vivos = [n for n in niveles if n["estado"] == "vivo"]
    rotos = [n for n in niveles if n["estado"] == "roto"]
    flips = [n for n in niveles if n["estado"] == "flip"]

    print(f"  Vivos: {len(vivos)}")
    print(f"  Rotos: {len(rotos)}")
    print(f"  Flips: {len(flips)}")
    print()

    # Validar niveles
    errores = []

    techos = [n for n in niveles if n["tipo"] == "techo"]
    suelos = [n for n in niveles if n["tipo"] == "suelo"]

    print(f"Techos: {len(techos)}")
    print(f"Suelos: {len(suelos)}")
    print()

    # Validar estados coherentes
    for niv in niveles:
        if niv["estado"] not in ("vivo", "roto", "flip"):
            errores.append(f"Estado inválido: {niv['precio']} = {niv['estado']}")

        if niv["tipo"] not in ("techo", "suelo"):
            errores.append(f"Tipo inválido: {niv['precio']} = {niv['tipo']}")

        if niv["toques"] < 0:
            errores.append(f"Toques negativo: {niv['precio']}")

        if niv["primero"] is None or niv["ultimo"] is None:
            if niv["estado"] != "roto":  # rotos pueden no tener último
                errores.append(f"Timestamps nulos: {niv['precio']}")

    # Validar coherencia de precios
    precios = [n["precio"] for n in niveles]
    if len(precios) != len(set(precios)):
        errores.append("Precios duplicados detectados")

    # Techos deben estar arriba, suelos abajo
    if techos and suelos:
        techo_min = min(n["precio"] for n in techos)
        suelo_max = max(n["precio"] for n in suelos)
        if techo_min < suelo_max:
            errores.append(f"Techo ({techo_min}) por debajo de suelo ({suelo_max})")

    # Mostrar errores
    if errores:
        print("❌ ERRORES ENCONTRADOS:\n")
        for err in errores:
            print(f"  • {err}")
        print()
        return False

    print("✓ Niveles válidos (sin conflictos)\n")

    # Resumen de parámetros
    print("PARÁMETROS:")
    for k, v in datos["params"].items():
        print(f"  {k}: {v}")
    print()
    print(f"ATR ref: {datos['atr_ref']:.4f}")
    print(f"Tolerancia: {datos['tolerancia']:.4f}")
    print()

    # Validar distribución
    print("DISTRIBUCIÓN DE TOQUES:")
    toques_list = sorted([n["toques"] for n in niveles])
    print(f"  Min: {toques_list[0]:.1f}")
    print(f"  Mediana: {toques_list[len(toques_list)//2]:.1f}")
    print(f"  Max: {toques_list[-1]:.1f}")
    print()

    # Validar antigüedad
    antiguedades = [n.get("antig_dias", 0) for n in niveles if "antig_dias" in n]
    if antiguedades:
        print("ANTIGÜEDAD DE NIVELES (días desde último toque):")
        print(f"  Más reciente: {min(antiguedades):.1f}d")
        print(f"  Más antiguo: {max(antiguedades):.1f}d")

    print("\n✅ AUDITORÍA EXITOSA\n")
    return True


def main():
    if len(sys.argv) < 3:
        print("Uso: python auditar_niveles.py <coin> <tf>")
        print("Ejemplo: python auditar_niveles.py eth 4h")
        print("\nAudita todos los coins:")
        print("Ejemplo: python auditar_niveles.py * 4h")
        return

    coin = sys.argv[1]
    tf = sys.argv[2]

    if coin == "*":
        # Audita todos los coins
        coins = set()
        for carpeta in DIR_NIVELES.iterdir():
            if carpeta.is_dir():
                coins.add(carpeta.name.lower())

        if not coins:
            print("No hay carpetas de niveles")
            return

        for c in sorted(coins):
            auditar_json(c, tf)
    else:
        auditar_json(coin, tf)


if __name__ == "__main__":
    main()
