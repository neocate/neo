# ================================================================================
# descargar_bit_futuros.py - PRODUCCION, PERPETUO USDT-M (ETH/USDT:USDT)
#
# Mantiene al dia los CSV de velas cerradas del PERPETUO. La logica esta en
# descargar_bit.py y la maquinaria en velas_bit.py: aqui solo se fija el
# mercado, que es lo unico que no se puede deducir ni adivinar.
#
#   python descargar_bit_futuros.py eth                # todos los TF, sale al dia
#   python descargar_bit_futuros.py eth 5m,15m,1h      # solo esos TF
#   python descargar_bit_futuros.py eth --loop         # daemon, todos los TF
#   python descargar_bit_futuros.py btc 4h,1d --loop   # daemon, solo 4h y 1d
#
# CSV: velas/[COIN]/bitget_[COIN]_[TF]_futuros.csv
# ================================================================================

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import descargar_bit

if __name__ == "__main__":
    descargar_bit.main('futuros', script='descargar_bit_futuros.py')
