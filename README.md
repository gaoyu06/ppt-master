# PPT Master (slimmed fork)

Fork of [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master), stripped to
the core compiler: **project-normalized SVG → native, editable PPTX**.

The upstream prompt workflows, design references, style presets, icon/sound
libraries, source-document converters, image/TTS backends, and preview UIs were
removed. What remains:

- `skills/ppt-master/scripts/svg_to_pptx.py` — compile `svg_output/*.svg` into
  a native PPTX (DrawingML shapes, charts, tables, OMML formulas, transitions,
  object animations, speaker notes, narration audio)
- `skills/ppt-master/scripts/svg_lint.py` — run the compiler's advisory SVG
  lint pass (also runs automatically at export)
- `skills/ppt-master/scripts/pptx_to_svg.py` — import an existing PPTX back
  into the authoring SVG form (round-trip editing)

## Usage

```bash
pip install -r requirements.txt

# project layout: <project>/svg_output/*.svg
python3 skills/ppt-master/scripts/svg_lint.py <project> \
  --quick-generate --canonical-authoring --stage final --json
python3 skills/ppt-master/scripts/svg_to_pptx.py <project> --quick-generate
```

Run `svg_to_pptx.py --help` for transitions (`-t`), object animations (`-a`),
native charts/tables, narration, and round-trip options.

Upstream: MIT license, © 2025-2026 Hugo He. Attribution files are kept under
`skills/ppt-master/` (LICENSE, SPONSORS.md, SPONSORS_CN.md).
