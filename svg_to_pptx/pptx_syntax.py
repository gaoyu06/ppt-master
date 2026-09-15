"""pptx: namespace — the public language surface of the SVG→PPTX compiler.

Author-facing PowerPoint extensions use ``xmlns:pptx`` elements and
attributes. This module parses them into the same internal shapes the
legacy ``animations.json`` sidecar produces, so the existing slide-config
pipeline (transitions, sequence targets, notes) consumes them unchanged.
All semantic validation happens here at compile time — the standalone SVG
quality checker deliberately passes the namespace through.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

PPTX_NS = 'http://pptx-svg.dev/ns/1'
_TAG_PREFIX = f'{{{PPTX_NS}}}'

PPTX_ELEMENT_NAMES = frozenset({'anim', 'transition', 'notes'})
PPTX_ROOT_ELEMENTS = frozenset({'transition', 'notes'})
PPTX_ATTR_NAMES = frozenset({
    'vert', 'anchor', 'autofit',
    'effect', 'build', 'name',
    'line-height', 'space-before', 'soft-break', 'line-break',
    'ph', 'crop', 'formula', 'data',
})

# ``pptx:ph`` placeholder vocabulary (mirrors template_structure._PLACEHOLDERS).
_PLACEHOLDER_NAMES = frozenset({
    'title', 'subtitle', 'body', 'picture', 'chart', 'table',
    'object', 'media', 'date', 'footer', 'slide-number',
})
_PH_ALIASES = {'pic': 'picture', 'tbl': 'table'}

# ``pptx:data`` payload kinds that compile to native graphicFrames.
_NATIVE_DATA_KINDS = frozenset({'chart', 'table'})

_ANIM_STARTS = {
    'click': 'on-click',
    'with': 'with-previous',
    'after': 'after-previous',
}
_ANIM_OPTION_ATTRS = {
    'dir': 'direction',
    'amount': 'amount',
    'color': 'color',
    'font': 'font_name',
    'size': 'size',
    'path': 'path',
    'relative': 'relative',
}
_ANIM_TIMING_ATTRS = {
    'autorev': 'auto_reverse',
    'rewind': 'rewind',
    'accel': 'accelerate',
    'decel': 'decelerate',
    'bounce': 'bounce_end',
    'restart': 'restart',
}
_ANIM_ATTRS = frozenset({
    'effect', 'start', 'dur', 'delay', 'on', 'repeat', 'after', 'sound',
    *_ANIM_OPTION_ATTRS, *_ANIM_TIMING_ATTRS,
})
_TRANSITION_ATTRS = frozenset({'effect', 'dur', 'duration', 'advance', 'sound'})
_TRANSITION_OPTION_ATTRS = {
    'dir': 'direction',
    'orientation': 'orientation',
    'style': 'style',
    'shape': 'shape',
    'pattern': 'pattern',
    'origin': 'origin',
    'pages': 'pages',
    'through-black': 'through_black',
    'bounce': 'bounce',
}

_BOOL_TRUE = frozenset({'true', '1', 'yes', 'on'})
_BOOL_FALSE = frozenset({'false', '0', 'no', 'off'})


def pptx_attr(elem: ET.Element, name: str) -> str | None:
    """Read one ``pptx:``-namespaced attribute, or ``None``."""
    return elem.get(f'{_TAG_PREFIX}{name}')


def pptx_attr_or_data(
    elem: ET.Element,
    name: str,
    data_name: str | None = None,
) -> str | None:
    """Read ``pptx:name``, falling back to the legacy ``data-pptx-*`` key."""
    value = pptx_attr(elem, name)
    if value is not None:
        return value
    return elem.get(data_name or f'data-pptx-{name}')


def is_pptx_element(elem: ET.Element) -> bool:
    return isinstance(elem.tag, str) and elem.tag.startswith(_TAG_PREFIX)


def pptx_local_name(elem: ET.Element) -> str:
    return elem.tag[len(_TAG_PREFIX):]


def _svg_local(elem: ET.Element) -> str:
    tag = elem.tag
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else str(tag)


def _bool(raw: str, label: str) -> bool:
    value = raw.strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    raise ValueError(
        f'{label} must be a boolean (true/false); got {raw!r}'
    )


def _number(raw: str, label: str, *, positive: bool = False) -> float:
    try:
        value = float(raw.strip())
    except (ValueError, AttributeError):
        raise ValueError(f'{label} must be a number; got {raw!r}') from None
    if positive and value <= 0:
        raise ValueError(f'{label} must be positive; got {raw!r}')
    return value


def _check_attrs(
    elem: ET.Element,
    allowed: frozenset[str],
    label: str,
) -> None:
    """Reject unknown unprefixed attributes on a ``pptx:`` element."""
    unknown = sorted(set(elem.attrib) - allowed)
    if unknown:
        raise ValueError(
            f'{label} has unknown attribute(s): {", ".join(unknown)}; '
            f'supported: {", ".join(sorted(allowed))}'
        )


def _after_effect(raw: str) -> Any:
    value = raw.strip()
    if value.startswith('color='):
        return {'type': 'dim', 'color': value[len('color='):].strip()}
    if value == 'dim':
        return {'type': 'dim', 'color': '#000000'}
    return value


def _anim_entry(elem: ET.Element, label: str, order: int) -> dict[str, Any]:
    _check_attrs(elem, _ANIM_ATTRS, label)
    entry: dict[str, Any] = {'order': order}

    raw_effect = (elem.get('effect') or '').strip()
    if not raw_effect:
        raise ValueError(f'{label} requires an effect')
    entry['effect'] = 'path_custom' if raw_effect == 'path' else raw_effect

    raw_start = elem.get('start')
    if raw_start is not None:
        start = raw_start.strip().lower()
        if start not in _ANIM_STARTS:
            raise ValueError(
                f'{label} start must be one of '
                f'{", ".join(_ANIM_STARTS)}; got {raw_start!r}'
            )
        entry['trigger'] = _ANIM_STARTS[start]

    for attr, field in (('dur', 'duration'), ('delay', 'delay')):
        raw = elem.get(attr)
        if raw is not None:
            entry[field] = _number(
                raw, f'{label} {attr}', positive=(attr == 'dur'),
            )

    raw_on = elem.get('on')
    if raw_on is not None:
        on = raw_on.strip()
        if not on:
            raise ValueError(f'{label} on must be a non-empty id reference')
        entry['trigger_shape'] = on

    raw_repeat = elem.get('repeat')
    if raw_repeat is not None:
        repeat = raw_repeat.strip().lower()
        if re.fullmatch(r'\d+', repeat):
            entry['repeat_count'] = int(repeat)
        else:
            entry['repeat_duration'] = _number(
                repeat.removesuffix('s'), f'{label} repeat', positive=True,
            )

    for attr, field in _ANIM_TIMING_ATTRS.items():
        raw = elem.get(attr)
        if raw is None:
            continue
        if field in ('auto_reverse', 'rewind'):
            entry[field] = _bool(raw, f'{label} {attr}')
        elif field == 'restart':
            entry[field] = raw.strip()
        else:
            entry[field] = _number(raw, f'{label} {attr}')

    raw_after = elem.get('after')
    if raw_after is not None:
        entry['after_effect'] = _after_effect(raw_after)

    raw_sound = elem.get('sound')
    if raw_sound is not None:
        entry['sound'] = raw_sound.strip()

    options: dict[str, Any] = {}
    for attr, field in _ANIM_OPTION_ATTRS.items():
        raw = elem.get(attr)
        if raw is None:
            continue
        value = raw.strip()
        if field == 'relative':
            options[field] = _bool(raw, f'{label} relative')
        elif field in ('amount', 'size') and re.fullmatch(
            r'-?\d+(\.\d+)?', value,
        ):
            options[field] = (
                int(value) if '.' not in value else float(value)
            )
        else:
            options[field] = value
    if options:
        entry['effect_options'] = options
    return entry


def _transition_cfg(elem: ET.Element, label: str) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    raw_effect = elem.get('effect')
    if raw_effect is not None and raw_effect.strip():
        cfg['effect'] = raw_effect.strip()
    for attr in ('dur', 'duration'):
        raw = elem.get(attr)
        if raw is not None:
            cfg['duration'] = _number(raw, f'{label} {attr}')
            break
    raw_advance = elem.get('advance')
    if raw_advance is not None:
        cfg['auto_advance'] = _number(raw_advance, f'{label} advance')
    raw_sound = elem.get('sound')
    if raw_sound is not None:
        cfg['sound'] = raw_sound.strip()
    unknown = sorted(set(elem.attrib) - _TRANSITION_ATTRS - set(
        _TRANSITION_OPTION_ATTRS,
    ))
    if unknown:
        raise ValueError(
            f'{label} has unknown attribute(s): {", ".join(unknown)}; '
            'supported: effect, dur, advance, sound, '
            + ', '.join(sorted(_TRANSITION_OPTION_ATTRS))
        )
    options: dict[str, Any] = {}
    for attr, field in _TRANSITION_OPTION_ATTRS.items():
        raw = elem.get(attr)
        if raw is None:
            continue
        value: Any = raw.strip()
        if value.lower() in _BOOL_TRUE | _BOOL_FALSE:
            value = value.lower() in _BOOL_TRUE
        elif re.fullmatch(r'\d+', value):
            value = int(value)
        options[field] = value
    if options:
        cfg['effect_options'] = options
    if not cfg:
        raise ValueError(f'{label} requires at least an effect or advance')
    return cfg


@dataclass(frozen=True)
class SlideSemantics:
    """pptx:-language payload extracted from one page SVG."""

    transition: dict[str, Any] | None
    groups: dict[str, dict[str, Any]]
    notes: str | None
    anim_anchor_ids: frozenset[str]

    def slide_cfg(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {}
        if self.transition is not None:
            cfg['transition'] = self.transition
        if self.groups:
            cfg['groups'] = self.groups
        return cfg


def extract_slide_semantics(
    root: ET.Element,
    slide_name: str,
) -> SlideSemantics:
    """Parse all ``pptx:`` extension elements in one page SVG.

    ``<pptx:transition>``/``<pptx:notes>`` must be direct children of the
    page root; ``<pptx:anim>`` must be the child of an element carrying an
    ``id`` (its animation anchor). Document order of ``pptx:anim``
    elements across the page defines the animation-pane order.
    """
    transition: dict[str, Any] | None = None
    notes: str | None = None
    animated: dict[str, list[dict[str, Any]]] = {}
    build_ids: set[str] = set()
    known_ids: set[str] = set()

    parent_by_id = {id(child): elem for elem in root.iter() for child in elem}
    for elem in root.iter():
        elem_id = (elem.get('id') or '').strip()
        if elem_id:
            known_ids.add(elem_id)
        build = pptx_attr(elem, 'build')
        if build is not None:
            if build.strip() != 'paragraph':
                raise ValueError(
                    f'{slide_name}: pptx:build must be "paragraph"; '
                    f'got {build!r}'
                )
            if not elem_id:
                raise ValueError(
                    f'{slide_name}: pptx:build requires an element id'
                )
            build_ids.add(elem_id)

    for elem in root.iter():
        if is_pptx_element(elem):
            continue
        unknown_attrs = sorted(
            key[len(_TAG_PREFIX):]
            for key in elem.attrib
            if key.startswith(_TAG_PREFIX)
            and key[len(_TAG_PREFIX):] not in PPTX_ATTR_NAMES
        )
        if unknown_attrs:
            raise ValueError(
                f'{slide_name}: unknown pptx: attribute(s) '
                f'{", ".join(unknown_attrs)} on <{_svg_local(elem)}>; '
                f'supported: {", ".join(sorted(PPTX_ATTR_NAMES))}'
            )

    order = 0
    for elem in root.iter():
        if not is_pptx_element(elem):
            continue
        local = pptx_local_name(elem)
        parent = parent_by_id.get(id(elem))
        if local not in PPTX_ELEMENT_NAMES:
            raise ValueError(
                f'{slide_name}: unknown pptx: element <pptx:{local}>'
            )
        if local in PPTX_ROOT_ELEMENTS:
            if parent is not root:
                raise ValueError(
                    f'{slide_name}: <pptx:{local}> must be a direct child '
                    'of the page root <svg>'
                )
            if local == 'transition':
                if transition is not None:
                    raise ValueError(
                        f'{slide_name}: duplicate <pptx:transition>'
                    )
                transition = _transition_cfg(
                    elem, f'{slide_name}: <pptx:transition>',
                )
            else:
                if notes is not None:
                    raise ValueError(
                        f'{slide_name}: duplicate <pptx:notes>'
                    )
                text = ''.join(elem.itertext()).strip()
                notes = text or None
            continue
        # <pptx:anim>
        if parent is None or parent is root or is_pptx_element(parent):
            raise ValueError(
                f'{slide_name}: <pptx:anim> must be a child of a '
                'non-root element carrying an id'
            )
        target_id = (parent.get('id') or '').strip()
        if not target_id:
            raise ValueError(
                f'{slide_name}: <pptx:anim> parent must carry an id'
            )
        order += 1
        label = f'{slide_name}: <pptx:anim> on #{target_id}'
        animated.setdefault(target_id, []).append(
            _anim_entry(elem, label, order),
        )

    for target_id in build_ids:
        if target_id not in animated:
            raise ValueError(
                f'{slide_name}: pptx:build="paragraph" on #{target_id} '
                'requires at least one <pptx:anim> child'
            )

    groups: dict[str, dict[str, Any]] = {}
    for target_id, entries in animated.items():
        if target_id in build_ids:
            for entry in entries:
                entry['by_paragraph'] = True
        if len(entries) == 1:
            groups[target_id] = entries[0]
        else:
            groups[target_id] = {'effects': entries}

    return SlideSemantics(
        transition=transition,
        groups=groups,
        notes=notes,
        anim_anchor_ids=frozenset(animated),
    )


def merge_slide_cfg(
    sidecar_cfg: dict[str, Any],
    semantics: SlideSemantics,
) -> dict[str, Any]:
    """Merge inline pptx: semantics over the legacy sidecar slide config.

    Inline ``<pptx:transition>`` replaces ``transition``; inline
    ``<pptx:anim>`` groups override same-id sidecar group entries.
    """
    merged = dict(sidecar_cfg)
    inline = semantics.slide_cfg()
    if 'transition' in inline:
        merged['transition'] = inline['transition']
    if 'groups' in inline:
        groups = dict(merged.get('groups') or {})
        groups.update(inline['groups'])
        merged['groups'] = groups
    return merged


_EFFECT_CALL_RE = re.compile(
    r'([a-z][a-z0-9-]*)\s*(?:\(([^)]*)\))?', re.IGNORECASE,
)
_EFFECT_KINDS = frozenset({
    'outer-shadow', 'inner-shadow', 'glow', 'reflection', 'soft-edge', 'blur',
})
_EFFECT_KEYS = frozenset({
    'blur', 'dist', 'dir', 'color', 'alpha', 'rad', 'pos', 'end', 'fade',
})


def parse_effect_calls(value: str, label: str) -> list[tuple[str, dict[str, str]]]:
    """Parse ``pptx:effect="inner-shadow(blur=8,dist=4,dir=90) soft-edge(rad=6)"``."""
    calls: list[tuple[str, dict[str, str]]] = []
    pos = 0
    text = value.strip()
    while pos < len(text):
        match = _EFFECT_CALL_RE.match(text, pos)
        if match is None:
            raise ValueError(f'{label}: cannot parse effect call near {text[pos:]!r}')
        name = match.group(1).lower()
        if name not in _EFFECT_KINDS:
            raise ValueError(
                f'{label}: unknown effect {name!r}; supported: '
                + ', '.join(sorted(_EFFECT_KINDS))
            )
        params: dict[str, str] = {}
        raw_params = (match.group(2) or '').strip()
        if raw_params:
            for pair in raw_params.split(','):
                if '=' not in pair:
                    raise ValueError(
                        f'{label}: effect {name} parameter must be key=value; '
                        f'got {pair!r}'
                    )
                key, raw_val = pair.split('=', 1)
                key = key.strip().lower()
                if key not in _EFFECT_KEYS:
                    raise ValueError(
                        f'{label}: effect {name} has unknown key {key!r}; '
                        f'supported: {", ".join(sorted(_EFFECT_KEYS))}'
                    )
                params[key] = raw_val.strip()
        calls.append((name, params))
        pos = match.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    if not calls:
        raise ValueError(f'{label}: pptx:effect must name at least one effect')
    return calls


def _effect_color(raw: str) -> tuple[str, float]:
    """Split #rgb/#rrggbb/#rrggbbaa into (rrggbb, alpha)."""
    value = raw.strip().lstrip('#')
    if not re.fullmatch(r'[0-9a-fA-F]{3,8}', value):
        raise ValueError(f'pptx:effect color must be #hex; got {raw!r}')
    if len(value) == 3:
        value = ''.join(ch * 2 for ch in value)
    alpha = 1.0
    if len(value) == 4:
        value = ''.join(ch * 2 for ch in value)
    if len(value) == 8:
        alpha = int(value[6:8], 16) / 255.0
        value = value[:6]
    return value.upper(), alpha


