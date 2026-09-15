"""Shared imports, constants, and module-level helpers for the lint
package. Mixins import this module wholesale so extracted methods keep
their original global namespace.
"""


import copy
import hashlib
import html
import json
import math
import re
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Tuple
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from native_payloads import NativePayloadError, hydrate_native_payload_refs
from pptx_workspace import (
    NATIVE_STRUCTURE_PATH,
    SOURCE_PPTX_PATH,
)
from slide_roster import discover_slide_svgs
from svg_authoring_contract import canonical_authoring_errors

from . import contracts as svg_contracts
from .xml_support import (
    SVG_NS,
    XLINK_NS,
    element_label as _element_label,
    local_name as _local_name,
)

try:
    from project_utils import (
        CANVAS_FORMATS,
        validate_communication_trace,
        validate_outline_roster,
    )
except ImportError:
    print("Warning: Unable to import project_utils")
    CANVAS_FORMATS = {}
    validate_communication_trace = None
    validate_outline_roster = None

from svg_to_pptx.canvas_contract import (
    CanvasContractError,
    parse_project_svg_root,
    parse_project_viewbox,
)

try:
    from project_management.project_specs import (
        parse_spec_lock as _parse_spec_lock,
        parse_spec_lock_image_value as _parse_spec_lock_image_value,
    )
except ImportError:
    _parse_spec_lock = None  # spec_lock anchor comparison will be skipped
    _parse_spec_lock_image_value = None

try:
    from svg_to_pptx.animation_config import (
        load_animation_config as _load_animation_config,
        usable_animation_group_id as _usable_animation_group_id,
        validate_animation_config as _validate_animation_config,
        validate_animation_config_errors as _validate_animation_config_errors,
        validate_transition_config as _validate_transition_config,
    )
except ImportError as exc:
    _load_animation_config = None
    _validate_animation_config = None
    _validate_animation_config_errors = None
    _validate_transition_config = None
    _animation_config_import_error = str(exc)

    def _usable_animation_group_id(raw: str | None) -> str | None:
        return raw if raw and raw.strip() else None
else:
    _animation_config_import_error = None

try:
    from svg_to_pptx.drawingml.utils import (
        IDENTITY_MATRIX as _IDENTITY_MATRIX,
        PROJECT_PAINT_PROPERTIES as _PAINT_PROPERTIES,
        PROJECT_TEXT_IMAGE_FILL_ATTR as _TEXT_IMAGE_FILL_ATTR,
        detect_text_lang as _detect_text_lang,
        is_cjk_char as _is_cjk_char,
        matrix_multiply as _matrix_multiply,
        parse_inline_style as _parse_inline_style,
        parse_project_geometry_length as _parse_project_geometry_length,
        parse_project_image_aspect_ratio as _parse_project_image_aspect_ratio,
        parse_project_opacity as _parse_project_opacity,
        parse_svg_color as _parse_export_color,
        parse_svg_length as _parse_svg_length,
        parse_transform_matrix as _parse_transform_matrix,
        project_definition_index as _project_definition_index,
        project_mask_errors as _project_mask_errors,
        rect_to_dml_xfrm as _rect_to_dml_xfrm,
        split_project_text_clusters as _split_project_text_clusters,
        svg_hidden_reason as _svg_hidden_reason,
        transform_point as _transform_point,
        unsafe_exported_font_faces as _unsafe_exported_font_faces,
        validate_dml_shape_matrix as _validate_dml_shape_matrix,
    )
except ImportError:
    _IDENTITY_MATRIX = None
    _PAINT_PROPERTIES = None
    _TEXT_IMAGE_FILL_ATTR = 'data-pptx-text-image-fill'
    _detect_text_lang = None
    _is_cjk_char = None
    _matrix_multiply = None
    _parse_inline_style = None
    _parse_project_geometry_length = None
    _parse_project_image_aspect_ratio = None
    _parse_project_opacity = None
    _parse_export_color = None
    _parse_svg_length = None
    _parse_transform_matrix = None
    _project_definition_index = None
    _project_mask_errors = None
    _rect_to_dml_xfrm = None
    _split_project_text_clusters = None
    _svg_hidden_reason = None
    _transform_point = None
    _unsafe_exported_font_faces = None
    _validate_dml_shape_matrix = None

try:
    from hyperlink_contract import (
        SHAPE_HYPERLINK_ATTR as _SHAPE_HYPERLINK_ATTR,
        project_hyperlink_errors as _project_hyperlink_errors,
    )
except ImportError:
    _SHAPE_HYPERLINK_ATTR = 'data-pptx-shape-hyperlink'
    _project_hyperlink_errors = None

try:
    from svg_to_pptx.drawingml.converter import (
        SvgNativeConversionError as _SvgNativeConversionError,
        collect_hidden_visuals as _collect_hidden_visuals,
        collect_unsupported_visuals as _collect_unsupported_visuals,
        preserved_native_text_body as _preserved_native_text_body,
    )
