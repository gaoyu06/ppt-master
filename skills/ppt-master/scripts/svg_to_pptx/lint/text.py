"""Font, text-run, and paragraph contract checks."""

from .common import *  # noqa: F401,F403


class TextChecks:
    """Mixin: Font, text-run, and paragraph contract checks."""

    def _check_fonts(
        self,
        content: str,
        result: Dict,
        *,
        font_families: List[str] | None = None,
    ):
        """Check font usage.

        PPTX stores concrete typefaces per run with no CSS fallback. The
        converter resolves each SVG font stack to exported latin / EA typefaces;
        validate those exported values rather than the visual-preview tail.
        """
        font_matches = (
            self._font_family_values(content)
            if font_families is None
            else font_families
        )

        if not font_matches:
            return

        result['info']['fonts'] = sorted(set(font_matches))
        if _unsafe_exported_font_faces is None:
            result['warnings'].append(
                "Unable to import svg_to_pptx font resolver; skipped exported-font safety check"
            )
            return

        for font_family in font_matches:
            unsafe = [
                f"{role}={family}"
                for role, family in _unsafe_exported_font_faces(font_family).items()
            ]
            if unsafe:
                result['warnings'].append(
                    "Font stack exports non-PPT-safe typeface(s) to PPTX "
                    f"({', '.join(unsafe)}): {font_family}"
                )
                break


    @staticmethod
    def _font_family_values(content: str) -> List[str]:
        """Extract SVG font-family values from attributes and inline styles."""
        return TextChecks._svg_property_values(content, 'font-family')


    @staticmethod
    def _svg_property_values(content: str, property_name: str) -> List[str]:
        """Extract a SVG property from direct attributes and inline styles."""
        values: List[str] = []
        attr_re = re.compile(
            rf'\b{re.escape(property_name)}\s*=\s*(["\'])(.*?)\1',
            re.IGNORECASE | re.DOTALL,
        )
        for match in attr_re.finditer(content):
            values.append(html.unescape(match.group(2)).strip())

        for match in re.finditer(r'\bstyle\s*=\s*(["\'])(.*?)\1', content, re.IGNORECASE | re.DOTALL):
            style_value = html.unescape(match.group(2))
            for part in style_value.split(';'):
                if ':' not in part:
                    continue
                name, value = part.split(':', 1)
                if name.strip().lower() == property_name.lower():
                    values.append(value.strip())
        return [value for value in values if value]


    def _check_text_elements(self, content: str, root: ET.Element, result: Dict):
        """Check text elements and wrapping methods"""
        # Count text and tspan elements
        text_count = content.count('<text')
        tspan_count = content.count('<tspan')

        result['info']['text_elements'] = text_count
        result['info']['tspan_elements'] = tspan_count

        self._check_module_bounds_contract(root, result)
        self._check_text_output_geometry(root, result)
        self._check_text_bounds(root, result)
        self._check_fragmented_paragraph_text(root, result)
        self._check_unmergeable_leading_text(root, result)
        self._check_nested_positional_tspans(root, result)


    @classmethod
    def _single_line_text_runs(
        cls,
        text_el: ET.Element,
    ) -> List[Tuple[ET.Element, str]] | None:
        """Return normalized inline runs, or ``None`` for positioned text."""
        raw_runs = cls._inline_text_segments(text_el, 'default')
        if raw_runs is None:
            return None
        return cls._normalize_source_text_runs(raw_runs)


    @classmethod
    def _inline_text_segments(
        cls,
        container: ET.Element,
        inherited_xml_space: str,
        *,
        include_container_dx: bool = False,
    ) -> List[Tuple[ET.Element, str, str]] | None:
        """Collect inline text and empty dx markers, rejecting baseline jumps."""
        if (
            _normalize_project_text_segments is None
            or _resolve_project_xml_space is None
            or _parse_svg_length is None
        ):
            return None
        raw_runs: List[Tuple[ET.Element, str, str]] = []

        def append_run(owner: ET.Element, raw: str, xml_space: str) -> None:
            if raw:
                raw_runs.append((owner, xml_space, raw))

        def collect(element: ET.Element, inherited: str) -> bool:
            try:
                xml_space = _resolve_project_xml_space(
                    element,
                    inherited,
                )
            except ValueError:
                return False
            if (
                cls._is_tspan(element)
                and element.get('dx') is not None
                and (element is not container or include_container_dx)
            ):
                try:
                    dx = _parse_svg_length(element.get('dx'))
                except ValueError:
                    return False
                if dx:
                    raw_runs.append((element, xml_space, ''))
            if element.text:
                append_run(element, element.text, xml_space)
            for child in list(element):
                # An inline hyperlink (``<a>`` wrapping ``<tspan>`` runs, the
                # form native-hyperlinks.md requires) is a transparent style
                # container: its runs are measured like any other inline run.
                if not (cls._is_tspan(child) or _local_name(child) == 'a'):
                    return False
                if any(child.get(name) is not None for name in ('x', 'y', 'dy')):
                    return False
                if child.get('dx') is not None and not cls._is_tspan(child):
                    return False
                if any(
                    name.startswith('data-paragraph-')
                    for name in child.attrib
                ):
                    return False
                if not collect(child, xml_space):
                    return False
                if child.tail:
                    append_run(element, child.tail, xml_space)
            return True

        if not collect(container, inherited_xml_space):
            return None
        return raw_runs


    @staticmethod
    def _normalize_source_text_runs(
        raw_runs: List[Tuple[ET.Element, str, str]],
    ) -> List[Tuple[ET.Element, str]]:
        """Normalize collected segments while retaining their style owner."""
        normalized = dict(_normalize_project_text_segments([
            (xml_space, raw)
            for _owner, xml_space, raw in raw_runs
        ]))
        return [
            (owner, normalized.get(index, ''))
            for index, (owner, _xml_space, raw) in enumerate(raw_runs)
            if not raw or index in normalized
        ]


    @classmethod
    def _paragraph_line_text_runs(
        cls,
        text_el: ET.Element,
        line_group: List[ET.Element],
        synthetic_first: ET.Element | None,
    ) -> List[Tuple[ET.Element, str]] | None:
        """Return one classified visual line's normalized source runs."""
        if (
            _normalize_project_text_segments is None
            or _resolve_project_xml_space is None
        ):
            return None
        try:
            parent_xml_space = _resolve_project_xml_space(text_el, 'default')
        except ValueError:
            return None

        raw_runs: List[Tuple[ET.Element, str, str]] = []
        for member in line_group:
            if member is synthetic_first:
                if member.text:
                    raw_runs.append((text_el, parent_xml_space, member.text))
                continue
            member_runs = cls._inline_text_segments(
                member,
                parent_xml_space,
                include_container_dx=member is not line_group[0],
            )
            if member_runs is None:
                return None
            raw_runs.extend(member_runs)
            if member.tail:
                raw_runs.append((text_el, parent_xml_space, member.tail))
        return cls._normalize_source_text_runs(raw_runs)


    @staticmethod
    def _unchanged_txbody_group_ids(
        root: ET.Element,
    ) -> set[int]:
        """Return imported shape groups whose original text body will survive."""
        if _preserved_native_text_body is None:
            return set()
        unchanged: set[int] = set()
        for group in root.iter(f'{{{SVG_NS}}}g'):
            try:
                if _preserved_native_text_body(
                    group,
                    trust_runtime_snapshot=False,
                ) is not None:
                    unchanged.add(id(group))
            except _SvgNativeConversionError:
                # The dedicated txBody contract check owns the diagnostic.
                continue
        return unchanged


    @staticmethod
    def _check_preserved_txbody_contract(
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Validate imported txBody payloads independently of text geometry."""
        if _preserved_native_text_body is None:
            return
        errors: set[str] = set()
        for group in root.iter(f'{{{SVG_NS}}}g'):
            try:
                _preserved_native_text_body(
                    group,
                    trust_runtime_snapshot=False,
                )
            except _SvgNativeConversionError as exc:
                errors.add(
                    f'{_element_label(group)} cannot preserve source '
                    f'txBody: {exc}'
                )
        result['errors'].extend(sorted(errors))


    @staticmethod
    def _has_ancestor_id(
        elem: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        ancestor_ids: set[int],
    ) -> bool:
        current = parent_by_id.get(id(elem))
        while current is not None:
            if id(current) in ancestor_ids:
                return True
            current = parent_by_id.get(id(current))
        return False


    @classmethod
    def _resolved_single_line_text_runs(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Dict] | None:
        """Resolve the same run metrics used by generated text-frame sizing."""
        source_runs = cls._single_line_text_runs(text_el)
        if source_runs is None:
            return None
        return cls._resolved_text_runs(
            source_runs,
            parent_by_id,
            font_sizes,
            letter_spacings,
        )


    @classmethod
    def _resolved_text_runs(
        cls,
        source_runs: List[Tuple[ET.Element, str]],
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Dict]:
        """Resolve run metrics shared by single and classified text lines."""
        resolved: List[Dict] = []
        for owner, text in source_runs:
            raw_weight = (
                _effective_presentation_value(
                    owner,
                    'font-weight',
                    parent_by_id,
                )
                or 'normal'
            ).strip().lower()
            weight = _parse_project_font_weight(raw_weight).canonical
            family = (
                _effective_presentation_value(
                    owner,
                    'font-family',
                    parent_by_id,
                )
                or ''
            )
            opacity_chain: List[str] = []
            current: ET.Element | None = owner
            while current is not None:
                style_values = (
                    _parse_inline_style(current.get('style'))
                    if _parse_inline_style is not None else {}
                )
                raw_opacity = style_values.get('opacity')
                if raw_opacity is None:
                    raw_opacity = current.get('opacity')
                if raw_opacity is not None:
                    opacity_chain.append(raw_opacity.strip())
                current = parent_by_id.get(id(current))
            resolved.append({
                'owner': owner,
                'text': text,
                'font_size': font_sizes[id(owner)],
                'font_weight': weight,
                'font_family': family,
                'letter_spacing': letter_spacings[id(owner)],
                'font_style': _effective_presentation_value(
                    owner,
                    'font-style',
                    parent_by_id,
                ) or 'normal',
                'text_decoration': _effective_presentation_value(
                    owner,
                    'text-decoration',
                    parent_by_id,
                ) or 'none',
                'fill_raw': _effective_presentation_value(
                    owner,
                    'fill',
                    parent_by_id,
                ) or '#000000',
                'fill_opacity': _effective_presentation_value(
                    owner,
                    'fill-opacity',
                    parent_by_id,
                ) or '1',
                'stroke_raw': _effective_presentation_value(
                    owner,
                    'stroke',
                    parent_by_id,
                ) or 'none',
                'stroke_width': _effective_presentation_value(
                    owner,
                    'stroke-width',
                    parent_by_id,
                ) or '1',
                'stroke_opacity': _effective_presentation_value(
                    owner,
                    'stroke-opacity',
                    parent_by_id,
                ) or '1',
                'opacity_chain': tuple(reversed(opacity_chain)),
                'inline_formula': owner.get(_INLINE_FORMULA_ATTR),
            })
            if not text:
                resolved[-1]['_inline_dx'] = _parse_svg_length(
                    owner.get('dx'),
                    font_size=font_sizes[id(owner)],
                )
                resolved[-1]['inline_formula'] = None
        return cls._coalesce_checker_text_runs(resolved)


    @staticmethod
    def _coalesce_checker_text_runs(runs: List[Dict]) -> List[Dict]:
        """Join only runs whose resolved source styles are provably equal."""
        if _detect_text_lang is None:
            return runs
        style_keys = (
            'font_size',
            'font_weight',
            'font_family',
            'letter_spacing',
            'font_style',
            'text_decoration',
            'fill_raw',
            'fill_opacity',
            'stroke_raw',
            'stroke_width',
            'stroke_opacity',
            'opacity_chain',
        )

        def signature(run: Dict) -> Tuple:
            return (
                _detect_text_lang(str(run.get('text', ''))),
                *(run.get(key) for key in style_keys),
            )

        merged: List[Dict] = []
        previous_signature: Tuple | None = None
        for run in runs:
            if run.get('inline_formula') is not None or '_inline_dx' in run:
                merged.append(run)
                previous_signature = None
                continue
            current_signature = signature(run)
            if merged and current_signature == previous_signature:
                candidate = {
                    **merged[-1],
                    'text': (
                        str(merged[-1].get('text', ''))
                        + str(run.get('text', ''))
                    ),
                }
                candidate_signature = signature(candidate)
                if candidate_signature == previous_signature:
                    merged[-1] = candidate
                    previous_signature = candidate_signature
                    continue
            merged.append(run)
            previous_signature = current_signature
        return merged


    def _check_unmergeable_leading_text(self, root: ET.Element, result: Dict) -> None:
        """Warn when leading text cannot be normalized into one PPT text frame."""
        risky = []
        for text_el in root.iter(f'{{{SVG_NS}}}text'):
            if not (text_el.text or "").strip():
                continue
            children = list(text_el)
            if not any(self._is_line_tspan(child) for child in children):
                continue

            reason = self._leading_text_normalizer_reject_reason(text_el)
            if reason is not None:
                risky.append(reason)

        if risky:
            sample = '; '.join(risky[:3])
            suffix = '' if len(risky) <= 3 else f"; +{len(risky) - 3} more"
            result['warnings'].append(
                "Detected multi-line <text> with leading direct text that cannot "
                f"be normalized into one PPT text frame ({sample}{suffix})"
            )


    def _check_fragmented_paragraph_text(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Warn on high-confidence prose lines split into sibling text frames."""
        helpers = (
            _parse_project_geometry_length,
            _resolve_project_font_sizes,
        )
        if any(helper is None for helper in helpers):
            return
        try:
            font_sizes = _resolve_project_font_sizes(root)
        except ValueError:
            return

        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        unchanged_groups = self._unchanged_txbody_group_ids(root)
        style_properties = (
            'fill',
            'fill-opacity',
            'font-family',
            'font-style',
            'font-weight',
            'letter-spacing',
            'opacity',
            'stroke',
            'stroke-opacity',
            'stroke-width',
            'text-decoration',
        )

        def line_record(element: ET.Element) -> Dict | None:
            if (
                _local_name(element) != 'text'
                or list(element)
                or element.get('x') is None
                or element.get('y') is None
                or any(element.get(name) is not None for name in ('dx', 'dy'))
                or element.get('transform') is not None
                or self._is_hidden_element(element, parent_by_id)
                or self._has_ancestor_id(
                    element,
                    parent_by_id,
                    unchanged_groups,
                )
            ):
                return None
            text = (element.text or '').strip()
            compact_text = re.sub(r'\s+', '', text)
            if (
                not compact_text
                or ('{{' in text and '}}' in text)
                or _PARAGRAPH_LIST_MARKER_RE.match(text)
            ):
                return None
            anchor = (
                _effective_presentation_value(
                    element,
                    'text-anchor',
                    parent_by_id,
                )
                or 'start'
            ).strip().lower()
            if anchor != 'start':
                return None
            try:
                x = _parse_project_geometry_length(element.get('x'), 'x')
                y = _parse_project_geometry_length(element.get('y'), 'y')
                font_size = float(font_sizes[id(element)])
            except (KeyError, TypeError, ValueError):
                return None
            if font_size <= 0:
                return None
            style = tuple(
                (
                    _effective_presentation_value(
                        element,
                        name,
                        parent_by_id,
                    )
                    or ''
                ).strip().lower()
                for name in style_properties
            )
            return {
                'chars': len(compact_text),
                'font_size': font_size,
                'style': style,
                'text': text,
                'x': x,
                'y': y,
            }

        suspects: List[str] = []
        for group in list(root):
            if (
                _local_name(group) != 'g'
                or self._is_hidden_element(group, parent_by_id)
            ):
                continue
            current_run: List[Dict] = []

            def flush_run() -> None:
                if len(current_run) < 2:
                    return
                total_chars = sum(line['chars'] for line in current_run)
                longest_line = max(line['chars'] for line in current_run)
                if (
                    total_chars < _PARAGRAPH_LINE_MIN_TOTAL_CHARS
                    or longest_line < _PARAGRAPH_LINE_MIN_LONGEST_CHARS
                ):
                    return
                first = current_run[0]
                last = current_run[-1]
                suspects.append(
                    f'{_element_label(group)} x={first["x"]:.1f}, '
                    f'y={first["y"]:.1f}..{last["y"]:.1f}, '
                    f'{len(current_run)} lines'
                )

            for child in list(group):
                line = line_record(child)
                if line is None:
                    flush_run()
                    current_run = []
                    continue
                if current_run:
                    previous = current_run[-1]
                    line_gap = line['y'] - previous['y']
                    same_frame = (
                        abs(line['x'] - previous['x'])
                        <= _PARAGRAPH_LINE_X_TOLERANCE
                        and line['style'] == previous['style']
                        and math.isclose(
                            line['font_size'],
                            previous['font_size'],
                            rel_tol=0.0,
                            abs_tol=1e-6,
                        )
                        and line_gap
                        >= line['font_size'] * _PARAGRAPH_LINE_GAP_MIN_RATIO
                        and line_gap
                        <= line['font_size'] * _PARAGRAPH_LINE_GAP_MAX_RATIO
                        and not _PARAGRAPH_LINE_TERMINATOR_RE.search(
                            previous['text']
                        )
                    )
                    if not same_frame:
                        flush_run()
                        current_run = []
                current_run.append(line)
            flush_run()

        if not suspects:
            return
        sample = '; '.join(suspects[:3])
        suffix = '' if len(suspects) <= 3 else f'; +{len(suspects) - 3} more'
        result['warnings'].append(
            f'Detected {len(suspects)} paragraph-like line run(s) split '
            f'across sibling <text> elements ({sample}{suffix}). If each run '
            'is one prose paragraph, combine it into one <text>: keep its '
            'first line as direct text and use direct <tspan> children with '
            'the parent x and positive relative dy values for later lines. '
            'An all-<tspan> form may start with dy="0". Keep semantically '
            'independent text frames separate.'
        )


    @staticmethod
    def _is_tspan(elem: ET.Element) -> bool:
        return elem.tag == f'{{{SVG_NS}}}tspan'


    @classmethod
    def _is_line_tspan(cls, elem: ET.Element) -> bool:
        if not cls._is_tspan(elem):
            return False
        if elem.get('x') is not None or elem.get('y') is not None:
            return True
        dy = elem.get('dy')
        if dy is None:
            return False
        try:
            return float(re.match(r'^[\s,]*([+-]?(?:\d+\.?\d*|\d*\.\d+))', dy).group(1)) != 0
        except (AttributeError, ValueError):
            return True


    @classmethod
    def _leading_text_normalizer_reject_reason(cls, text_el: ET.Element) -> str | None:
        if text_el.get('x') is None:
            return '<text> has no x anchor'

        children = list(text_el)
        if any(not cls._is_tspan(child) for child in children):
            return '<text> has non-tspan child'

        # Mirror svg_finalize.flatten_tspan: tail text after an inline run
        # stays on its line, but a line made of one positioned <tspan> has
        # nowhere to keep a tail.
        groups: list[list[ET.Element]] = [[]]
        for child in children:
            if cls._is_line_tspan(child):
                groups.append([child])
            else:
                groups[-1].append(child)
        for group in groups[1:]:
            if len(group) == 1 and (group[0].tail or "").strip():
                return (
                    'text follows a positioned line <tspan> directly; '
                    'nest it inside that <tspan>'
                )

        return None


    @classmethod
    def _visible_svg_text_blocks(cls, root: ET.Element) -> List[str]:
        """Return rendered text blocks, excluding hidden/non-visual content."""
        working_root = copy.deepcopy(root)
        if (
            _expand_local_use_references is not None
            and _UseExpansionError is not None
        ):
            try:
                _expand_local_use_references(working_root)
            except _UseExpansionError:
                working_root = copy.deepcopy(root)
        parent_by_id = {
            id(child): parent
            for parent in working_root.iter()
            for child in list(parent)
        }

        blocks: List[str] = []
        for element in working_root.iter(f'{{{SVG_NS}}}text'):
            if (
                cls._is_hidden_element(element, parent_by_id)
                or cls._has_non_visual_ancestor(
                    element,
                    working_root,
                    parent_by_id,
                )
                or cls._has_zero_opacity(element, parent_by_id)
            ):
                continue
            text = re.sub(r'\s+', ' ', ' '.join(element.itertext())).strip()
            if text:
                blocks.append(text)
        return blocks


