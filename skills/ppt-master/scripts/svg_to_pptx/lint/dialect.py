"""SVG dialect surface checks: viewBox, element support, geometry."""

from .common import *  # noqa: F401,F403


class DialectChecks:
    """Mixin: SVG dialect surface checks: viewBox, element support, geometry."""

    def _check_viewbox(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
        expected_format: str = None,
        *,
        expected_viewbox: str | None = None,
        expected_viewbox_label: str = "expected canvas",
    ):
        """Validate the root page canvas and its project-level locks."""
        viewbox = root.get('viewBox')
        try:
            parsed = parse_project_svg_root(
                root,
                context=svg_path.name,
            )
        except CanvasContractError as exc:
            result['errors'].append(str(exc))
            return
        assert viewbox is not None
        result['info']['viewbox'] = viewbox
        if viewbox != parsed.canonical or not parsed.has_integer_dimensions:
            if parsed.has_integer_dimensions:
                recommendation = f'write viewBox="{parsed.canonical}"'
            else:
                recommendation = (
                    "fractional dimensions are reserved for compatible imported "
                    "custom slide sizes; new authoring uses integer pixels"
                )
            result['warnings'].append(
                f"Compatible non-canonical root viewBox {viewbox!r}; {recommendation}."
            )

        contracts: list[tuple[str, str]] = []
        if expected_viewbox is not None:
            contracts.append((expected_viewbox_label, expected_viewbox))
        elif not self.template_mode:
            lock = self._get_spec_lock(svg_path)
            if lock is not None and 'canvas' in lock:
                locked_viewbox = lock.get('canvas', {}).get('viewBox')
                if not locked_viewbox:
                    result['errors'].append(
                        "spec_lock.md canvas section must declare viewBox"
                    )
                else:
                    contracts.append(("spec_lock canvas", locked_viewbox))

        if expected_format and expected_format in CANVAS_FORMATS:
            contracts.append((
                f"canvas format {expected_format!r}",
                CANVAS_FORMATS[expected_format]['viewbox'],
            ))
        elif expected_format:
            result['errors'].append(f"Unsupported canvas format: {expected_format}")

        seen_contracts: set[tuple[str, str]] = set()
        for label, raw_expected in contracts:
            contract_key = (label, raw_expected)
            if contract_key in seen_contracts:
                continue
            seen_contracts.add(contract_key)
            try:
                expected = parse_project_viewbox(
                    raw_expected,
                    context=f"{label} viewBox",
                )
            except CanvasContractError as exc:
                result['errors'].append(str(exc))
                continue
            if parsed != expected:
                result['errors'].append(
                    f"viewBox mismatch: {label} requires '{expected.canonical}', "
                    f"got '{parsed.canonical}'"
                )


    def _check_hyperlinks(self, root: ET.Element, result: Dict) -> None:
        """Validate the standard SVG anchor surface shared with export."""
        anchors = [
            elem for elem in root.iter()
            if _local_name(elem) == 'a'
        ]
        transports = [
            elem for elem in root.iter()
            if elem.get(_SHAPE_HYPERLINK_ATTR) is not None
        ]
        if not anchors and not transports:
            return
        result['info']['hyperlinks'] = len(anchors) + len(transports)
        if _project_hyperlink_errors is None:
            result['errors'].append(
                'Unable to import hyperlink validator; cannot verify SVG links'
            )
            return
        result['errors'].extend(
            f'Invalid SVG hyperlink: {error}'
            for error in _project_hyperlink_errors(
                root,
                slide_count=None if self.partial_roster else self._active_slide_count,
            )
        )


    def _check_nested_positional_tspans(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Reject nested baseline jumps that DrawingML runs cannot represent."""
        if _nested_positional_tspan_errors is None:
            return
        result['errors'].extend(_nested_positional_tspan_errors(root))


    @staticmethod
    def _is_hidden_element(
        element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Resolve ordinary suppression and empty clips before measuring visuals."""
        if _svg_hidden_reason is not None and _svg_hidden_reason(element, parent_by_id) is not None:
            return True
        if _empty_clip_path_reason is None or _project_definition_index is None:
            return False
        clipped = []
        root = element
        current = element
        while current is not None:
            if current.get('clip-path') is not None:
                clipped.append(current)
            root = current
            current = parent_by_id.get(id(current))
        if not clipped:
            return False
        definitions, _duplicates = _project_definition_index(root)
        return any(_empty_clip_path_reason(item, definitions, parent_by_id) for item in clipped)


    def _check_hidden_elements(self, root: ET.Element, result: Dict) -> None:
        """Advise which hidden SVG objects will be omitted during native export."""
        if _collect_hidden_visuals is None:
            return
        for element, reason in _collect_hidden_visuals(root):
            result['warnings'].append(
                f'Hidden element {_element_label(element)} will not be exported '
                f'({reason}, including inherited state); advisory only'
            )


    def _check_shape_coordinate_ranges(self, root: ET.Element, result: Dict) -> None:
        """Check ordinary shape frames with the exporter's OOXML range validator."""
        if _rect_to_dml_xfrm is None or _parse_project_geometry_length is None:
            return
        parents = {id(child): parent for parent in root.iter() for child in parent}

        def visit(element: ET.Element) -> None:
            tag = _local_name(element)
            if tag in {'defs', 'metadata', 'title', 'desc', 'style'}:
                return
            if (
                tag in {'rect', 'image', 'circle', 'ellipse'}
                and element.get('data-pptx-frame') is None
                and not self._is_hidden_element(element, parents)
            ):
                styles = _parse_inline_style(element.get('style')) if _parse_inline_style else {}

                def length(name: str) -> float:
                    return _parse_project_geometry_length(styles.get(name, element.get(name, '0')), name)

                try:
                    if tag in {'rect', 'image'}:
                        frame = (length('x'), length('y'), length('width'), length('height'))
                    else:
                        rx = length('r' if tag == 'circle' else 'rx')
                        ry = rx if tag == 'circle' else length('ry')
                        frame = (length('cx') - rx, length('cy') - ry, rx * 2, ry * 2)
                    matrix = self._accumulated_transform_matrix(element, parents)
                    if matrix is not None and frame[2] > 0 and frame[3] > 0:
                        _rect_to_dml_xfrm(*frame, matrix)
                except ValueError as exc:
                    # Other geometry/transform grammar errors have their own checks.
                    if 'OOXML' in str(exc):
                        result['errors'].append(
                            f'{_element_label(element)}: {exc}; reduce the shape '
                            'coordinates or dimensions before export'
                        )
            for child in element:
                visit(child)

        visit(root)


    @staticmethod
    def _has_zero_opacity(
        element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Return whether an element or ancestor has zero effective opacity."""
        current: ET.Element | None = element
        while current is not None:
            style_values = (
                _parse_inline_style(current.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            raw = style_values.get('opacity')
            if raw is None:
                raw = current.get('opacity')
            if raw is not None:
                value = raw.strip()
                try:
                    opacity = (
                        float(value[:-1]) / 100
                        if value.endswith('%')
                        else float(value)
                    )
                except ValueError:
                    pass
                else:
                    if opacity <= 0:
                        return True
            current = parent_by_id.get(id(current))
        return False


    def _check_unsupported_visual_elements(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Reject authored visual elements with no native converter dispatch."""
        if _collect_unsupported_visuals is None:
            result['errors'].append(
                "Unable to import native visual-element preflight; "
                "cannot verify SVG element support"
            )
            return
        if _expand_local_use_references is None or _UseExpansionError is None:
            result['errors'].append(
                "Unable to import local <use> expansion; "
                "cannot verify SVG element support"
            )
            return

        expanded_root = copy.deepcopy(root)
        try:
            _expand_local_use_references(expanded_root)
        except _UseExpansionError:
            # _check_forbidden_elements already reports the actionable
            # local-reference validation error.
            return

        unsupported = _collect_unsupported_visuals(
            expanded_root,
            allow_data_icon_use=True,
        )
        if not unsupported:
            return

        preview = '; '.join(unsupported[:8])
        suffix = '' if len(unsupported) <= 8 else f'; +{len(unsupported) - 8} more'
        result['errors'].append(
            f"Unsupported visual SVG element(s) for native PPTX export: "
            f"{preview}{suffix}"
        )


    def _check_preset_geometry_metadata(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Validate round-trip preset metadata with the exporter's parser."""
        marked = [
            elem
            for elem in root.iter()
            if (
                elem.get('data-pptx-prst') is not None
                or elem.get('data-pptx-frame') is not None
                or elem.get('data-pptx-geometry-status') is not None
                or elem.get('data-pptx-geometry-reason') is not None
                or elem.get('data-pptx-geometry-kind') is not None
                or elem.get('data-pptx-custgeom') is not None
                or elem.get('data-pptx-preview-sha256') is not None
                or elem.get('data-pptx-shape-id') is not None
                or elem.get('data-pptx-shape-scope') is not None
                or elem.get('data-pptx-shape-style') is not None
                or elem.get(_AUTHORING_ATTR) is not None
                or any(attr.startswith('data-pptx-av-') for attr in elem.attrib)
            )
        ]
        if not marked:
            return
        if _validate_preset_geometry_metadata is None:
            result['errors'].append(
                'Unable to import PPTX preset metadata validator; '
                'cannot verify native shape restoration'
            )
            return

        issues = set()
        for elem in marked:
            tag = _local_name(elem)
            elem_id = elem.get('id')
            label = f'<{tag} id="{elem_id}">' if elem_id else f'<{tag}>'
            for error in _validate_preset_geometry_metadata(elem):
                issues.add(f'{label} has invalid PPTX shape metadata: {error}')
        if _validate_authored_preset_tree is None:
            if any(
                elem.get(_AUTHORING_ATTR) is not None
                for elem in root.iter()
            ):
                issues.add(
                    'Unable to import authored PPTX preset validator'
                )
        else:
            for error in _validate_authored_preset_tree(root):
                issues.add(f'Invalid authored PPTX preset: {error}')
        if (
            _svg_preset_preview_fingerprint is None
            or _resolve_preset_preview_hash is None
        ):
            issues.add('Unable to import PPTX preset preview fingerprint validator')
        else:
            for elem in root.iter():
                if (
                    _local_name(elem) != 'g'
                    or elem.get('data-pptx-object') not in {'shape', 'connector'}
                    or elem.get('data-pptx-prst') is None
                ):
                    continue
                try:
                    expected = _resolve_preset_preview_hash(elem)
                except ValueError as exc:
                    elem_id = elem.get('id') or '(no id)'
                    issues.add(
                        f'<g id="{elem_id}"> has an invalid PPTX preset '
                        f'preview contract: {exc}'
                    )
                    continue
                if expected is None:
                    continue
                actual = _svg_preset_preview_fingerprint(elem)
                if actual != expected:
                    elem_id = elem.get('id') or '(no id)'
                    issues.add(
                        f'<g id="{elem_id}"> has a stale PPTX preset preview; '
                        'update the native carrier or restore the generated detail paths'
                    )
        result['errors'].extend(sorted(issues))
        if (
            _authored_preset_encoding is not None
            and _validate_authored_preset_group is not None
        ):
            expanded = [
                elem.get('id') or '(no id)'
                for elem in root.iter()
                if _authored_preset_encoding(elem) == 'expanded'
                and not _validate_authored_preset_group(elem)
            ]
            if expanded:
                examples = ', '.join(expanded[:3])
                suffix = '' if len(expanded) <= 3 else f', +{len(expanded) - 3} more'
                result['warnings'].append(
                    'Compatible expanded authored-preset fragment(s) detected '
                    f'({len(expanded)}: {examples}{suffix}). New project-authored '
                    'pages and templates use the compact helper form; the '
                    'expanded carrier/preview form remains readable for compatibility. '
                    'No change is required while it remains ordinary Slide-local input.'
                )
        inherited_paint = _compact_preset_ancestor_paint(root)
        if inherited_paint:
            examples = ', '.join(
                f'{element_id} ({"/".join(properties)})'
                for element_id, properties in inherited_paint[:3]
            )
            suffix = (
                ''
                if len(inherited_paint) <= 3
                else f', +{len(inherited_paint) - 3} more'
            )
            result['warnings'].append(
                'Compact authored preset(s) use compatible ancestor paint or '
                f'opacity ({examples}{suffix}). Canonical page/template authoring '
                'keeps preset paint local and reruns the helper with channel alpha; '
                'export remains supported.'
            )


    def _check_preset_geometry_transforms(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Reject preset transforms that DrawingML cannot represent exactly."""
        helpers = (
            _IDENTITY_MATRIX,
            _matrix_multiply,
            _parse_transform_matrix,
            _rect_to_dml_xfrm,
            _validate_dml_shape_matrix,
        )
        if any(helper is None for helper in helpers):
            return

        relevant: set[ET.Element] = set()

        def mark_relevant(element: ET.Element) -> bool:
            found = element.get('data-pptx-prst') is not None
            for child in element:
                found = mark_relevant(child) or found
            if found:
                relevant.add(element)
            return found

        mark_relevant(root)
        issues = set()

        def visit(element: ET.Element, parent_matrix) -> None:
            if element not in relevant:
                return
            matrix = parent_matrix
            transform = element.get('transform')
            if transform:
                try:
                    local_matrix = _parse_transform_matrix(transform)
                    matrix = _matrix_multiply(parent_matrix, local_matrix)
                except ValueError as exc:
                    issues.add(
                        f'<{_local_name(element)}> has invalid preset '
                        f'transform: {exc}'
                    )
                    return
            if element.get('data-pptx-prst') is not None:
                try:
                    raw_frame = element.get('data-pptx-frame')
                    if raw_frame:
                        frame = tuple(
                            float(part)
                            for part in re.split(r'[\s,]+', raw_frame.strip())
                        )
                        if len(frame) != 4:
                            raise ValueError(
                                'data-pptx-frame must contain four numbers'
                            )
                        preset = element.get('data-pptx-prst') or ''
                        _rect_to_dml_xfrm(
                            frame[0],
                            frame[1],
                            frame[2],
                            frame[3],
                            matrix,
                            preserve_degenerate_axes=(
                                element.get('data-pptx-object') == 'connector'
                                or preset in _CONNECTOR_PRESET_TYPES
                            ),
                        )
                    else:
                        _validate_dml_shape_matrix(matrix)
                except ValueError as exc:
                    elem_id = element.get('id') or '(no id)'
                    issues.add(
                        f'<{_local_name(element)} id="{elem_id}"> has '
                        f'unsupported preset transform: {exc}'
                    )
            for child in element:
                visit(child, matrix)

        visit(root, _IDENTITY_MATRIX)
        result['errors'].extend(sorted(issues))


    @staticmethod
    def _is_full_canvas_root_rect(
        root: ET.Element,
        element: ET.Element,
    ) -> bool:
        """Return whether one direct rect is the ordinary full-page backdrop."""
        if (
            _local_name(element) != 'rect'
            or _parse_project_geometry_length is None
            or any(
                element.get(attribute)
                for attribute in ('transform', 'filter', 'clip-path')
            )
        ):
            return False
        viewbox = _parse_viewbox_values(root.get('viewBox') or '')
        if viewbox is None:
            return False

        parent_by_id = {id(element): root}

        def inherited(name: str, default: str) -> str:
            return _effective_presentation_value(
                element,
                name,
                parent_by_id,
            ) or default

        try:
            values = {
                name: _parse_project_geometry_length(
                    element.get(name) or '0',
                    name,
                )
                for name in ('x', 'y', 'width', 'height', 'rx', 'ry')
            }
            stroke_width = _parse_project_geometry_length(
                inherited('stroke-width', '1'),
                'stroke-width',
            )
            stroke_opacity = (
                _parse_project_opacity(inherited('stroke-opacity', '1'))
                if _parse_project_opacity is not None else 1.0
            )
        except ValueError:
            return False
        fill = inherited('fill', '#000000').strip().lower()
        stroke = inherited('stroke', 'none').strip().lower()
        if (
            fill == 'none'
            or (
                stroke != 'none'
                and stroke_width > 0
                and stroke_opacity > 0
            )
        ):
            return False

        view_x, view_y, view_width, view_height = viewbox
        tolerance = 0.5
        return (
            values['rx'] == 0
            and values['ry'] == 0
            and abs(values['x'] - view_x) <= tolerance
            and abs(values['y'] - view_y) <= tolerance
            and abs(values['width'] - view_width) <= tolerance
            and abs(values['height'] - view_height) <= tolerance
        )


    def _check_pattern_fills(self, root: ET.Element, result: Dict):
        """Audit <pattern> defs that drive PPTX <a:pattFill> output.

        svg_to_pptx maps <pattern fill> to native <a:pattFill prst="...">. The
        preset name comes from `data-pptx-pattern` (e.g. `lgGrid` / `smGrid` /
        `dkUpDiag`). Patterns marked with `data-pptx-text-image-fill` instead
        map to run-level <a:blipFill> and are validated by converter preflight.
        Two preset-pattern failure modes are worth catching pre-export:

        1. Missing annotation → the converter compatibility fallback chooses
           `ltUpDiag` (diagonal stripes), which is not an authoring contract.
        2. Invalid preset name → PPTX schema rejects the file; PowerPoint
           opens it with "needs to be repaired". OOXML
           `ST_PresetPatternVal` is a closed enum — only the names in
           `_OOXML_PATTERN_PRESETS` are legal. Inventing `ltGrid` (no such
           value) is the canonical mistake; the only grids are `smGrid` /
           `lgGrid` / `dotGrid`.
        """
        definitions, _duplicates = _direct_defs_index(root)
        referenced_patterns: set[str] = set()
        for elem in root.iter():
            style_values = (
                _parse_inline_style(elem.get('style'))
                if _parse_inline_style is not None else {}
            )
            fill = style_values.get('fill') or elem.get('fill')
            match = re.fullmatch(r'url\(#([^)]+)\)', (fill or '').strip())
            if match is None:
                continue
            definition = definitions.get(match.group(1))
            if definition is not None and _local_name(definition) == 'pattern':
                referenced_patterns.add(match.group(1))

        for pattern in (
            elem for elem in root.iter()
            if _local_name(elem) == 'pattern'
        ):
            pat_id = pattern.get('id', '<unnamed>')
            prst = pattern.get('data-pptx-pattern')
            if pattern.get(_TEXT_IMAGE_FILL_ATTR) is not None:
                continue
            if pat_id in referenced_patterns and not prst:
                result['warnings'].append(
                    f"Fidelity warning: <pattern id=\"{pat_id}\"> has no "
                    "data-pptx-pattern attribute, so the converter will use its "
                    "compatible `ltUpDiag` fallback. Generated SVG should declare a valid "
                    "data-pptx-pattern to make the intended preset explicit; "
                    "set data-pptx-fg/data-pptx-bg or matching child paints "
                    "when explicit pattern colors are required. No change is "
                    "required for export."
                )
            if pat_id in referenced_patterns and pattern.get('patternTransform'):
                result['errors'].append(
                    f"<pattern id=\"{pat_id}\"> cannot use patternTransform; "
                    "the native preset mapping does not preserve custom tile transforms"
                )
            if prst not in self._OOXML_PATTERN_PRESETS:
                if not prst:
                    continue
                result['errors'].append(
                    f"<pattern id=\"{pat_id}\"> uses data-pptx-pattern=\"{prst}\" "
                    "which is not in OOXML ST_PresetPatternVal — exported PPTX "
                    "will fail schema validation ('needs to be repaired'). "
                    "Use one of: smGrid / lgGrid / dotGrid (grids), "
                    "ltUpDiag / dkUpDiag / cross / diagCross / weave / plaid / "
                    "horzBrick (others); see references/native-data-interface.md §1 "
                    "for the full authoring enum."
                )
            elif prst and _parse_pattern_colors is not None:
                try:
                    _parse_pattern_colors(pattern)
                except ValueError as exc:
                    result['errors'].append(str(exc))


    @staticmethod
    def _check_legacy_pptx_attributes(
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Reject superseded long-form authoring attributes."""
        for element in root.iter():
            for legacy, canonical in _LEGACY_PPTX_ATTRIBUTE_RENAMES.items():
                if element.get(legacy) is None:
                    continue
                result['errors'].append(
                    f'{svg_path.name}: {_element_label(element)} uses legacy '
                    f'{legacy}; rename it to {canonical}'
                )


