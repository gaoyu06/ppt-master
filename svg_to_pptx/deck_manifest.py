"""deck.xml — optional deck manifest for the SVG→PPTX compiler.

``deck.xml`` at the project root declares deck-level properties and, when
``<pptx:page>`` children are present, the authoritative page list and
order. The manifest is a compile-time input only; it never ships inside
the produced PPTX.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from svg_to_pptx.language_tags import LanguageTagError, normalize_language_tag
from svg_to_pptx.project_utils import CANVAS_FORMATS

from .pptx_syntax import PPTX_NS

_TAG = f'{{{PPTX_NS}}}'

_DECK_ATTRS = frozenset({'format', 'lang', 'title'})
_PAGE_ATTRS = frozenset({'src'})


class DeckManifestError(ValueError):
    """Raised when deck.xml is malformed or references missing pages."""


@dataclass
class DeckManifest:
    """Parsed deck manifest.

    ``pages`` are resolved paths in manifest order. ``format`` is a
    canvas-format key (see ``CANVAS_FORMATS``); when set, every listed
    page's viewBox must match the format's canvas.
    """

    pages: list[Path]
    source_dir: str
    title: str | None = None
    lang: str | None = None
    format: str | None = None


def load_deck_manifest(project_path: Path) -> DeckManifest | None:
    """Parse ``project_path/deck.xml``; return None when absent."""
    manifest_path = project_path / 'deck.xml'
    if not manifest_path.is_file():
        return None
    try:
        root = ET.parse(str(manifest_path)).getroot()
    except ET.ParseError as exc:
        raise DeckManifestError(f'deck.xml is not well-formed XML: {exc}') \
            from None
    if root.tag != f'{_TAG}deck':
        raise DeckManifestError(
            'deck.xml root must be <pptx:deck '
            f'xmlns:pptx="{PPTX_NS}">; got <{root.tag}>'
        )
    for attr in root.attrib:
        name = attr.rsplit('}', 1)[-1]
        if name not in _DECK_ATTRS:
            raise DeckManifestError(
                f'deck.xml: unknown <pptx:deck> attribute {name!r} '
                f'(expected: {", ".join(sorted(_DECK_ATTRS))})'
            )

    deck_format = (root.get('format') or '').strip() or None
    if deck_format is not None and deck_format not in CANVAS_FORMATS:
        raise DeckManifestError(
            f'deck.xml: unknown format {deck_format!r} '
            f'(known: {", ".join(sorted(CANVAS_FORMATS))})'
        )
    deck_lang = (root.get('lang') or '').strip() or None
    if deck_lang is not None:
        try:
            deck_lang = normalize_language_tag(deck_lang)
        except LanguageTagError as exc:
            raise DeckManifestError(f'deck.xml: {exc}') from None
    deck_title = (root.get('title') or '').strip() or None

    pages: list[Path] = []
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if child.tag != f'{_TAG}page':
            raise DeckManifestError(
                'deck.xml: <pptx:deck> only contains <pptx:page> '
                f'elements; got <{child.tag.rsplit("}", 1)[-1]}>'
            )
        for attr in child.attrib:
            name = attr.rsplit('}', 1)[-1]
            if name not in _PAGE_ATTRS:
                raise DeckManifestError(
                    f'deck.xml: unknown <pptx:page> attribute {name!r} '
                    '(expected: src)'
                )
        src = (child.get('src') or '').strip()
        if not src:
            raise DeckManifestError('deck.xml: <pptx:page> requires src')
        page_path = (project_path / src).resolve()
        try:
            page_path.relative_to(project_path.resolve())
        except ValueError:
            raise DeckManifestError(
                f'deck.xml: page src {src!r} escapes the project directory'
            ) from None
        if page_path.suffix.lower() != '.svg':
            raise DeckManifestError(
                f'deck.xml: page src {src!r} is not an .svg file'
            )
        if not page_path.is_file():
            raise DeckManifestError(
                f'deck.xml: page src {src!r} does not exist'
            )
        if page_path in pages:
            raise DeckManifestError(
                f'deck.xml: duplicate page src {src!r}'
            )
        pages.append(page_path)

    if not pages:
        raise DeckManifestError(
            'deck.xml: <pptx:deck> must list at least one <pptx:page>'
        )

    parent_dirs = {page.parent for page in pages}
    if len(parent_dirs) != 1:
        raise DeckManifestError(
            'deck.xml: all <pptx:page> sources must live in one directory'
        )
    source_dir = str(
        pages[0].parent.relative_to(project_path.resolve())
    )

    if deck_format is not None:
        expected = CANVAS_FORMATS[deck_format]['viewbox']
        for page in pages:
            page_root = ET.parse(str(page)).getroot()
            viewbox = ' '.join(
                (page_root.get('viewBox') or '').split()
            )
            if viewbox != expected:
                raise DeckManifestError(
                    f'deck.xml: page {page.name} viewBox {viewbox!r} does '
                    f'not match format {deck_format!r} ({expected})'
                )

    return DeckManifest(
        pages=pages,
        source_dir=source_dir,
        title=deck_title,
        lang=deck_lang,
        format=deck_format,
    )
