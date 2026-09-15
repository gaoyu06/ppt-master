# pptx-svg — an SVG superset that compiles to editable PPTX

Fork of [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master), rebuilt as
a compiler: **project-normalized SVG + `pptx:` namespace extensions → native,
editable PPTX**. The language is specified in `SPEC.md`.

The upstream prompt workflows, design references, style presets, icon/sound
libraries, source-document converters, image/TTS backends, and preview UIs were
removed. What remains:

- `svg_to_pptx.py` — compile `svg_output/*.svg` into a native PPTX
  (DrawingML shapes, charts, tables, OMML formulas, transitions,
  object animations, speaker notes)
- `svg_lint.py` — run the compiler's advisory SVG lint pass
  (also runs automatically at export)
- `pptx_to_svg.py` — import an existing PPTX back into the authoring SVG
  form (round-trip editing)

## Usage

```bash
pip install -r requirements.txt

# project layout: <project>/svg_output/*.svg
python3 svg_lint.py <project> \
  --quick-generate --canonical-authoring --stage final --json
python3 svg_to_pptx.py <project> --quick-generate
```

Run `svg_to_pptx.py --help` for transitions (`-t`), object animations (`-a`),
native charts/tables, and round-trip options.

Upstream: MIT license, © 2025-2026 Hugo He (LICENSE, SPONSORS.md,
SPONSORS_CN.md).
