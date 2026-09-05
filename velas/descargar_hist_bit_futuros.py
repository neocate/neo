
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import descargar_hist_bit

if __name__ == "__main__":
    descargar_hist_bit.main('futuros', script='descargar_hist_bit_futuros.py')
