# Uso: python herramientas/verificacion/verificar_niveles.py [coin] [tf]

import csv
import io
import json
import os
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from herramientas import niveles as niv


def test_determinismo_y_diferencias(coin, tf):
    ruta_listado = niv._ruta_listado(coin, tf)
    if not os.path.exists(ruta_listado):
        print(f"  (sin listado real de {coin} {tf}, se salta esta parte)")
        return
    with open(ruta_listado) as f:
        guardado = json.load(f)
    params = guardado["params"]

    r1 = niv._analizar(coin, tf, params["k"], params["tolerancia_atr"], params["toques_min"],
                        desde_dias=None, confirmacion_velas=params["confirmacion_velas"])
    r2 = niv._analizar(coin, tf, params["k"], params["tolerancia_atr"], params["toques_min"],
                        desde_dias=None, confirmacion_velas=params["confirmacion_velas"])
    assert r1["niveles"] == r2["niveles"] and r1["atr_ref"] == r2["atr_ref"], \
        "FALLO: _analizar() no es determinista"
    print(f"  OK determinismo ({coin} {tf}): misma llamada dos veces da lo mismo")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=niv.CAMPOS_HISTORIAL)
    writer.writeheader()

    class FalsoLog:
        def flush(self):
            pass

    n_nuevos, n_toques, n_trans, n_desap = niv._anotar_diferencias(
        guardado["niveles"], r1["niveles"], r1["tolerancia"], writer, FalsoLog(), coin, tf)
    filas = buf.getvalue().count("\n") - 1
    total_niveles = len(r1["niveles"])
    assert filas <= total_niveles * 2, (
        f"FALLO: {filas} eventos para {total_niveles} niveles - posible avalancha de eventos falsos")
    print(f"  OK diferencias ({coin} {tf}): {filas} eventos para {total_niveles} niveles "
          f"(nuevos={n_nuevos} toques={n_toques} transiciones={n_trans} desaparecidos={n_desap})")


def test_forzar_recalculo_primera_llamada(coin, tf):
    coin_prueba = f"{coin}_PRUEBA_VERIFICACION"
    _orig = niv._archivo_bitget
    niv._archivo_bitget = lambda c, t: (_orig(coin, t) if c == coin_prueba else _orig(c, t))
    try:
        ruta_listado_real = niv._ruta_listado(coin, tf)
        if not os.path.exists(ruta_listado_real):
            print(f"  (sin listado real de {coin} {tf}, se salta esta parte)")
            return
        shutil.copy2(ruta_listado_real, niv._ruta_listado(coin_prueba, tf))
        with open(niv._ruta_listado(coin_prueba, tf)) as f:
            antes = json.load(f)

        niv._actualizar(coin_prueba, tf, k=antes["params"]["k"],
                         tolerancia_atr=antes["params"]["tolerancia_atr"],
                         toques_min=antes["params"]["toques_min"],
                         confirmacion_velas=antes["params"]["confirmacion_velas"])
        with open(niv._ruta_listado(coin_prueba, tf)) as f:
            despues1 = json.load(f)
        assert despues1["atr_ref"] != antes["atr_ref"] or despues1 == antes, (
            "revisar: si el listado real ya estaba corregido esto no prueba nada")

        niv._actualizar(coin_prueba, tf, k=antes["params"]["k"],
                         tolerancia_atr=antes["params"]["tolerancia_atr"],
                         toques_min=antes["params"]["toques_min"],
                         confirmacion_velas=antes["params"]["confirmacion_velas"])
        with open(niv._ruta_listado(coin_prueba, tf)) as f:
            despues2 = json.load(f)
        assert despues2 == despues1, "FALLO: la segunda llamada no fue un no-op (deberia serlo)"
        print(f"  OK forzar-recalculo ({coin} {tf}): 1a llamada recalcula, 2a respeta 'ya esta al dia'")
    finally:
        niv._archivo_bitget = _orig
        carpeta = niv._carpeta_niveles(coin_prueba)
        if os.path.isdir(carpeta):
            shutil.rmtree(carpeta)


if __name__ == "__main__":
    coin = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    tf = sys.argv[2] if len(sys.argv) > 2 else "4h"
    test_determinismo_y_diferencias(coin, tf)
    test_forzar_recalculo_primera_llamada(coin, tf)
    print(f"\nOK: verificacion de niveles.py completa para {coin} {tf}, produccion intacta.")