except ImportError:
    _SvgNativeConversionError = None
    _collect_hidden_visuals = None
    _collect_unsupported_visuals = None
    _preserved_native_text_body = None

try:
    from svg_to_pptx.pptx_syntax import normalize_language_attrs
except ImportError:
    normalize_language_attrs = None

try:
    from svg_to_pptx.drawingml.styles import parse_pattern_colors as _parse_pattern_colors
except ImportError:
    _parse_pattern_colors = None

try:
    from svg_to_pptx.drawingml.elements import (
        drawingml_text_frame_width_emu as _drawingml_text_frame_width_emu,
        empty_clip_path_reason as _empty_clip_path_reason,
        estimate_single_line_text_frame_width as _estimate_single_line_text_frame_width,
        project_image_errors as _project_image_errors,
        validate_single_line_text_run_advances as _validate_single_line_text_run_advances,
        validate_preset_geometry_metadata as _validate_preset_geometry_metadata,
    )
except ImportError:
    _drawingml_text_frame_width_emu = None
    _empty_clip_path_reason = None
    _estimate_single_line_text_frame_width = None
    _project_image_errors = None
    _validate_single_line_text_run_advances = None
    _validate_preset_geometry_metadata = None

try:
    from svg_to_pptx.drawingml.text_properties import (
        normalize_project_text_segments as _normalize_project_text_segments,
        parse_project_font_weight as _parse_project_font_weight,
        parse_project_text_anchor as _parse_project_text_anchor,
        resolve_project_xml_space as _resolve_project_xml_space,
        resolve_project_font_sizes as _resolve_project_font_sizes,
        resolve_project_letter_spacings as _resolve_project_letter_spacings,
    )
except ImportError:
    _normalize_project_text_segments = None
    _parse_project_font_weight = None
    _parse_project_text_anchor = None
    _resolve_project_xml_space = None
    _resolve_project_font_sizes = None
    _resolve_project_letter_spacings = None

try:
    from pptx_to_svg.preset_authoring import (
        AUTHORING_ATTR as _AUTHORING_ATTR,
        authored_preset_encoding as _authored_preset_encoding,
        validate_authored_preset_group as _validate_authored_preset_group,
        validate_authored_preset_tree as _validate_authored_preset_tree,
    )
except ImportError:
    _AUTHORING_ATTR = 'data-pptx-authoring'
    _authored_preset_encoding = None
    _validate_authored_preset_group = None
    _validate_authored_preset_tree = None

try:
    from pptx_shapes import (
        CONNECTOR_PRESET_TYPES as _CONNECTOR_PRESET_TYPES,
        resolve_preset_preview_hash as _resolve_preset_preview_hash,
        svg_preset_preview_fingerprint as _svg_preset_preview_fingerprint,
    )
except ImportError:
    _CONNECTOR_PRESET_TYPES = frozenset()
    _resolve_preset_preview_hash = None
    _svg_preset_preview_fingerprint = None

try:
    from svg_to_pptx.native_objects import (
        validate_native_object_marker as _validate_native_object_marker,
    )
except ImportError:
    _validate_native_object_marker = None

try:
    from svg_to_pptx.native_objects import (
        validate_native_object_marker_with_warnings as _validate_native_object_marker_with_warnings,
    )
except ImportError:
    _validate_native_object_marker_with_warnings = None

try:
    from svg_to_pptx.native_objects import (
        native_object_marker_warnings as _native_object_marker_warnings,
    )
except ImportError:
    _native_object_marker_warnings = None

try:
    from svg_to_pptx.native_objects import (
        INLINE_FORMULA_ATTR as _INLINE_FORMULA_ATTR,
        estimate_inline_formula_vertical_extent as _estimate_inline_formula_vertical_extent,
        native_fallback_kind as _native_fallback_kind,
        inline_formula_marker_errors as _inline_formula_marker_errors,
        native_marker_legacy_warnings as _native_marker_legacy_warnings,
        native_replacement_kind as _native_replacement_kind,
        native_replacement_status as _native_replacement_status,
        require_fresh_native_fallback as _require_fresh_native_fallback,
    )
except ImportError:
    _INLINE_FORMULA_ATTR = 'data-pptx-inline-formula'
    _estimate_inline_formula_vertical_extent = None
    _native_fallback_kind = None
    _inline_formula_marker_errors = None
    _native_marker_legacy_warnings = None
    _native_replacement_kind = None
    _native_replacement_status = None
    _require_fresh_native_fallback = None

try:
    from svg_to_pptx.native_objects.marker_status import (
        native_marker_release_block_reason as _native_marker_release_block_reason,
        native_marker_status_errors as _native_marker_status_errors,
    )
except ImportError:
    _native_marker_release_block_reason = None
    _native_marker_status_errors = None