_EFFECT_PARAM_KEYS = {
    'outer-shadow': {'blur', 'dist', 'dir', 'color', 'alpha'},
    'inner-shadow': {'blur', 'dist', 'dir', 'color', 'alpha'},
    'glow': {'rad', 'color', 'alpha'},
    'reflection': {'blur', 'dist', 'dir', 'alpha'},
    'soft-edge': {'rad'},
    'blur': {'rad'},
}


def effect_call_filter_xml(name: str, params: dict[str, str], label: str) -> ET.Element:
    """Synthesize a ``<filter>`` element that reuses the native builders."""
    import math

    unsupported = sorted(set(params) - _EFFECT_PARAM_KEYS[name])
    if unsupported:
        raise ValueError(
            f'{label}: effect {name} does not support key(s) '
            f'{", ".join(unsupported)}; supported: '
            + ', '.join(sorted(_EFFECT_PARAM_KEYS[name]))
        )

    def px(key: str, default: float = 0.0) -> float:
        raw = params.get(key)
        if raw is None:
            return default
        return _number(raw, f'{label} {name} {key}')

    color = '000000'
    alpha = 0.3
    if 'color' in params:
        color, alpha = _effect_color(params['color'])
    if 'alpha' in params:
        alpha = _number(params['alpha'], f'{label} {name} alpha')
        if alpha > 1:
            alpha /= 100.0

    filter_elem = ET.Element('filter')
    # ``outer-shadow`` is the public name; the internal kind is ``shadow``.
    filter_elem.set(
        'data-pptx-effect',
        'shadow' if name == 'outer-shadow' else name,
    )

    if name in ('outer-shadow', 'inner-shadow', 'reflection'):
        dist = px('dist', 4.0)
        direction = px('dir', 90.0)
        dx = dist * math.cos(math.radians(direction))
        dy = dist * math.sin(math.radians(direction))
        primitive = ET.SubElement(filter_elem, 'feDropShadow')
        primitive.set('stdDeviation', str(px('blur', 4.0) / 2.0))
        primitive.set('dx', f'{dx:.4f}')
        primitive.set('dy', f'{dy:.4f}')
        primitive.set('flood-color', f'#{color}')
        primitive.set('flood-opacity', f'{alpha:.4f}')
    elif name == 'glow':
        primitive = ET.SubElement(filter_elem, 'feGaussianBlur')
        primitive.set('stdDeviation', str(px('rad', 4.0)))
        flood = ET.SubElement(filter_elem, 'feFlood')
        flood.set('flood-color', f'#{color}')
        flood.set('flood-opacity', f'{alpha:.4f}')
    else:  # soft-edge, blur
        primitive = ET.SubElement(filter_elem, 'feGaussianBlur')
        primitive.set('stdDeviation', str(px('rad', 4.0) / 2.0))
    return filter_elem


