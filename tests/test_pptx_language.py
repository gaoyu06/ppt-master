#!/usr/bin/env python3
"""Tests for the public ``pptx:`` namespace language.

Two levels: unit tests over ``svg_to_pptx.pptx_syntax`` /
``svg_to_pptx.deck_manifest`` (parse + normalize + errors), and
compile-level tests through ``convert_svg_to_slide_shapes`` /
``main()`` that assert the emitted DrawingML.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from svg_to_pptx.deck_manifest import (  # noqa: E402
    DeckManifestError,
    load_deck_manifest,
)
from svg_to_pptx.drawingml.converter import (  # noqa: E402
    convert_svg_to_slide_shapes,
)
from svg_to_pptx.pptx_syntax import (  # noqa: E402
    PPTX_NS,
    effect_call_filter_xml,
    extract_slide_semantics,
    merge_slide_cfg,
    normalize_language_attrs,
    parse_crop_src_rect,
    parse_effect_calls,
)

SVG_NS = 'http://www.w3.org/2000/svg'
_PPTX = f'{{{PPTX_NS}}}'
_SVG = f'{{{SVG_NS}}}'


def _root(body: str) -> ET.Element:
    return ET.fromstring(
        f'<svg xmlns="{SVG_NS}" xmlns:pptx="{PPTX_NS}" '
        f'viewBox="0 0 1280 720">{body}</svg>'
    )


def _semantics(body: str):
    return extract_slide_semantics(_root(body), 't.svg')


def _normalized(body: str) -> ET.Element:
    root = _root(body)
    normalize_language_attrs(root, 't.svg')
    return root


def _convert(body: str, tmp: Path, **kwargs) -> tuple:
    path = Path(tmp) / '01.svg'
    path.write_text(
        f'<svg xmlns="{SVG_NS}" xmlns:pptx="{PPTX_NS}" '
        f'viewBox="0 0 1280 720">{body}</svg>',
        encoding='utf-8',
    )
    return convert_svg_to_slide_shapes(
        path, resource_root=Path(tmp), **kwargs,
    )


def _convert_error(body: str, tmp: Path, **kwargs) -> str:
    with unittest.TestCase().assertRaises(Exception) as cm:
        _convert(body, tmp, **kwargs)
    return str(cm.exception)


# A tiny valid PNG (20x20 red) for image tests.
_PNG_10 = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x14\x00\x00\x00\x14'
    b'\x08\x02\x00\x00\x00\x02\xeb\x8aZ\x00\x00\x00*IDATx\x9cc\xfc\xcf'
    b'@>`\xa2@/\xc3\xa8f\x12\x01\x13\xa9\x1a\x90\xc1\xa8f\x12\x01\x13\xa9'
    b'\x1a\x90\xc1\xa8f\x12\x01E\x01\x06\x00"\x9c\x01\'#\xed|\xb4'
    b'\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _write_png(tmp: Path, name: str = 'img.png') -> Path:
    path = Path(tmp) / name
    path.write_bytes(_PNG_10)
    return path


class AnimSyntaxTests(unittest.TestCase):
    """<pptx:anim> → slide_cfg group entries."""

    def test_basic_entry(self) -> None:
        sem = _semantics(
            '<g id="card"><pptx:anim effect="entrance_fade"/></g>'
        )
        entry = sem.groups['card']
        self.assertEqual(entry['effect'], 'entrance_fade')
        self.assertEqual(entry['order'], 1)
        self.assertIn('card', sem.anim_anchor_ids)

    def test_start_mapping(self) -> None:
        for raw, expected in (
            ('click', 'on-click'),
            ('with', 'with-previous'),
            ('after', 'after-previous'),
        ):
            with self.subTest(start=raw):
                sem = _semantics(
                    f'<g id="g"><pptx:anim effect="entrance_fade" '
                    f'start="{raw}"/></g>'
                )
                self.assertEqual(sem.groups['g']['trigger'], expected)

    def test_timing_and_options(self) -> None:
        sem = _semantics(
            '<g id="g"><pptx:anim effect="entrance_fly" dir="up" '
            'dur="0.5" delay="0.2" autorev="true" accel="0.3" '
            'after="color=#FF0000"/></g>'
        )
        entry = sem.groups['g']
        self.assertEqual(entry['duration'], 0.5)
        self.assertEqual(entry['delay'], 0.2)
        self.assertIs(entry['auto_reverse'], True)
        self.assertEqual(entry['accelerate'], 0.3)
        self.assertEqual(entry['effect_options']['direction'], 'up')
        self.assertEqual(
            entry['after_effect'], {'type': 'dim', 'color': '#FF0000'}
        )

    def test_repeat_forms(self) -> None:
        for raw, field, expected in (
            ('3', 'repeat_count', 3),
            ('2s', 'repeat_duration', 2.0),
            ('indefinite', 'repeat_count', 'indefinite'),
        ):
            with self.subTest(repeat=raw):
                sem = _semantics(
                    f'<g id="g"><pptx:anim effect="emphasis_grow_shrink" '
                    f'repeat="{raw}"/></g>'
                )
                self.assertEqual(sem.groups['g'][field], expected)

    def test_on_trigger(self) -> None:
        sem = _semantics(
            '<g id="g"><pptx:anim effect="entrance_fade" on="btn"/></g>'
        )
        self.assertEqual(sem.groups['g']['trigger_shape'], 'btn')

    def test_path_effect_and_custom_path(self) -> None:
        sem = _semantics(
            '<g id="g"><pptx:anim effect="path" path="M0,0 L100,50" '
            'relative="true" dur="1"/></g>'
        )
        entry = sem.groups['g']
        self.assertEqual(entry['effect'], 'path_custom')
        self.assertEqual(entry['effect_options']['path'], 'M0,0 L100,50')
        self.assertIs(entry['effect_options']['relative'], True)

    def test_multiple_anims_become_effects_list(self) -> None:
        sem = _semantics(
            '<g id="g">'
            '<pptx:anim effect="entrance_fade"/>'
            '<pptx:anim effect="emphasis_grow_shrink" start="after"/>'
            '</g>'
        )
        entry = sem.groups['g']
        self.assertIn('effects', entry)
        self.assertEqual(
            [e['effect'] for e in entry['effects']],
            ['entrance_fade', 'emphasis_grow_shrink'],
        )
        self.assertEqual(entry['effects'][1]['order'], 2)

    def test_numeric_option_coercion(self) -> None:
        sem = _semantics(
            '<g id="g"><pptx:anim effect="emphasis_grow_shrink" '
            'size="150"/></g>'
        )
        self.assertEqual(sem.groups['g']['effect_options']['size'], 150)

    def test_errors(self) -> None:
        cases = [
            # missing effect
            '<g id="g"><pptx:anim/></g>',
            # bad start
            '<g id="g"><pptx:anim effect="entrance_fade" start="now"/></g>',
            # bad bool
            '<g id="g"><pptx:anim effect="entrance_fade" autorev="yep"/></g>',
            # bad dur
            '<g id="g"><pptx:anim effect="entrance_fade" dur="x"/></g>',
            # unknown attribute on pptx:anim
            '<g id="g"><pptx:anim effect="entrance_fade" bogus="1"/></g>',
            # anim at root (no non-root parent)
            '<pptx:anim effect="entrance_fade"/>',
            # parent element without id
            '<g><rect><pptx:anim effect="entrance_fade"/></rect></g>',
            # nested inside another pptx element is not allowed
            '<g id="g"><pptx:anim effect="entrance_fade">'
            '<pptx:anim effect="entrance_fade"/></pptx:anim></g>',
        ]
        for body in cases:
            with self.subTest(body=body[:60]):
                with self.assertRaises(ValueError):
                    _semantics(body)

    def test_unknown_pptx_element_and_attr(self) -> None:
        with self.assertRaises(ValueError):
            _semantics('<pptx:bogus/>')
        with self.assertRaises(ValueError):
            _semantics('<g id="g" pptx:bogus="1"/>')


class BuildAttrTests(unittest.TestCase):
    """pptx:build="paragraph" flags every anim on the element."""

    def test_marks_by_paragraph(self) -> None:
        sem = _semantics(
            '<g id="bullets" pptx:build="paragraph">'
            '<pptx:anim effect="entrance_wipe" dir="up"/>'
            '<pptx:anim effect="emphasis_grow_shrink"/>'
            '</g>'
        )
        entry = sem.groups['bullets']
        self.assertTrue(
            all(e['by_paragraph'] for e in entry['effects'])
        )

    def test_requires_anim(self) -> None:
        with self.assertRaises(ValueError):
            _semantics('<g id="g" pptx:build="paragraph"/>')

    def test_rejects_other_values_and_missing_id(self) -> None:
        with self.assertRaises(ValueError):
            _semantics('<g id="g" pptx:build="word"/>')
        with self.assertRaises(ValueError):
            _semantics('<g pptx:build="paragraph"/>')


class TransitionSyntaxTests(unittest.TestCase):
    """<pptx:transition> → slide_cfg transition."""

    def test_effect_duration_advance(self) -> None:
        sem = _semantics(
            '<pptx:transition effect="fade" dur="0.5" advance="5"/>'
        )
        cfg = sem.transition
        self.assertEqual(cfg['effect'], 'fade')
        self.assertEqual(cfg['duration'], 0.5)
        self.assertEqual(cfg['auto_advance'], 5.0)

    def test_option_attrs(self) -> None:
        sem = _semantics(
            '<pptx:transition effect="push" dir="left" '
            'through-black="true" pages="3"/>'
        )
        opts = sem.transition['effect_options']
        self.assertEqual(opts['direction'], 'left')
        self.assertIs(opts['through_black'], True)
        self.assertEqual(opts['pages'], 3)

    def test_must_be_root_child(self) -> None:
        with self.assertRaises(ValueError):
            _semantics(
                '<g id="g"><pptx:transition effect="fade"/></g>'
            )

    def test_duplicate_and_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _semantics(
                '<pptx:transition effect="fade"/>'
                '<pptx:transition effect="push"/>'
            )
        with self.assertRaises(ValueError):
            _semantics('<pptx:transition/>')

    def test_unknown_attr_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _semantics('<pptx:transition effect="fade" bogus="1"/>')


class NotesSyntaxTests(unittest.TestCase):
    def test_text_extracted(self) -> None:
        sem = _semantics('<pptx:notes>  讲两句  </pptx:notes>')
        self.assertEqual(sem.notes, '讲两句')

    def test_empty_notes_is_none(self) -> None:
        sem = _semantics('<pptx:notes>   </pptx:notes>')
        self.assertIsNone(sem.notes)

    def test_must_be_root_child_and_unique(self) -> None:
        with self.assertRaises(ValueError):
            _semantics('<g id="g"><pptx:notes>x</pptx:notes></g>')
        with self.assertRaises(ValueError):
            _semantics(
                '<pptx:notes>a</pptx:notes><pptx:notes>b</pptx:notes>'
            )


class MergeSlideCfgTests(unittest.TestCase):
    def test_inline_overrides_sidecar(self) -> None:
        sidecar = {
            'transition': {'effect': 'push'},
            'groups': {'a': {'effect': 'entrance_fade'}},
        }
        sem = _semantics(
            '<pptx:transition effect="fade"/>'
            '<g id="a"><pptx:anim effect="entrance_zoom"/></g>'
        )
        merged = merge_slide_cfg(sidecar, sem)
        self.assertEqual(merged['transition']['effect'], 'fade')
        self.assertEqual(
            merged['groups']['a']['effect'], 'entrance_zoom'
        )

    def test_sidecar_preserved_without_inline(self) -> None:
        sidecar = {'transition': {'effect': 'push'}}
        merged = merge_slide_cfg(sidecar, _semantics(''))
        self.assertEqual(merged['transition']['effect'], 'push')


class NormalizePhTests(unittest.TestCase):
    """pptx:ph → data-pptx-placeholder (incl. aliases)."""

    def test_canonical_and_aliases(self) -> None:
        for raw, expected in (
            ('title', 'title'),
            ('pic', 'picture'),
            ('tbl', 'table'),
            ('Picture', 'picture'),
        ):
            with self.subTest(ph=raw):
                root = _normalized(f'<g id="s" pptx:ph="{raw}"/>')
                slot = root.find(f'{_SVG}g')
                self.assertEqual(
                    slot.get('data-pptx-placeholder'), expected
                )

    def test_invalid_and_conflict(self) -> None:
        with self.assertRaises(ValueError):
            _normalized('<g id="s" pptx:ph="bogus"/>')
        with self.assertRaises(ValueError):
            _normalized(
                '<g id="s" pptx:ph="title" data-pptx-placeholder="body"/>'
            )


class NormalizeFormulaTests(unittest.TestCase):
    """pptx:formula → data-pptx-inline-formula on a tspan."""

    def test_direct_text_wrapped_in_tspan(self) -> None:
        root = _normalized(
            '<text x="10" y="10" pptx:formula="x^2">x²</text>'
        )
        text = root.find(f'{_SVG}text')
        tspan = text.find(f'{_SVG}tspan')
        self.assertIsNotNone(tspan)
        self.assertEqual(tspan.get('data-pptx-inline-formula'), 'x^2')
        self.assertEqual(tspan.text, 'x²')
        self.assertIsNone(text.text)

    def test_single_tspan_marked(self) -> None:
        root = _normalized(
            '<text x="10" y="10" pptx:formula="x^2">'
            '<tspan>x²</tspan></text>'
        )
        tspan = root.find(f'{_SVG}text/{_SVG}tspan')
        self.assertEqual(tspan.get('data-pptx-inline-formula'), 'x^2')

    def test_on_tspan_directly(self) -> None:
        root = _normalized(
            '<text x="10" y="10"><tspan pptx:formula="x^2">x²</tspan>'
            '</text>'
        )
        tspan = root.find(f'{_SVG}text/{_SVG}tspan')
        self.assertEqual(tspan.get('data-pptx-inline-formula'), 'x^2')

    def test_errors(self) -> None:
        cases = [
            # multiple tspans, ambiguous preview carrier
            '<text pptx:formula="x^2"><tspan>a</tspan>'
            '<tspan>b</tspan></text>',
            # no preview text at all
            '<text pptx:formula="x^2"></text>',
            # wrong element
            '<rect pptx:formula="x^2"/>',
            # conflicts with the internal marker
            '<text><tspan pptx:formula="x^2" '
            'data-pptx-inline-formula="y">x</tspan></text>',
        ]
        for body in cases:
            with self.subTest(body=body[:60]):
                with self.assertRaises(ValueError):
                    _normalized(body)


class NormalizeDataTests(unittest.TestCase):
    """pptx:data → native-object markers with JSON authority."""

    def test_chart_normalizes(self) -> None:
        payload = {
            'kind': 'chart', 'type': 'bar', 'name': 'c',
            'categories': ['A'], 'series': [{'name': 's', 'values': [1]}],
            'x': 0, 'y': 0, 'width': 100, 'height': 100,
        }
        root = _normalized(
            f'<g id="c" pptx:data="{json.dumps(payload).replace(chr(34), "&quot;")}">'
            '<rect width="10" height="10"/></g>'
        )
        g = root.find(f'{_SVG}g')
        self.assertEqual(g.get('data-pptx-replace-with'), 'chart')
        self.assertEqual(g.get('data-pptx-native-authority'), 'json')
        stored = json.loads(g.get('data-pptx-json'))
        self.assertNotIn('kind', stored)
        self.assertEqual(stored['type'], 'bar')

    def test_table_kind(self) -> None:
        payload = {
            'kind': 'table',
            'schema': 'ppt-master.semantic-table.v2',
            'columns': ['a'], 'rows': [['1']],
            'x': 0, 'y': 0, 'width': 100, 'height': 50,
        }
        root = _normalized(
            f'<g id="t" pptx:data="{json.dumps(payload).replace(chr(34), "&quot;")}"/>'
        )
        self.assertEqual(
            root.find(f'{_SVG}g').get('data-pptx-replace-with'), 'table'
        )

    def test_errors(self) -> None:
        cases = [
            # not valid JSON
            '<g id="g" pptx:data="{oops}"/>',
            # JSON but not an object
            '<g id="g" pptx:data="[1,2]"/>',
            # missing kind
            '<g id="g" pptx:data="{&quot;type&quot;:&quot;bar&quot;}"/>',
            # unsupported kind
            '<g id="g" pptx:data="{&quot;kind&quot;:&quot;smartart&quot;}"/>',
            # wrong element
            '<rect pptx:data="{&quot;kind&quot;:&quot;chart&quot;}"/>',
            # conflicts with internal marker
            '<g id="g" data-pptx-replace-with="chart" '
            'pptx:data="{&quot;kind&quot;:&quot;chart&quot;}"/>',
        ]
        for body in cases:
            with self.subTest(body=body[:60]):
                with self.assertRaises(ValueError):
                    _normalized(body)


class CropTests(unittest.TestCase):
    """pptx:crop parse + element-scope check."""

    def test_fractions_to_src_rect_units(self) -> None:
        self.assertEqual(
            parse_crop_src_rect('0.1,0.2,0.3,0.1'),
            (10000, 20000, 30000, 10000),
        )

    def test_zero_crop_returns_none(self) -> None:
        self.assertIsNone(parse_crop_src_rect('0,0,0,0'))

    def test_parse_errors(self) -> None:
        for raw in ('1,2,3', 'a,b,c,d', '-0.1,0,0,0', '0.7,0,0.5,0'):
            with self.subTest(crop=raw):
                with self.assertRaises(ValueError):
                    parse_crop_src_rect(raw)

    def test_only_valid_on_image(self) -> None:
        _normalized('<image href="i.png" pptx:crop="0.1,0,0,0"/>')
        with self.assertRaises(ValueError):
            _normalized('<rect pptx:crop="0.1,0,0,0"/>')


class EffectCallTests(unittest.TestCase):
    """pptx:effect micro-syntax → synthesized <filter>."""

    def test_parse_multiple_calls(self) -> None:
        calls = parse_effect_calls(
            'inner-shadow(blur=8,dist=4) soft-edge(rad=6)', 't'
        )
        self.assertEqual(
            calls,
            [
                ('inner-shadow', {'blur': '8', 'dist': '4'}),
                ('soft-edge', {'rad': '6'}),
            ],
        )

    def test_parse_errors(self) -> None:
        for raw in ('unknown()', 'glow(bogus=1)', 'glow(rad)', '', 'x y'):
            with self.subTest(effect=raw):
                with self.assertRaises(ValueError):
                    parse_effect_calls(raw, 't')

    def test_shadow_filter_synthesis(self) -> None:
        f = effect_call_filter_xml(
            'outer-shadow',
            {'blur': '8', 'dist': '4', 'dir': '90', 'color': '#00000080'},
            't',
        )
        self.assertEqual(f.get('data-pptx-effect'), 'shadow')
        drop = f.find('feDropShadow')
        self.assertIsNotNone(drop)
        self.assertAlmostEqual(float(drop.get('dy')), 4.0)
        self.assertEqual(drop.get('flood-color'), '#000000')
        self.assertAlmostEqual(
            float(drop.get('flood-opacity')), 128 / 255, places=3,
        )

    def test_per_kind_param_restrictions(self) -> None:
        with self.assertRaises(ValueError):
            effect_call_filter_xml('soft-edge', {'dist': '4'}, 't')
        with self.assertRaises(ValueError):
            effect_call_filter_xml('blur', {'color': '#000'}, 't')

    def test_glow_and_blur_primitives(self) -> None:
        glow = effect_call_filter_xml(
            'glow', {'rad': '10', 'color': '#FF0000'}, 't'
        )
        self.assertIsNotNone(glow.find('feGaussianBlur'))
        self.assertIsNotNone(glow.find('feFlood'))
        blur = effect_call_filter_xml('blur', {'rad': '14'}, 't')
        self.assertIsNotNone(blur.find('feGaussianBlur'))
        self.assertIsNone(blur.find('feFlood'))


class DeckManifestTests(unittest.TestCase):
    """deck.xml: page order, metadata, validation."""

    def _project(self, tmp: Path, pages: list[str]) -> Path:
        for name in pages:
            path = Path(tmp) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"/>',
                encoding='utf-8',
            )
        return Path(tmp)

    def _write_manifest(self, tmp: Path, body: str, attrs: str = '') -> None:
        (Path(tmp) / 'deck.xml').write_text(
            f'<pptx:deck xmlns:pptx="{PPTX_NS}" {attrs}>{body}</pptx:deck>',
            encoding='utf-8',
        )

    def test_page_order_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = self._project(Path(d), ['pages/a.svg', 'pages/b.svg'])
            self._write_manifest(
                tmp,
                '<pptx:page src="pages/b.svg"/>'
                '<pptx:page src="pages/a.svg"/>',
                attrs='title="演示" lang="zh-CN" format="ppt169"',
            )
            manifest = load_deck_manifest(tmp)
            self.assertEqual(
                [p.name for p in manifest.pages], ['b.svg', 'a.svg']
            )
            self.assertEqual(manifest.title, '演示')
            self.assertEqual(manifest.lang, 'zh-CN')
            self.assertEqual(manifest.format, 'ppt169')
            self.assertEqual(manifest.source_dir, 'pages')

    def test_absent_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(load_deck_manifest(Path(d)))

    def test_errors(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = self._project(Path(d), ['pages/a.svg', 'pages/b.svg'])
            bad_manifests = [
                # wrong root
                '<deck/>',
                # unknown deck attr
                f'<pptx:deck xmlns:pptx="{PPTX_NS}" bogus="1">'
                '<pptx:page src="pages/a.svg"/></pptx:deck>',
                # unknown format
                f'<pptx:deck xmlns:pptx="{PPTX_NS}" format="nope">'
                '<pptx:page src="pages/a.svg"/></pptx:deck>',
                # bad lang tag
                f'<pptx:deck xmlns:pptx="{PPTX_NS}" lang="!!!">'
                '<pptx:page src="pages/a.svg"/></pptx:deck>',
                # non-page child
                f'<pptx:deck xmlns:pptx="{PPTX_NS}"><pptx:other/></pptx:deck>',
                # missing src
                f'<pptx:deck xmlns:pptx="{PPTX_NS}"><pptx:page/></pptx:deck>',
                # escapes project
                f'<pptx:deck xmlns:pptx="{PPTX_NS}">'
                '<pptx:page src="../x.svg"/></pptx:deck>',
                # not .svg
                f'<pptx:deck xmlns:pptx="{PPTX_NS}">'
                '<pptx:page src="pages/a.png"/></pptx:deck>',
                # missing file
                f'<pptx:deck xmlns:pptx="{PPTX_NS}">'
                '<pptx:page src="pages/ghost.svg"/></pptx:deck>',
                # duplicate
                f'<pptx:deck xmlns:pptx="{PPTX_NS}">'
                '<pptx:page src="pages/a.svg"/>'
                '<pptx:page src="pages/a.svg"/></pptx:deck>',
                # empty
                f'<pptx:deck xmlns:pptx="{PPTX_NS}"/>',
                # pages in different dirs
                f'<pptx:deck xmlns:pptx="{PPTX_NS}">'
                '<pptx:page src="pages/a.svg"/>'
                '<pptx:page src="b.svg"/></pptx:deck>',
                # unknown page attr
                f'<pptx:deck xmlns:pptx="{PPTX_NS}">'
                '<pptx:page src="pages/a.svg" bogus="1"/></pptx:deck>',
            ]
            # need a root-level page for the multi-dir case
            (tmp / 'b.svg').write_text(
                f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720"/>',
                encoding='utf-8',
            )
            for body in bad_manifests:
                with self.subTest(body=body[:60]):
                    (tmp / 'deck.xml').write_text(body, encoding='utf-8')
                    with self.assertRaises(DeckManifestError):
                        load_deck_manifest(tmp)

    def test_format_viewbox_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / 'pages').mkdir()
            (tmp / 'pages' / 'a.svg').write_text(
                f'<svg xmlns="{SVG_NS}" viewBox="0 0 960 540"/>',
                encoding='utf-8',
            )
            self._write_manifest(
                tmp,
                '<pptx:page src="pages/a.svg"/>',
                attrs='format="ppt169"',
            )
            with self.assertRaises(DeckManifestError):
                load_deck_manifest(tmp)


class ConvertFeatureTests(unittest.TestCase):
    """Compile-level: pptx: features reach DrawingML output."""

    def test_crop_emits_src_rect(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _write_png(Path(d))
            slide_xml, media, *_ = _convert(
                '<image href="img.png" x="0" y="0" width="200" height="100" '
                'pptx:crop="0.1,0.2,0.3,0.1"/>',
                Path(d),
            )
            self.assertIn(
                '<a:srcRect l="10000" t="20000" r="30000" b="10000"/>',
                slide_xml,
            )
            # the full source image is embedded, not a pre-cropped copy
            self.assertEqual(len(media), 1)

    def test_crop_overrides_slice(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _write_png(Path(d))
            slide_xml, *_ = _convert(
                '<image href="img.png" x="0" y="0" width="200" height="100" '
                'preserveAspectRatio="xMidYMid slice" '
                'pptx:crop="0.5,0,0,0"/>',
                Path(d),
            )
            self.assertIn('<a:srcRect l="50000"', slide_xml)

    def test_formula_emits_omml(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            slide_xml, *_ = _convert(
                '<text x="10" y="50" font-size="24" '
                'pptx:formula="x^2">x²</text>',
                Path(d),
            )
            self.assertIn('oMath', slide_xml)

    def test_data_chart_and_table_emit_graphic_frames(self) -> None:
        chart = {
            'kind': 'chart', 'type': 'bar', 'name': 'c',
            'categories': ['A', 'B'],
            'series': [{'name': 's', 'values': [1, 2]}],
            'x': 0, 'y': 0, 'width': 400, 'height': 300,
        }
        table = {
            'kind': 'table',
            'schema': 'ppt-master.semantic-table.v2',
            'columns': ['h1', 'h2'], 'rows': [['a', 'b']],
            'x': 0, 'y': 0, 'width': 200, 'height': 80,
        }
        for kind, payload, needle in (
            ('chart', chart, 'graphicFrame'),
            ('table', table, '<a:tbl>'),
        ):
            with self.subTest(kind=kind):
                attr = json.dumps(payload).replace('"', '&quot;')
                with tempfile.TemporaryDirectory() as d:
                    slide_xml, _m, _r, _a, pkg, _o = _convert(
                        f'<g id="obj" pptx:data="{attr}">'
                        '<rect width="10" height="10"/></g>',
                        Path(d),
                        native_objects=True,
                    )
                    self.assertIn(needle, slide_xml)
                    if kind == 'chart':
                        # charts emit chart XML + embedded workbook parts
                        self.assertTrue(
                            any('chart' in k for k in pkg),
                            f'no chart package part emitted: {list(pkg)}',
                        )

    def test_effect_calls_emit_effect_lst(self) -> None:
        cases = [
            ('outer-shadow(blur=8,dist=4)', '<a:outerShdw'),
            ('inner-shadow(blur=8)', '<a:innerShdw'),
            ('glow(rad=10,color=#FF0000)', '<a:glow'),
            ('soft-edge(rad=6)', '<a:softEdge'),
            ('reflection(dist=8)', '<a:reflection'),
            ('blur(rad=14)', '<a:blur'),
        ]
        for effect, needle in cases:
            with self.subTest(effect=effect):
                with tempfile.TemporaryDirectory() as d:
                    slide_xml, *_ = _convert(
                        f'<g id="g"><rect width="50" height="50" '
                        f'fill="#112233" pptx:effect="{effect}"/></g>',
                        Path(d),
                    )
                    self.assertIn(needle, slide_xml)

    def test_reflection_emits_vertical_flip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            slide_xml, *_ = _convert(
                '<g id="g"><rect width="50" height="50" fill="#112233" '
                'pptx:effect="reflection(dist=8)"/></g>',
                Path(d),
            )
            self.assertIn('sy="-100000"', slide_xml)

    def test_effect_on_text(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            slide_xml, *_ = _convert(
                '<text x="10" y="50" font-size="24" '
                'pptx:effect="outer-shadow(blur=6)">t</text>',
                Path(d),
            )
            self.assertIn('<a:outerShdw', slide_xml)

    def test_line_height_multiple_emits_spc_pct(self) -> None:
        for raw, expected in (
            ('1.6x', 'val="160000"'),
            ('160%', 'val="160000"'),
        ):
            with self.subTest(lh=raw):
                with tempfile.TemporaryDirectory() as d:
                    slide_xml, *_ = _convert(
                        f'<text x="10" y="50" font-size="20" '
                        f'pptx:line-height="{raw}">'
                        '<tspan x="10" dy="0">• a</tspan>'
                        '<tspan x="10" dy="32">• b</tspan></text>',
                        Path(d),
                    )
                    self.assertIn('spcPct', slide_xml)
                    self.assertIn(expected, slide_xml)

    def test_line_height_px_emits_spc_pts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            slide_xml, *_ = _convert(
                '<text x="10" y="50" font-size="20" '
                'pptx:line-height="32px">'
                '<tspan x="10" dy="0">• a</tspan>'
                '<tspan x="10" dy="32">• b</tspan></text>',
                Path(d),
            )
            self.assertIn('spcPts', slide_xml)

    def test_line_break_soft_break_space_before(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            slide_xml, *_ = _convert(
                '<text x="10" y="50" font-size="20" '
                'pptx:line-height="1.6x">'
                '<tspan x="10" dy="0">• a</tspan>'
                '<tspan x="10" dy="32" pptx:line-break="true">• b</tspan>'
                '<tspan x="10" dy="64" pptx:space-before="12">• c</tspan>'
                '</text>',
                Path(d),
            )
            # line-break: 'b' continues 'a' in one <a:p> via <a:br/>
            # space-before: 'c' is its own paragraph with <a:spcBef>
            self.assertIn('<a:br/>', slide_xml)
            self.assertIn('<a:spcBef>', slide_xml)
            self.assertEqual(slide_xml.count('<a:p>'), 2)

    def test_vert_anchor_autofit_name(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            slide_xml, *_ = _convert(
                '<text x="10" y="50" font-size="20" pptx:vert="eaVert" '
                'pptx:anchor="ctr" pptx:autofit="norm" '
                'pptx:name="标题框">t</text>',
                Path(d),
            )
            self.assertIn('vert="eaVert"', slide_xml)
            self.assertIn('anchor="ctr"', slide_xml)
            self.assertIn('<a:normAutofit/>', slide_xml)
            self.assertIn('name="标题框"', slide_xml)

    def test_compile_errors_surface(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for body, fragment in (
                ('<g id="g" pptx:ph="bogus"/>', 'pptx:ph'),
                ('<rect pptx:crop="0,0,0,0"/>', 'pptx:crop'),
                ('<g id="g" pptx:data="{bad}"/>', 'pptx:data'),
            ):
                with self.subTest(body=body):
                    error = _convert_error(body, Path(d))
                    self.assertIn(fragment, error)


class DeckCompileTests(unittest.TestCase):
    """Full main() export: deck.xml ordering + slide semantics."""

    def _project(self, tmp: Path) -> Path:
        pages = Path(tmp) / 'pages'
        pages.mkdir()
        (pages / '01-first.svg').write_text(
            f'<svg xmlns="{SVG_NS}" xmlns:pptx="{PPTX_NS}" '
            'viewBox="0 0 1280 720">'
            '<pptx:transition effect="fade" dur="0.5" advance="5"/>'
            '<pptx:notes>封面备注</pptx:notes>'
            '<g id="hero">'
            '<pptx:anim effect="emphasis_grow_shrink" '
            'repeat="indefinite" autorev="true"/>'
            '<rect width="100" height="100" fill="#FF0000"/>'
            '</g>'
            '<g id="btn"><rect x="200" y="200" width="60" height="30"/>'
            '</g>'
            '<g id="pop">'
            '<pptx:anim effect="entrance_fade" on="btn"/>'
            '<rect x="300" y="200" width="60" height="30"/>'
            '</g>'
            '<g id="bullets" pptx:build="paragraph">'
            '<pptx:anim effect="entrance_wipe" dir="up"/>'
            '<text x="10" y="400" font-size="20" '
            'pptx:line-height="1.6x">'
            '<tspan x="10" dy="0">• a</tspan>'
            '<tspan x="10" dy="32">• b</tspan></text></g>'
            '</svg>',
            encoding='utf-8',
        )
        (pages / '02-second.svg').write_text(
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 1280 720">'
            '<rect width="100" height="100" fill="#00FF00"/>'
            '<text x="10" y="50" font-size="20">SECOND_PAGE</text></svg>',
            encoding='utf-8',
        )
        (Path(tmp) / 'deck.xml').write_text(
            f'<pptx:deck xmlns:pptx="{PPTX_NS}" title="测试标题" '
            'lang="zh-CN">'
            '<pptx:page src="pages/02-second.svg"/>'
            '<pptx:page src="pages/01-first.svg"/>'
            '</pptx:deck>',
            encoding='utf-8',
        )
        return Path(tmp)

    def test_full_export(self) -> None:
        from svg_to_pptx.pptx_package.cli import main

        with tempfile.TemporaryDirectory() as d:
            project = self._project(Path(d))
            out = Path(d) / 'out.pptx'
            rc = main([str(project), '-o', str(out), '--quick-generate'])
            self.assertEqual(rc, 0)
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
                slide1 = z.read('ppt/slides/slide1.xml').decode()
                slide2 = z.read('ppt/slides/slide2.xml').decode()
                core = z.read('docProps/core.xml').decode()

                # manifest order: 02-second is slide1
                self.assertIn('SECOND_PAGE', slide1)
                self.assertNotIn('SECOND_PAGE', slide2)

                # manifest metadata
                self.assertIn('测试标题', core)
                self.assertIn('zh-CN', core)

                # notes from <pptx:notes>
                notes_parts = [
                    n for n in names if n.startswith('ppt/notesSlides/')
                    and n.endswith('.xml')
                ]
                self.assertTrue(notes_parts)
                notes_xml = z.read(notes_parts[0]).decode()
                self.assertIn('封面备注', notes_xml)

                # transition + auto-advance on the slide carrying them
                self.assertIn('<p:transition', slide2)
                self.assertIn('advTm="5000"', slide2)

                # object animation timing
                self.assertIn('<p:timing>', slide2)
                self.assertIn('repeatCount="indefinite"', slide2)
                self.assertIn('autoRev="1"', slide2)
                self.assertIn('bldP', slide2)
                self.assertIn('pRg', slide2)
                self.assertIn('interactiveSeq', slide2)


if __name__ == '__main__':
    unittest.main()
