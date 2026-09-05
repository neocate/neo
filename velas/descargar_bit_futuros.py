
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import descargar_bit

if __name__ == "__main__":
    descargar_bit.main('futuros', script='descargar_bit_futuros.py')
