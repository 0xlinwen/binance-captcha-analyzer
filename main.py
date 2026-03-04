#!/usr/bin/env python3
"""Compatibility entrypoint.

Use `python captcha_analyzer.py` as before; implementation lives in src/binance_analyzer.
"""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from binance_analyzer.cli import main


if __name__ == "__main__":
    main()