# PPT Master scripts (slimmed)

Core toolchain for the SVG → PPTX pipeline:

- `svg_to_pptx.py` — export `<project>/svg_output/*.svg` to a native PPTX
- `svg_quality_checker.py` — validate the authoring SVG dialect
- `pptx_to_svg.py` — import a PPTX into authoring SVG (round-trip)
- `finalize_svg.py` — derive the `svg_final/` preview set

Packages: `svg_to_pptx/` (compiler), `pptx_to_svg/` (importer),
`svg_quality/` (checker), `svg_finalize/`, `pptx_shapes/` (preset registry),
`pptx_ooxml/` (OPC package primitives), `project_management/`,
`template_import/`, `tests/`.
