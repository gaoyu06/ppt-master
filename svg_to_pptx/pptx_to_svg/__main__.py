"""``python -m svg_to_pptx.pptx_to_svg`` entry point."""

from svg_to_pptx.console_encoding import configure_utf8_stdio

configure_utf8_stdio()

from svg_to_pptx.pptx_to_svg.cli import main

main()
