#!/usr/bin/env python3
"""Dev-time entry: standalone SVG lint.

Equivalent to the installed ``svg-lint`` console script:

    python3 svg_lint.py <project> --quick-generate --json
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from svg_to_pptx.console_encoding import configure_utf8_stdio

configure_utf8_stdio()

from svg_to_pptx.lint.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
