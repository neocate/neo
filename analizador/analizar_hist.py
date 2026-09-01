#!/usr/bin/env python3
"""
analizar_hist.py - WRAPPER COMPATIBLE (anacrónico, usa analyzer.py internamente)

Para análisis histórico, usa en su lugar:
  python analyzer.py --tf 5m --replay-desde 2026-08-26 --replay-hasta 2026-08-27 --intervalo 15

Este archivo se mantiene por compatibilidad, pero es un thin wrapper.
"""

import sys
import logging
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from analyzer import main as analyzer_main

logging.warning("[AVISO] analizar_hist.py es anacronico. Usa: analyzer.py --replay-desde/hasta")

if __name__ == "__main__":
    sys.argv[0] = "analyzer.py"
    try:
        analyzer_main()
    except SystemExit:
        raise