try:
    from svg_to_pptx.semantic_markers import (
        SEMANTIC_ATTRS as _SEMANTIC_ATTRS,
        STRUCTURAL_ROLES as _STRUCTURAL_ROLES,
        is_static_page_frame as _is_static_page_frame,
        validate_semantic_markers as _validate_semantic_markers,
    )
except ImportError:
    _SEMANTIC_ATTRS = frozenset({
        'data-pptx-page-role',
        'data-pptx-role',
    })
    _STRUCTURAL_ROLES = frozenset({
        'background',
        'chrome',
        'decoration',
        'footer',
        'header',
        'logo',
        'page-number',
        'watermark',
    })
    _is_static_page_frame = None
    _validate_semantic_markers = None

try:
    from svg_to_pptx.use_expander import (
        UseExpansionError as _UseExpansionError,
        expand_local_use_references as _expand_local_use_references,
    )
except ImportError:
    _UseExpansionError = None
    _expand_local_use_references = None

try:
    from svg_to_pptx.tspan_flattener import (
        classify_paragraph_block as _classify_paragraph_block,
        flatten_positional_tspans as _flatten_positional_tspans,
        nested_positional_tspan_errors as _nested_positional_tspan_errors,
    )
except ImportError:
    _classify_paragraph_block = None
    _flatten_positional_tspans = None
    _nested_positional_tspan_errors = None

try:
    from svg_to_pptx.pptx_package.template_structure import (
        TemplateStructureError as _TemplateStructureError,
        _is_authored_preset_atom as _is_authored_preset_atom,
        load_pptx_structure_lock as _load_pptx_structure_lock,
        parse_optional_layout_slides as _parse_optional_layout_slides,
        parse_template_slide as _parse_template_structure_slide,
        parse_template_slides as _parse_template_structure_slides,
        _structure_subtree_signature as _structure_subtree_signature,
        template_lock_errors as _template_lock_errors,
        template_prototype_errors as _template_prototype_errors,
        validate_template_svg as _validate_template_structure_svg,
    )
except ImportError:
    _TemplateStructureError = None
    _is_authored_preset_atom = None
    _load_pptx_structure_lock = None
    _parse_optional_layout_slides = None
    _parse_template_structure_slide = None
    _parse_template_structure_slides = None
    _structure_subtree_signature = None
    _template_lock_errors = None
    _template_prototype_errors = None
    _validate_template_structure_svg = None

try:
    from svg_to_pptx.drawingml.theme_colors import (
        ThemeColorError as _ThemeColorError,
        load_theme_color_spec as _load_theme_color_spec,
    )
    from svg_to_pptx.drawingml.theme_fonts import (
        ThemeFontError as _ThemeFontError,
        load_master_text_style_spec as _load_master_text_style_spec,
        load_theme_font_spec as _load_theme_font_spec,
    )
except ImportError:
    _ThemeColorError = None
    _ThemeFontError = None
    _load_theme_color_spec = None
    _load_master_text_style_spec = None
    _load_theme_font_spec = None

try:
    from svg_finalize.embed_icons import (
        resolve_icon_path as _resolve_icon_path,
        suggest_icon_name as _suggest_icon_name,
    )
except ImportError:
    _resolve_icon_path = None
    _suggest_icon_name = None

try:
    from resource_paths import (
        SVG_WORK_DIR_NAMES as _SVG_WORK_DIR_NAMES,
        icon_dir_for_svg as _icon_dir_for_svg,
        project_root_for_svg_path as _project_root_for_svg_path,
        resolve_external_image_reference as _resolve_external_image_reference,
    )
except ImportError:
    _SVG_WORK_DIR_NAMES = frozenset()
    _icon_dir_for_svg = None
    _project_root_for_svg_path = None
    _resolve_external_image_reference = None


HEX_VALUE_RE = re.compile(
    r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})"
)

# Master/Layout preflight validation. Structured deck/layout-template projects
# are checked at authoring time; the exporter remains the final OOXML/package
# authority. Flat projects only receive the negative guard that rejects authored
# structure metadata. Template roster/placeholder checks always run. Current
# bundled templates opt in to complete structure validation through their
# native_structure_mode: structured declaration. Legacy template-mode packages
# fail closed; Create Template must author a new current-contract workspace.
_CHECK_PPTX_STRUCTURED_PROJECT = True

_SPEC_RELATIONSHIPS_LINE_RE = re.compile(
    r'^\s*-\s*\*\*Relationships(?:\s*\([^)]*\))?\*\*\s*[:：]\s*(?P<value>.*)$'
)
_CARRIED_RELATION_WORD_RE = re.compile(
    r'\b(?:order|link|parent|membership)\b', re.IGNORECASE
)


def count_carried_relationship_pages(design_spec_text: str) -> int:
    """Count §IX ``Relationships`` lines naming order, link, parent, or membership."""
    count = 0
    for line in design_spec_text.splitlines():
        match = _SPEC_RELATIONSHIPS_LINE_RE.match(line)
        if match and _CARRIED_RELATION_WORD_RE.search(match.group('value')):
            count += 1
    return count


