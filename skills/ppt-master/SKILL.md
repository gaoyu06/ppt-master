---
name: ppt-master
description: >
  Compile project-normalized SVG pages into a native, editable PPTX:
  DrawingML shapes and connectors, native charts/tables, OMML formulas,
  transitions and object animations, speaker notes, and narration audio.
  Also validates the authoring SVG dialect and imports PPTX back to SVG
  for round-trip editing. Use when the user asks to turn SVG slide designs
  into a .pptx, check slide SVGs, or edit an existing PPTX.
metadata:
  version: "6.4.0"
  copyright: "Copyright (c) 2025-2026 Hugo He"
  license: "MIT"
  official_repository: "https://github.com/hugohe3/ppt-master"
  sponsors:
    - "SPONSORS.md"
    - "SPONSORS_CN.md"
---

# PPT Master — SVG → PPTX core

Slimmed fork: the upstream routed workflow, design references, and preset
libraries were removed. This skill is the converter toolchain only — the
agent authors `svg_output/*.svg` directly, then exports.

## Pipeline

1. Author one SVG per slide under `<project>/svg_output/`. The root
   `viewBox="0 0 W H"` is the canvas (`ppt169` = `0 0 1280 720`).
2. Validate:

   ```bash
   python3 "${SKILL_DIR}/scripts/svg_quality_checker.py" <project> \
     --quick-generate --canonical-authoring --stage final --json
   ```

3. Export:

   ```bash
   python3 "${SKILL_DIR}/scripts/svg_to_pptx.py" <project> --quick-generate
   ```

Useful options: `-t <transition>` (48 native effects incl. `morph`),
`-a <entrance_*/emphasis_*/path_*/exit_*>` object animations,
`--animation-config animations.json` for per-object sequences and
`trigger_shape` click triggers, `--native-charts-and-tables`,
`--recorded-narration <audio_dir>`, `--roundtrip` for PPTX→SVG→PPTX editing.

The SVG dialect is closed: unsupported elements/attributes fail validation
instead of silently degrading. `templates/charts|tables` hold the runtime
visualization SVG vocabulary; `templates/schemas|scaffolds` back the
structured-template path.

## Native extension markers

- `<filter id="fx" data-pptx-effect="shadow|glow|inner-shadow|reflection|soft-edge|blur">`
  selects the native `a:effectLst` effect explicitly.
- `<text>` accepts `data-pptx-vert` (`eaVert` vertical CJK etc.),
  `data-pptx-anchor` (`t|ctr|b|just|dist`), `data-pptx-autofit`
  (`none|norm|shape`) → `a:bodyPr`.
- `animations.json` group effect `"path_custom"` takes
  `effect_options.path` — an OOXML motion path in slide fractions
  (`"M 0 0 L 0.25 0.1"`, optional `relative` boolean) → `p:animMotion`.
- `animations.json` group field `"by_paragraph": true` expands the effect
  into one row per `a:p` of the first text-bearing shape in the group
  (`p:txEl`/`p:pRg` + `p:bldP build="p"` per-paragraph builds).
  UNVERIFIED: structurally valid but does not play on PowerPoint for Mac.

