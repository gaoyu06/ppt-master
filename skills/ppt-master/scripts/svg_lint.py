#!/usr/bin/env python3
"""PPT Master - SVG lint entry point (thin wrapper).

Runs the compiler's advisory lint pass over project SVG pages:

    python3 scripts/svg_lint.py <project> --canonical-authoring --stage final
    python3 scripts/svg_lint.py <project> --quick-generate --stage final --json
    python3 scripts/svg_lint.py <roundtrip_workspace> --roundtrip
    python3 scripts/svg_lint.py <workspace>/templates --template-mode
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from console_encoding import configure_utf8_stdio

configure_utf8_stdio()

from svg_to_pptx.lint.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
