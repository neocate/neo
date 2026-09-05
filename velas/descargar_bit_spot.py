
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import descargar_bit

if __name__ == "__main__":
    descargar_bit.main('spot', script='descargar_bit_spot.py')