_BARE_HEX_VALUE_RE = re.compile(
    r"(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})"
)
_NON_VISUAL_SVG_TAGS = frozenset({
    'defs',
    'desc',
    'metadata',
    'style',
    'title',
})
_BOUNDS_ATTR = 'data-pptx-bounds'
_MORPH_STAGING_ATTR = 'data-pptx-morph-staging'
_BOUNDS_OVERFLOW_TOLERANCE = 1.0
_BOUNDS_OVERFLOW_ERROR_RATIO = 0.05
_ROUNDTRIP_TEXT_CALIBRATION_CAP = 0.10
_PARAGRAPH_LINE_GAP_MIN_RATIO = 0.9
_PARAGRAPH_LINE_GAP_MAX_RATIO = 2.05
_PARAGRAPH_LINE_X_TOLERANCE = 0.5
_PARAGRAPH_LINE_MIN_TOTAL_CHARS = 12
_PARAGRAPH_LINE_MIN_LONGEST_CHARS = 8
_PARAGRAPH_LINE_TERMINATOR_RE = re.compile(r'[.!?。！？;；]["\'”’）)]*$')
_PARAGRAPH_LIST_MARKER_RE = re.compile(
    r'^\s*(?:[•·・▪◦‣]\s*|[-–—*]\s+|\d+[.)、]\s+|[（(]\d+[）)]\s*)\S+'
)
_LEGACY_PPTX_ATTRIBUTE_RENAMES = {
    'data-pptx-module-bounds': _BOUNDS_ATTR,
    'data-pptx-placeholder-bounds': _BOUNDS_ATTR,
    'data-pptx-placeholder-carrier': 'data-pptx-carrier',
    'data-pptx-placeholder-binding': 'data-pptx-binding',
    'data-pptx-placeholder-idx': 'data-pptx-idx',
}
_PPTX_ROOT_STRUCTURE_ATTRS = (
    'data-pptx-master',
    'data-pptx-master-name',
    'data-pptx-layout',
    'data-pptx-layout-name',
)
_PPTX_ROOT_VISIBILITY_ATTRS = (
    'data-pptx-show-master-shapes',
    'data-pptx-show-inherited-shapes',
)
_PPTX_STRUCTURE_ATTRS = frozenset({
    *_PPTX_ROOT_STRUCTURE_ATTRS,
    *_PPTX_ROOT_VISIBILITY_ATTRS,
    'data-pptx-layer',
    'data-pptx-layout-kind',
    'data-pptx-placeholder',
    'data-pptx-binding',
    'data-pptx-carrier',
    'data-pptx-idx',
})
_PPTX_PLACEHOLDER_DETAIL_ATTRS = frozenset({
    'data-pptx-binding',
    'data-pptx-idx',
})
_PPTX_STRUCTURE_SECTION_RE = re.compile(
    r"(?ms)^##[ \t]+pptx_structure[ \t]*\r?\n(.*?)(?=^##[ \t]+|\Z)"
)
_PPTX_STRUCTURE_MODE_RE = re.compile(
    r"(?m)^-[ \t]+mode[ \t]*:[ \t]*([^\s#]+)[ \t]*(?:#.*)?$"
)
def _compact_preset_ancestor_paint(
    root: ET.Element,
) -> list[tuple[str, tuple[str, ...]]]:
    """Return compact presets affected by compatible ancestor paint."""
    if (
        _authored_preset_encoding is None
        or _validate_authored_preset_group is None
    ):
        return []
    parents = {
        child: parent
        for parent in root.iter()
        for child in parent
    }
    affected: list[tuple[str, tuple[str, ...]]] = []
    for group in root.iter():
        if (
            _authored_preset_encoding(group) != 'compact'
            or _validate_authored_preset_group(group)
        ):
            continue
        relevant = {'opacity'}
        if group.get('fill') != 'none' and group.get('fill-opacity') is None:
            relevant.add('fill-opacity')
        if group.get('stroke') != 'none':
            for name in (
                'stroke-opacity',
                'stroke-dasharray',
                'stroke-linecap',
                'stroke-linejoin',
            ):
                if group.get(name) is None:
                    relevant.add(name)

        inherited: set[str] = set()
        ancestor = parents.get(group)
        while ancestor is not None:
            declarations = {
                name: ancestor.get(name) or ''
                for name in relevant
                if ancestor.get(name) is not None
            }
            for declaration in (ancestor.get('style') or '').split(';'):
                name, separator, value = declaration.partition(':')
                name = name.strip().lower()
                if separator and name in relevant:
                    declarations[name] = value.strip()
            for name, value in declarations.items():
                normalized = value.strip().lower()
                if name in {'opacity', 'fill-opacity', 'stroke-opacity'}:
                    try:
                        if float(normalized) == 1:
                            continue
                    except ValueError:
                        pass
                elif name == 'stroke-dasharray' and normalized == 'none':
                    continue
                elif name == 'stroke-linecap' and normalized == 'butt':
                    continue
                elif name == 'stroke-linejoin' and normalized == 'miter':
                    continue
                inherited.add(name)
            ancestor = parents.get(ancestor)
        if inherited:
            affected.append((
                group.get('id') or '(no id)',
                tuple(sorted(inherited)),
            ))
    return affected


