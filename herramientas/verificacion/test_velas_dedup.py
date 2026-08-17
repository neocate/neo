# ---------------------------------------------------------------
# test_velas_dedup.py - Verifica el arreglo del 2026-08-17 en
# descargar_bit.py (_reemplazar_ultima_fila/_anexar_nuevas): cuando
# Bitget devuelve otra vez, actualizada, la vela que se habia guardado
# como "ultima cerrada" (seguia en curso), se sobreescribe la ultima fila
# en vez de duplicarla. Trabaja sobre un CSV temporal, no toca nada real.
#
# Uso: python herramientas/verificacion/test_velas_dedup.py
# ---------------------------------------------------------------
import csv
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)
from herramientas import descargar_bit as db


def main():
    fd, ruta = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(ruta, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "fecha_utc", "open", "high", "low", "close", "volumen"])
            db._escribir_filas(f, [(1000, 1, 2, 0.5, 1.5, 10), (2000, 1.5, 2.5, 1, 2, 20)])

        # caso 1: la primera 'nueva' coincide con la ultima guardada (2000)
        # pero con OHLCV actualizado (seguia en curso) + una vela nueva de verdad
        nuevas = [(2000, 1.5, 2.6, 1, 2.1, 25), (3000, 2.1, 2.3, 2.0, 2.2, 5)]
        restantes, corregida = db._anexar_nuevas(ruta, nuevas, ultimo_ts=2000)
        assert corregida is True
        assert restantes == [(3000, 2.1, 2.3, 2.0, 2.2, 5)]

        # caso 2: sin solapamiento, append normal
        restantes2, corregida2 = db._anexar_nuevas(ruta, [(4000, 2.2, 2.4, 2.1, 2.3, 7)], ultimo_ts=3000)
        assert corregida2 is False
        assert restantes2 == [(4000, 2.2, 2.4, 2.1, 2.3, 7)]

        import pandas as pd
        df = pd.read_csv(ruta)
        assert df["timestamp"].is_unique, "FALLO: hay timestamps duplicados"
        assert list(df["timestamp"]) == [1000, 2000, 3000, 4000], df["timestamp"].tolist()
        assert df.loc[df.timestamp == 2000, "close"].iloc[0] == 2.1, "FALLO: no se corrigio el close de 2000"
        print("OK: _anexar_nuevas()/_reemplazar_ultima_fila() sobreescriben en vez de duplicar, "
              "y el append normal sigue funcionando.")
    finally:
        os.remove(ruta)


if __name__ == "__main__":
    main()
