#!/usr/bin/env python3
"""Dev-time entry: PPTX -> SVG semantic importer.

Equivalent to the installed ``pptx-to-svg`` console script:

    python3 pptx_to_svg.py <deck.pptx> [-o out_dir]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from svg_to_pptx.console_encoding import configure_utf8_stdio

configure_utf8_stdio()

from svg_to_pptx.pptx_to_svg.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
