# ---------------------------------------------------------------
# holdout.py - Bloque de datos CONGELADO, reservado para la prueba final.
#
# CONGELADO EL 2026-09-03. Corte: 2025-01-01.
#
#   desarrollo: todo lo anterior al corte   -> 136 ventanas de 60 dias
#   holdout:    2025-01-01 en adelante      ->  36 ventanas (21%)
#
# ============================ LA REGLA ============================
#
#   Sobre 'desarrollo' se puede probar, ajustar y descartar cuanto haga
#   falta. Sobre 'holdout' se evalua UNA SOLA VEZ, cuando ya hay un
#   candidato cerrado, y el resultado se acepta tal cual.
#
#   Si el holdout sale mal, el candidato se descarta. NO se reajusta y se
#   vuelve a probar: eso convierte el holdout en datos de desarrollo y
#   destruye la unica garantia que teniamos.
#
# ==================== POR QUE, ESCRITO AHORA ======================
#
# Esto se congela ANTES de saber el resultado, para que la justificacion no
# se pueda escribir a posteriori. En la sesion del 2026-09-03 pasaron tres
# cosas que lo hacen necesario:
#
#  1. Dos sesgos de lookahead distintos, y los dos daban resultados
#     espectaculares. El primero en el log en vivo del analyzer (timestamp
#     de reloj con precio de vela cerrada). El segundo en el regimen diario:
#     una barra de las 09:15 usaba el cierre de ESE dia, hasta 15 horas en
#     el futuro. Al corregirlo, la media cayo de +14,3% a +5,5% y las
#     ventanas positivas del 97% al 72%.
#
#  2. La salida por niveles parecia funcionar en ETH (+39,2%) y en BTC
#     (+14,8%). Con ICP aparecieron 3 liquidaciones y el peor caso se fue a
#     -100%. Dos activos no bastaron para verlo.
#
#  3. Se probaron muchas configuraciones sobre los mismos 176 periodos. Cada
#     prueba adicional sube la probabilidad de encontrar ruido que parezca
#     senal.
#
# El patron comun: cuando un backtest sale muy bien, el primer sospechoso es
# el metodo, no el mercado. El holdout es la unica defensa que no depende de
# acordarse de sospechar.
#
# ===================== ESTADO DEL HOLDOUT ========================
#
# Cada evaluacion contra el holdout se registra en holdout_evaluaciones.log
# con fecha y descripcion. Si ese fichero tiene mas de una linea por
# candidato, el holdout ya esta contaminado y hay que reservar uno nuevo con
# datos posteriores.
#
# ---------------------------------------------------------------

import os
from datetime import datetime, timezone

CORTE_MS = 1735689600000          # 2025-01-01 00:00 UTC
CORTE_TXT = "2025-01-01"
CONGELADO_EL = "2026-09-03"

DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRO = os.path.join(DIR, "holdout_evaluaciones.log")


def es_desarrollo(ts_ms):
    """True para las marcas anteriores al corte. Acepta escalar o array."""
    try:
        return ts_ms < CORTE_MS
    except TypeError:
        return [t < CORTE_MS for t in ts_ms]


def ventanas_desarrollo(ts, largo):
    """Indices de arranque de ventanas que terminan ANTES del corte."""
    return [k for k in range(0, len(ts) - largo + 1, largo)
            if ts[k + largo - 1] < CORTE_MS]


def ventanas_holdout(ts, largo):
    """Indices de arranque de ventanas que empiezan EN o DESPUES del corte.

    Las ventanas que cruzan el corte se descartan a proposito: mezclarian
    datos de los dos bloques."""
    return [k for k in range(0, len(ts) - largo + 1, largo)
            if ts[k] >= CORTE_MS]


def registrar_evaluacion(candidato, resultado):
    """Deja constancia de cada uso del holdout. Llamar SIEMPRE que se evalue.

    No impide evaluar dos veces -- eso no se puede forzar desde el codigo --
    pero deja el rastro para que se sepa si el holdout sigue limpio."""
    linea = "%s\t%s\t%s\n" % (
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        candidato, resultado)
    with open(REGISTRO, "a", encoding="utf-8") as f:
        f.write(linea)
    return linea


def usos_previos():
    """Cuantas veces se ha evaluado ya el holdout."""
    if not os.path.exists(REGISTRO):
        return 0
    with open(REGISTRO, encoding="utf-8") as f:
        return sum(1 for l in f if l.strip() and not l.startswith("#"))


if __name__ == "__main__":
    print("Holdout congelado el %s" % CONGELADO_EL)
    print("  corte: %s (%d)" % (CORTE_TXT, CORTE_MS))
    print("  desarrollo: 136 ventanas de 60 dias | holdout: 36 (21%)")
    n = usos_previos()
    print("  evaluaciones registradas: %d %s"
          % (n, "-> LIMPIO" if n == 0 else "-> revisar holdout_evaluaciones.log"))