_SVG_TSPAN = '{http://www.w3.org/2000/svg}tspan'


def normalize_language_attrs(root: ET.Element, slide_name: str) -> None:
    """Translate authored ``pptx:`` element attributes into internal markers.

    ``pptx:ph``/``pptx:formula``/``pptx:data`` are the public spellings of
    ``data-pptx-placeholder``/``data-pptx-inline-formula``/the native-object
    replacement pair. This pass runs on every parsed tree before validation
    and conversion so downstream pipelines see one dialect. ``pptx:crop``
    is read directly by the image converter and needs no rewrite.
    """
    for elem in root.iter():
        if not isinstance(elem.tag, str) or is_pptx_element(elem):
            continue
        tag = _svg_local(elem)
        label = f'{slide_name}: <{tag}>'
        ph = pptx_attr(elem, 'ph')
        if ph is not None:
            value = _PH_ALIASES.get(ph.strip().lower(), ph.strip().lower())
            if value not in _PLACEHOLDER_NAMES:
                raise ValueError(
                    f'{label} pptx:ph must be one of '
                    f'{", ".join(sorted(_PLACEHOLDER_NAMES))}; got {ph!r}'
                )
            if elem.get('data-pptx-placeholder') is not None:
                raise ValueError(
                    f'{label} sets both pptx:ph and data-pptx-placeholder'
                )
            elem.set('data-pptx-placeholder', value)
        formula = pptx_attr(elem, 'formula')
        if formula is not None:
            target = elem
            if tag == 'text':
                tspans = [
                    child for child in elem if child.tag == _SVG_TSPAN
                ]
                if len(tspans) > 1:
                    raise ValueError(
                        f'{label} pptx:formula needs a single <tspan> '
                        'carrying the preview text'
                    )
                if tspans:
                    target = tspans[0]
                elif (elem.text or '').strip():
                    # <text pptx:formula="…">preview</text> — wrap the
                    # direct text so the tspan-scoped formula marker
                    # and preview contract apply unchanged.
                    target = ET.SubElement(elem, _SVG_TSPAN)
                    target.text = elem.text
                    elem.text = None
                else:
                    raise ValueError(
                        f'{label} pptx:formula needs preview text or one '
                        '<tspan> child'
                    )
            elif tag != 'tspan':
                raise ValueError(
                    f'{label} pptx:formula is only valid on <text> or '
                    '<tspan>'
                )
            if target.get('data-pptx-inline-formula') is not None:
                raise ValueError(
                    f'{label} sets both pptx:formula and '
                    'data-pptx-inline-formula'
                )
            target.set('data-pptx-inline-formula', formula)
        data = pptx_attr(elem, 'data')
        if data is not None:
            if tag != 'g':
                raise ValueError(
                    f'{label} pptx:data is only valid on <g>'
                )
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f'{label} pptx:data is not valid JSON: {exc.msg}'
                ) from None
            if not isinstance(payload, dict):
                raise ValueError(
                    f'{label} pptx:data must be a JSON object'
                )
            kind = str(payload.get('kind') or '').strip().lower()
            if kind not in _NATIVE_DATA_KINDS:
                raise ValueError(
                    f'{label} pptx:data requires "kind" to be one of '
                    f'{", ".join(sorted(_NATIVE_DATA_KINDS))}; '
                    f'got {payload.get("kind")!r}'
                )
            if elem.get('data-pptx-replace-with') is not None:
                raise ValueError(
                    f'{label} sets both pptx:data and '
                    'data-pptx-replace-with'
                )
            # pptx:data declares the embedded JSON authoritative: the
            # visible children are an authored preview, so no
            # fallback-sha256 baseline is required.
            elem.set('data-pptx-replace-with', kind)
            elem.set('data-pptx-json', data)
            elem.set('data-pptx-native-authority', 'json')
        crop = pptx_attr(elem, 'crop')
        if crop is not None:
            if tag != 'image':
                raise ValueError(
                    f'{label} pptx:crop is only valid on <image>'
                )
            parse_crop_src_rect(crop)


def parse_crop_src_rect(value: str) -> tuple[int, int, int, int] | None:
    """Parse ``pptx:crop="l,t,r,b"`` fractions into DrawingML srcRect units
    (1/1000 of a percent). Returns None for a zero crop."""
    parts = [part.strip() for part in value.split(',')]
    if len(parts) != 4:
        raise ValueError(
            f'pptx:crop expects four fractions "l,t,r,b"; got {value!r}'
        )
    fractions: list[float] = []
    for part in parts:
        try:
            number = float(part)
        except ValueError:
            raise ValueError(
                f'pptx:crop values must be fractions; got {part!r}'
            ) from None
        if not 0.0 <= number <= 1.0:
            raise ValueError(
                f'pptx:crop values must be between 0 and 1; got {part!r}'
            )
        fractions.append(number)
    if fractions[0] + fractions[2] > 1.0 or fractions[1] + fractions[3] > 1.0:
        raise ValueError(
            'pptx:crop l+r and t+b must each not exceed 1; '
            f'got {value!r}'
        )
    l, t, r, b = (int(round(frac * 100000)) for frac in fractions)
    if not (l or t or r or b):
        return None
    return (l, t, r, b)
