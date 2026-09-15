"""``python -m svg_to_pptx`` — the SVG -> PPTX compiler entry point."""

from svg_to_pptx.console_encoding import configure_utf8_stdio

configure_utf8_stdio()

from svg_to_pptx.pptx_package.cli import main

raise SystemExit(main())
