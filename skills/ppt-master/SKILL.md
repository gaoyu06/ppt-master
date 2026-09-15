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
   python3 "${SKILL_DIR}/scripts/svg_lint.py" <project> \
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

## pptx: language (preferred authoring surface)

Declare `xmlns:pptx="http://pptx-svg.dev/ns/1"` on the root. These are
compile-time checked by the exporter — wrong values fail with a precise
error, they do not silently degrade.

```xml
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:pptx="http://pptx-svg.dev/ns/1"
     viewBox="0 0 1280 720" lang="zh-CN">
  <pptx:transition effect="fade" dur="0.4" advance="5"/>
  <pptx:notes>Speaker notes for this page.</pptx:notes>

  <g id="bullets" pptx:build="paragraph">
    <text x="120" y="340" font-size="28" pptx:line-height="44">…</text>
    <pptx:anim effect="wipe" start="click" dur="0.5" dir="right"/>
  </g>

  <g id="dot">
    <circle cx="1000" cy="400" r="40"
            pptx:effect="inner-shadow(blur=8,dist=4,dir=90,color=#00000066) soft-edge(rad=2)"/>
    <pptx:anim effect="path" path="M 0 0 L 0.2 0" start="after" dur="2"/>
    <pptx:anim effect="emphasis_grow_shrink" start="with" dur="0.8" size="1.1"/>
  </g>
</svg>
```

- `<pptx:anim>` — child of an animation anchor (top-level `<g>` with id).
  Document order = pane order. Attrs: `effect` (registry name or `path`
  for a custom `p:animMotion` via `path="…"` slide fractions +
  `relative="false"`), `start` (`click`/`with`/`after`), `dur`, `delay`,
  `on="<anchor id>"` (interactive click trigger), `repeat` (count or
  seconds), `autorev`/`rewind`, `accel`/`decel`/`bounce`, `restart`,
  `after` (`dim`/`hide`/`hide-on-next-click`/`color=#…`), `sound`, plus
  per-effect options `dir`/`amount`/`color`/`font`/`size`.
- `pptx:build="paragraph"` on the anchor — one animation step per `a:p`
  of the first text-bearing shape. UNVERIFIED on PowerPoint for Mac.
- `<pptx:transition>` — page transition; `effect` (48 names incl.
  `morph`), `dur`, `advance` (auto-advance seconds), `sound`, and
  per-effect options `dir`/`style`/`shape`/`origin`/`bounce`/`through-black`…
- `<pptx:notes>` — speaker notes (pass `--with-notes` under
  `--quick-generate`).
- `pptx:effect` — whitespace-separated `name(k=v,…)` calls, one
  `<a:effectLst>`; kinds `outer-shadow`/`inner-shadow`/`glow`/
  `reflection`/`soft-edge`/`blur` with `blur`/`dist`/`rad` px, `dir`
  degrees, `color` `#hex[a]`, `alpha` `0–1` or percent.
- `pptx:vert`/`pptx:anchor`/`pptx:autofit` on `<text>` → `a:bodyPr`;
  `pptx:name` → shape name; `pptx:line-height`/`pptx:space-before`/
  `pptx:soft-break` → paragraph layout (aliases of `data-paragraph-*`).
- `animations.json` remains supported; inline `pptx:` wins per key.
  `data-pptx-*` is compiler-internal metadata — never authored by hand.

See `SPEC.md` at the repository root for the language spec.

