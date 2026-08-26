# ================================================================================
# descargar_hist_bit_spot.py - HISTORIAL COMPLETO, MERCADO AL CONTADO (ETH/USDT)
#
# Baja el historial completo del SPOT desde el origen del contrato. Se corre
# dos o tres veces en la vida. La logica esta en descargar_hist_bit.py: aqui
# solo se fija el mercado.
#
#   python descargar_hist_bit_spot.py eth               # todos los TF
#   python descargar_hist_bit_spot.py eth 15m,1h,4h,1d  # sin 1m/3m/5m
#   python descargar_hist_bit_spot.py btc 1m            # solo el 1m de BTC
#   python descargar_hist_bit_spot.py eth 1h --rehacer  # borra el CSV y baja todo
#
# CSV: velas/[COIN]/bitget_[COIN]_[TF]_spot.csv
# ================================================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import descargar_hist_bit

if __name__ == "__main__":
    descargar_hist_bit.main('spot', script='descargar_hist_bit_spot.py')
