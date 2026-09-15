#!/usr/bin/env python3
"""Dev-time entry: SVG -> PPTX compiler.

Equivalent to the installed ``svg-to-pptx`` console script:

    python3 svg_to_pptx.py <project> -o out.pptx
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from svg_to_pptx.console_encoding import configure_utf8_stdio
from svg_to_pptx import main

configure_utf8_stdio()

if __name__ == '__main__':
    raise SystemExit(main())
