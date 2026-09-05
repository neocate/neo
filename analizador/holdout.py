
import os
from datetime import datetime, timezone

CORTE_MS = 1735689600000
CORTE_TXT = "2025-01-01"
CONGELADO_EL = "2026-09-03"

DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRO = os.path.join(DIR, "holdout_evaluaciones.log")


def es_desarrollo(ts_ms):
    try:
        return ts_ms < CORTE_MS
    except TypeError:
        return [t < CORTE_MS for t in ts_ms]


def ventanas_desarrollo(ts, largo):
    return [k for k in range(0, len(ts) - largo + 1, largo)
            if ts[k + largo - 1] < CORTE_MS]


def ventanas_holdout(ts, largo):
    return [k for k in range(0, len(ts) - largo + 1, largo)
            if ts[k] >= CORTE_MS]


def registrar_evaluacion(candidato, resultado):
    linea = "%s\t%s\t%s\n" % (
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        candidato, resultado)
    with open(REGISTRO, "a", encoding="utf-8") as f:
        f.write(linea)
    return linea


def usos_previos():
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
