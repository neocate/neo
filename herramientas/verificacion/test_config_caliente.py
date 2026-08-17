# ---------------------------------------------------------------
# test_config_caliente.py - Verifica el patron de config en caliente
# (PARAMS_DEFECTO/cargar_params()/guardar_params()) en los tres modulos que
# lo implementan: mercado.indicadores, mercado.senales, herramientas.niveles.
#
# Comprueba, para cada uno:
#   1. Sin *_config.json: el comportamiento es IDENTICO al de siempre
#      (mismos valores que pasando el parametro explicito a mano).
#   2. Con *_config.json: cambiar un valor ahi SI cambia el resultado
#      (hot-reload de verdad, no solo un default que nunca se lee).
#   3. guardar_params() rechaza valores fuera de LIMITES_PARAMS.
# Limpia cualquier *_config.json de prueba al terminar - deja los tres
# modulos exactamente como estaban (sin JSON) al acabar.
#
# Uso: python herramientas/verificacion/test_config_caliente.py
# ---------------------------------------------------------------
import csv
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from mercado import indicadores as ind
from mercado import senales as sen
from herramientas import niveles as niv


def _cargar_velas(coin, tf):
    ruta = os.path.join(RAIZ, "herramientas", "velas", coin, f"{tf}_bitget.csv")
    velas = []
    with open(ruta, newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 7:
                continue
            velas.append([int(row[0]), float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])])
    return velas


def _limpiar(modulo):
    ruta = modulo._ruta_config()
    if os.path.exists(ruta):
        os.remove(ruta)
    modulo._cache_params = None
    modulo._cache_mtime = None
    modulo._cache_verificado = 0.0


def _forzar_relectura(modulo):
    """cargar_params() tiene un margen de INTERVALO_RECOMPROBAR segundos
    antes de volver a mirar el mtime en disco (ver su comentario en cada
    modulo - evita golpear la red del NAS en bucles ajustados). Para un
    test que escribe el JSON y quiere ver el efecto YA, hay que resetear
    el reloj de la cache a mano en vez de esperar el margen real."""
    modulo._cache_verificado = 0.0


def test_indicadores(velas):
    _limpiar(ind)
    cierres = [v[4] for v in velas]
    assert ind.rsi(cierres) == ind.rsi(cierres, 14), "indicadores: default != explicito sin json"
    ind.guardar_params({"rsi_periodo": 21})
    _forzar_relectura(ind)
    assert ind.rsi(cierres) == ind.rsi(cierres, 21), "indicadores: el json no cambio el resultado"
    try:
        ind.guardar_params({"rsi_periodo": 5000})
        raise AssertionError("indicadores: deberia rechazar rsi_periodo=5000")
    except ValueError:
        pass
    _limpiar(ind)
    print("OK indicadores.py: default sin json, hot-reload con json, limites respetados")


def test_senales(velas):
    _limpiar(sen)
    assert sen.detectar(velas, k=3) == sen.detectar(velas, k=3, ventana_ruptura=30), \
        "senales: default != explicito sin json"
    cierres = [v[4] for v in velas]
    atr_actual = 100.0  # valor de prueba, no importa la escala real aqui
    assert sen._impulso(cierres, atr_actual) == sen._impulso(cierres, atr_actual, umbral=2.5), \
        "senales: default de _impulso != explicito sin json"
    sen.guardar_params({"umbral_impulso_atr": 0.01})
    _forzar_relectura(sen)
    assert sen._impulso(cierres, atr_actual) == sen._impulso(cierres, atr_actual, umbral=0.01), \
        "senales: el json no cambio el resultado de _impulso"
    try:
        sen.guardar_params({"rsi_sobrecompra": 200})
        raise AssertionError("senales: deberia rechazar rsi_sobrecompra=200")
    except ValueError:
        pass
    _limpiar(sen)
    print("OK senales.py: default sin json, hot-reload con json, limites respetados")


def test_niveles():
    _limpiar(niv)
    p = niv.cargar_params()
    assert p == niv.PARAMS_DEFECTO, "niveles: default sin json deberia ser PARAMS_DEFECTO"
    niv.guardar_params({"k": 5})
    _forzar_relectura(niv)
    assert niv.cargar_params()["k"] == 5, "niveles: el json no cambio el resultado"
    try:
        niv.guardar_params({"toques_min": 99999})
        raise AssertionError("niveles: deberia rechazar toques_min=99999")
    except ValueError:
        pass
    _limpiar(niv)
    print("OK niveles.py: default sin json, hot-reload con json, limites respetados")


if __name__ == "__main__":
    velas = _cargar_velas("ETH", "1h")
    test_indicadores(velas)
    test_senales(velas)
    test_niveles()
    print("\nOK: los tres modulos quedan sin *_config.json al terminar (estado limpio).")