def _declared_pptx_structure_mode(project_path: Path) -> str | None:
    """Return the explicitly locked SVG structure mode without a fallback."""
    lock_path = project_path / 'spec_lock.md'
    try:
        content = lock_path.read_text(encoding='utf-8')
    except OSError:
        return None
    section_match = _PPTX_STRUCTURE_SECTION_RE.search(content)
    if section_match is None:
        return None
    mode_match = _PPTX_STRUCTURE_MODE_RE.search(section_match.group(1))
    return mode_match.group(1).strip().lower() if mode_match else None


def _generated_theme_contract_errors(project_path: Path) -> List[str]:
    """Validate the current-project theme contract required by release export."""
    if (
        _ThemeColorError is None
        or _ThemeFontError is None
        or _load_theme_color_spec is None
        or _load_master_text_style_spec is None
        or _load_theme_font_spec is None
    ):
        return [
            "PowerPoint theme contract validation is unavailable because the "
            "theme loader modules could not be imported."
        ]
    try:
        theme_font_spec = _load_theme_font_spec(project_path)
        _load_master_text_style_spec(project_path)
        theme_color_spec = _load_theme_color_spec(project_path)
    except (_ThemeFontError, _ThemeColorError) as exc:
        return [str(exc)]

    missing: List[str] = []
    if theme_font_spec is None:
        missing.append("typography font_family/title_family/body_family")
    if theme_color_spec is None:
        missing.append("colors")
    if not missing:
        return []
    return [
        "spec_lock.md generated PowerPoint theme contract is missing: "
        + ", ".join(missing)
    ]


def _parse_positive_bounds(
    value: str,
) -> Tuple[float, float, float, float]:
    """Parse one positive x/y/width/height boundary."""
    raw_values = [item for item in re.split(r"[\s,]+", value.strip()) if item]
    if len(raw_values) != 4:
        raise ValueError("must contain exactly four numbers: x y width height")
    try:
        values = tuple(float(item) for item in raw_values)
    except ValueError as exc:
        raise ValueError("must contain only numeric values") from exc
    if not all(math.isfinite(item) for item in values):
        raise ValueError("must contain only finite values")
    if values[2] <= 0 or values[3] <= 0:
        raise ValueError("must use positive width and height")
    return values


def _placeholder_bounds_error(value: str) -> str | None:
    """Return a concise error for invalid design-zone bounds."""
    try:
        _parse_positive_bounds(value)
    except ValueError as exc:
        return str(exc)
    return None


