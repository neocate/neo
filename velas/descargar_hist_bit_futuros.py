# ================================================================================
# descargar_hist_bit_futuros.py - HISTORIAL COMPLETO, PERPETUO USDT-M
#                                 (ETH/USDT:USDT)
#
# Baja el historial completo del PERPETUO desde el origen del contrato. Se
# corre dos o tres veces en la vida. La logica esta en descargar_hist_bit.py:
# aqui solo se fija el mercado.
#
#   python descargar_hist_bit_futuros.py eth               # todos los TF
#   python descargar_hist_bit_futuros.py eth 15m,1h,4h,1d  # sin 1m/3m/5m
#   python descargar_hist_bit_futuros.py btc 1m            # solo el 1m de BTC
#   python descargar_hist_bit_futuros.py eth 1h --rehacer  # borra y baja todo
#
# CSV: velas/[COIN]/bitget_[COIN]_[TF]_futuros.csv
# ================================================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import descargar_hist_bit

if __name__ == "__main__":
    descargar_hist_bit.main('futuros', script='descargar_hist_bit_futuros.py')
