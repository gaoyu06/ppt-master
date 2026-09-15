import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from console_encoding import configure_utf8_stdio

configure_utf8_stdio()

from svg_to_pptx.lint.cli import main

main()