def _local_pptx_structure_errors(
    root: ET.Element,
    svg_path: Path,
    *,
    require_structure: bool,
) -> List[str]:
    """Validate the authoring shape of the structured SVG contract."""
    errors: List[str] = []
    root_values = {
        attr: (root.get(attr) or '').strip()
        for attr in _PPTX_ROOT_STRUCTURE_ATTRS
    }
    has_root_structure = any(root_values.values())
    if require_structure or has_root_structure:
        missing = [attr for attr, value in root_values.items() if not value]
        if missing:
            errors.append(
                f"{svg_path.name}: structured SVG root is missing "
                + ', '.join(missing)
            )
    for attr in _PPTX_ROOT_VISIBILITY_ATTRS:
        raw = root.get(attr)
        if raw is not None and raw not in {'true', 'false'}:
            errors.append(
                f"{svg_path.name}: root {attr} must be exactly 'true' or 'false'"
            )

    parent_by_id = {
        id(child): parent
        for parent in root.iter()
        for child in list(parent)
    }
    for elem in root.iter():
        tag = elem.tag.rsplit('}', 1)[-1]
        element_id = elem.get('id') or f"<{tag}>"
        parent = parent_by_id.get(id(elem))

        if elem is not root:
            nested_root_attrs = [
                attr for attr in (
                    *_PPTX_ROOT_STRUCTURE_ATTRS,
                    *_PPTX_ROOT_VISIBILITY_ATTRS,
                )
                if elem.get(attr) is not None
            ]
            if nested_root_attrs:
                errors.append(
                    f"{svg_path.name}: {element_id} carries root-only metadata "
                    + ', '.join(nested_root_attrs)
                )

        if elem.get('data-pptx-layout-kind') is not None:
            errors.append(
                f"{svg_path.name}: data-pptx-layout-kind is a legacy distillation "
                "attribute; restore the page to the structured contract"
            )

        layer = (elem.get('data-pptx-layer') or '').strip().lower()
        placeholder = (elem.get('data-pptx-placeholder') or '').strip().lower()
        if layer in {'master', 'layout'}:
            if parent is not root:
                errors.append(
                    f"{svg_path.name}: {element_id} data-pptx-layer={layer!r} "
                    "must be a direct child of the root <svg>"
                )
            if tag == 'g' and not (
                _is_authored_preset_atom is not None
                and _is_authored_preset_atom(elem)
            ):
                errors.append(
                    f"{svg_path.name}: {element_id} is a <g> marked as {layer}; "
                    "Master/Layout fixed visuals must be root-level atomic elements"
                )
            if placeholder:
                errors.append(
                    f"{svg_path.name}: {element_id} cannot be both a fixed "
                    f"{layer} element and a placeholder slot"
                )

        detail_attrs = [
            attr for attr in _PPTX_PLACEHOLDER_DETAIL_ATTRS
            if elem.get(attr) is not None
        ]
        if detail_attrs and not placeholder:
            errors.append(
                f"{svg_path.name}: {element_id} uses placeholder detail metadata "
                "without data-pptx-placeholder"
            )

        if placeholder:
            if parent is not root:
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} must be a "
                    "direct child of the root <svg>"
                )
            if tag != 'g':
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} must be a "
                    "root-level <g>"
                )
            if not (elem.get('id') or '').strip():
                errors.append(
                    f"{svg_path.name}: every placeholder slot <g> requires a stable id"
                )
            wrapper_attrs = sorted(
                attr.rsplit('}', 1)[-1]
                for attr in elem.attrib
                if attr != 'id'
                and not attr.rsplit('}', 1)[-1].startswith('data-pptx-')
            )
            if wrapper_attrs:
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} is an "
                    "authoring boundary and may carry only id/data-pptx-*; remove "
                    + ', '.join(wrapper_attrs)
                )
            bounds = (elem.get('data-pptx-bounds') or '').strip()
            if not bounds:
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} requires "
                    "data-pptx-bounds"
                )
            else:
                bounds_error = _placeholder_bounds_error(bounds)
                if bounds_error:
                    errors.append(
                        f"{svg_path.name}: placeholder slot {element_id} bounds "
                        + bounds_error
                    )

            binding = (
                elem.get('data-pptx-binding') or 'carrier'
            ).strip().lower()
            if binding not in {'carrier', 'proxy'}:
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} has unknown "
                    f"binding {binding!r}; use carrier or proxy"
                )
            carrier_descendants = [
                child for child in elem.iter()
                if child is not elem
                and child.get('data-pptx-carrier') is not None
            ]
            visual_children = [
                child for child in list(elem)
                if child.tag.rsplit('}', 1)[-1] not in _NON_VISUAL_SVG_TAGS
            ]
            direct_carriers = [
                child for child in visual_children
                if (child.get('data-pptx-carrier') or '').strip().lower()
                == 'true'
            ]
            nested_carriers = [
                child for child in carrier_descendants
                if parent_by_id.get(id(child)) is not elem
            ]
            if nested_carriers:
                names = ', '.join(
                    child.get('id') or f"<{child.tag.rsplit('}', 1)[-1]}>"
                    for child in nested_carriers
                )
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} has nested "
                    f"carrier marker(s): {names}; the carrier must be a direct child"
                )
            if binding == 'carrier':
                if len(visual_children) != 1 or len(direct_carriers) != 1:
                    errors.append(
                        f"{svg_path.name}: placeholder slot {element_id} requires "
                        "exactly one visual direct child, marked "
                        "data-pptx-carrier=\"true\""
                    )
            if binding == 'proxy':
                if placeholder != 'object':
                    errors.append(
                        f"{svg_path.name}: proxy binding is allowed only for an "
                        f"object placeholder, not {placeholder!r}"
                    )
                if carrier_descendants:
                    errors.append(
                        f"{svg_path.name}: proxy placeholder slot {element_id} must "
                        "not declare a visible placeholder carrier"
                    )
                if not visual_children:
                    errors.append(
                        f"{svg_path.name}: proxy placeholder slot {element_id} must "
                        "contain visible Slide-local content"
                    )

        carrier_value = elem.get('data-pptx-carrier')
        if carrier_value is not None:
            if carrier_value.strip().lower() != 'true':
                errors.append(
                    f"{svg_path.name}: {element_id} "
                    "data-pptx-carrier must equal true"
                )
            if parent is None or not (
                parent.get('data-pptx-placeholder') or ''
            ).strip():
                errors.append(
                    f"{svg_path.name}: placeholder carrier {element_id} must be a "
                    "direct child of a root placeholder slot"
                )

        if tag in _NON_VISUAL_SVG_TAGS and (layer or placeholder):
            errors.append(
                f"{svg_path.name}: non-visual {element_id} cannot carry "
                "Master/Layout/placeholder ownership"
            )

    return list(dict.fromkeys(errors))


def _normalize_hex_rgb(value: str) -> str | None:
    """Normalize 3/4/6/8-digit HEX to alpha-free ``RRGGBB``."""
    if not HEX_VALUE_RE.fullmatch(value):
        return None
    color = value[1:]
    if len(color) in {3, 4}:
        color = ''.join(channel * 2 for channel in color)
    return color[:6].upper()


# Cheap numeric envelope for font-size role enforcement. Semantic role assignment
# is prompt-owned; Checker only verifies that a used value is close to at least
# one declared size anchor.
FONT_SIZE_ANCHOR_TOLERANCE_PX = 2.0
SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES = 2

# Oversampling alone does not imply distortion and is often harmless for small
# logos. Warn about downscaling only when the source also has material on-disk
# weight, because PPTX embeds the compressed source asset rather than raw pixels.
# 1280px=96 SVG px/in; at 1.5 device px/SVG px on 1080p, 2x becomes ~3x on-screen—visibly soft; smaller is not warned.
IMAGE_UPSCALE_WARN_RATIO = 2.0
IMAGE_DOWNSIZE_WARN_RATIO = 4.0
IMAGE_DOWNSIZE_WARN_MIN_BYTES = 1024 * 1024

_TEMPLATE_SPEC_NAME_RE = re.compile(
    r'design_spec\.(?P<kind>brand|style|layout|deck)\.(?P<id>[^/\\]+)\.md'
)


def _template_spec_paths(directory: Path) -> list[Path]:
    """Return every template Design Spec directly inside one directory.

    A library workspace keeps the exact ``design_spec.md`` because its parent
    directory already names the kind and id. A project workspace root shares one
    ``templates/`` across kinds, so it keeps ``design_spec.<kind>.<id>.md`` and
    may hold one spec per kind side by side.
    """
    if not directory.is_dir():
        return []
    exact = directory / 'design_spec.md'
    qualified = sorted(
        path
        for path in directory.glob('design_spec.*.md')
        if _TEMPLATE_SPEC_NAME_RE.fullmatch(path.name)
    )
    if exact.is_file():
        # Mixing both shapes hides one of them from every reader that stops at
        # the first match, so it is reported rather than silently resolved.
        return [exact] + qualified
    return qualified


def _spec_declared_kind(spec_path: Path) -> str | None:
    """Return one spec's kind, from its filename when it carries one."""
    match = _TEMPLATE_SPEC_NAME_RE.fullmatch(spec_path.name)
    if match is not None:
        return match.group('kind')
    return _design_spec_kind(spec_path)


def _roster_spec_paths(directory: Path) -> list[Path]:
    """Return every spec in one directory that owns an SVG roster."""
    roster = []
    for spec in _template_spec_paths(directory):
        match = _TEMPLATE_SPEC_NAME_RE.fullmatch(spec.name)
        if match is not None:
            if match.group('kind') in {'layout', 'deck'}:
                roster.append(spec)
        elif _design_spec_kind(spec) not in {'brand', 'style'}:
            roster.append(spec)
    return roster


def _roster_spec_path(directory: Path) -> Path | None:
    """Return the effective spec that owns this directory's SVG roster.

    A project root may carry both Layout and Deck. Layout owns reusable
    structure when present; Deck owns it only when no Layout is installed.
    """
    roster = _roster_spec_paths(directory)
    for spec in roster:
        if spec.name == 'design_spec.md':
            return spec
    for kind in ('layout', 'deck'):
        for spec in roster:
            if _spec_declared_kind(spec) == kind:
                return spec
    return None


def _design_spec_kind(spec_path: Path) -> str | None:
    """Return ``kind`` declared in Design Spec frontmatter.

    Lightweight detector that does not require PyYAML — scans only the
    frontmatter block (``---`` delimited).
    """
    try:
        text = spec_path.read_text(encoding='utf-8')
    except OSError:
        return None
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end == -1:
        return None
    fm_block = text[4:end]
    for line in fm_block.splitlines():
        stripped = line.strip()
        match = re.fullmatch(
            r'''kind\s*:\s*(?:(['"])(brand|style|layout|deck)\1|'''
            r'''(brand|style|layout|deck))'''
            r'''(?:\s+#.*)?\s*''',
            stripped,
        )
        if match:
            return match.group(2) or match.group(3)
    return None


def _declared_template_structure_mode(target_path: Path) -> str | None:
    """Return a template directory's explicit native structure mode."""
    directory = target_path.parent if target_path.is_file() else target_path
    spec_path = _roster_spec_path(directory)
    if spec_path is None:
        return None
    try:
        text = spec_path.read_text(encoding='utf-8')
    except OSError:
        return None
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end == -1:
        return None
    match = re.search(
        r'^native_structure_mode:\s*([A-Za-z0-9_-]+)\s*$',
        text[4:end],
        re.MULTILINE,
    )
    return match.group(1).lower() if match else None


def _declared_template_canvas_viewbox(target_path: Path) -> str | None:
    """Return a template design spec's locked root-canvas value."""
    directory = target_path.parent if target_path.is_file() else target_path
    spec_path = _roster_spec_path(directory)
    if spec_path is None:
        return None
    try:
        text = spec_path.read_text(encoding='utf-8')
    except OSError:
        return None
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end == -1:
        return None
    match = re.search(
        r'^canvas_viewbox:\s*["\']?([^"\'\r\n]+?)["\']?\s*$',
        text[4:end],
        re.MULTILINE,
    )
    return match.group(1).strip() if match else None


def _template_structure_checks_enabled(target_path: Path) -> bool:
    """Return whether positive structure checks apply to this template."""
    return _declared_template_structure_mode(target_path) == 'structured'


def _direct_defs_index(
    root: ET.Element,
) -> tuple[Dict[str, ET.Element], set[str]]:
    """Return direct ``<defs>`` children by id plus duplicate ids."""
    definitions: Dict[str, ET.Element] = {}
    duplicates: set[str] = set()
    for defs_elem in root.iter():
        if _local_name(defs_elem) != 'defs':
            continue
        for child in defs_elem:
            definition_id = (child.get('id') or '').strip()
            if not definition_id:
                continue
            if definition_id in definitions:
                duplicates.add(definition_id)
            definitions[definition_id] = child
    return definitions, duplicates


def _effective_presentation_value(
    elem: ET.Element,
    name: str,
    parent_by_id: Dict[int, ET.Element],
) -> str | None:
    """Resolve one inherited presentation property for validation."""
    current: ET.Element | None = elem
    while current is not None:
        style_values = (
            _parse_inline_style(current.get('style'))
            if _parse_inline_style is not None else {}
        )
        if name in style_values:
            return style_values[name]
        direct = current.get(name)
        if direct is not None:
            return direct
        current = parent_by_id.get(id(current))
    return None


def _parse_viewbox_values(viewbox: str) -> Tuple[float, float, float, float] | None:
    """Parse a root viewBox into four numeric values."""
    try:
        parsed = parse_project_viewbox(viewbox)
    except CanvasContractError:
        return None
    return 0.0, 0.0, float(parsed.width), float(parsed.height)


def _parse_placeholders_fallback(block: str) -> Dict[str, Tuple[str, ...]]:
    """Tiny YAML-free reader for the documented ``placeholders:`` shape.

    Used only when PyYAML is unavailable. Recognized lines (indentation-aware,
    two-space indent assumed):

    .. code-block:: yaml

        placeholders:
          01_cover: ["{{TITLE}}", "{{LOGO}}"]
          03_content: []
          03a_content_two_col:
            - "{{LEFT_TITLE}}"
            - "{{RIGHT_TITLE}}"

    Anything outside this minimal grammar is silently skipped — designers who
    rely on advanced YAML should install pyyaml.
    """
    out: Dict[str, Tuple[str, ...]] = {}
    inline_re = re.compile(
        r"^\s{2}([A-Za-z0-9_]+)\s*:\s*\[(.*)\]\s*$"
    )
    empty_re = re.compile(r"^\s{2}([A-Za-z0-9_]+)\s*:\s*\[\s*\]\s*$")
    block_header_re = re.compile(r"^\s{2}([A-Za-z0-9_]+)\s*:\s*$")
    item_re = re.compile(r'^\s{4}-\s*"?([^"]+)"?\s*$')

    in_section = False
    current_block_key: str | None = None
    current_items: List[str] = []

    def _flush_block() -> None:
        nonlocal current_block_key, current_items
        if current_block_key is not None:
            out[current_block_key] = tuple(current_items)
            current_block_key = None
            current_items = []

    for line in block.splitlines():
        if line.startswith("placeholders:"):
            in_section = True
            continue
        if not in_section:
            continue

        # End of section: dedent to a non-key line.
        if line and not line.startswith(" "):
            _flush_block()
            in_section = False
            continue

        if current_block_key is not None:
            m = item_re.match(line)
            if m:
                value = m.group(1).strip().strip('"').strip("'")
                if value:
                    current_items.append(value)
                continue
            # Block ended.
            _flush_block()

        if empty_re.match(line):
            key = empty_re.match(line).group(1)
            out[key] = ()
            continue

        m = inline_re.match(line)
        if m:
            key, raw = m.group(1), m.group(2)
            items = [p.strip().strip('"').strip("'") for p in raw.split(",")]
            out[key] = tuple(item for item in items if item)
            continue

        m = block_header_re.match(line)
        if m:
            current_block_key = m.group(1)
            current_items = []
            continue

    _flush_block()
    return out




__all__ = [
    name
    for name in dir()
    if not name.startswith("__")
]
