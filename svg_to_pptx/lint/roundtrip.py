"""PPTX-to-SVG round-trip workspace checks."""

from .common import *  # noqa: F401,F403


class RoundtripChecks:
    """Mixin: PPTX-to-SVG round-trip workspace checks."""

    def check_roundtrip_workspace(self, workspace: str) -> List[Dict]:
        """Check edited text on the resolved round-trip output page roster."""
        from svg_to_pptx.authoring_roundtrip import (
            AuthoringRoundtripError,
            _generate_baseline_bundle,
            _load_current_assets,
            _load_documents,
            _load_page_plan,
            _parse_svg,
        )

        project_path = Path(workspace).resolve()
        authoring_dir = project_path / 'authoring-svg-flat'
        self._has_incomplete_page_roster = False
        self._structured_native_slots: list[str] = []
        if not project_path.is_dir():
            print(f"[ERROR] Round-trip workspace does not exist: {project_path}")
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []
        if not authoring_dir.is_dir():
            print(
                "[ERROR] Round-trip workspace has no authoring-svg-flat/: "
                f"{project_path}"
            )
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []

        try:
            source_root, source_proxy_dir, documents, _, _ = _load_documents(
                project_path,
                authoring_dir,
            )
            pages, _ = _load_page_plan(
                project_path,
                authoring_dir,
                documents,
            )
            current_assets, inventory = _load_current_assets(
                project_path,
                authoring_dir,
                documents,
            )
            baseline_temporary, baseline_dir, baseline_assets = (
                _generate_baseline_bundle(
                    project_path,
                    source_root,
                    source_proxy_dir,
                    documents,
                    inventory,
                )
            )
        except (AuthoringRoundtripError, OSError, ValueError) as exc:
            print(f"[ERROR] Invalid round-trip workspace: {exc}")
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []

        documents_by_slide = {
            document.source_slide: document
            for document in documents.values()
        }
        self._active_slide_count = len(pages)
        print(
            f"\n[SCAN] Checking {len(pages)} round-trip output SVG page(s) "
            "for edited-text horizontal capacity (single-line width per "
            "positioned line; vertical wrapping is not modeled)...\n"
        )
        try:
            baseline_roots = {
                source_slide: _parse_svg(
                    baseline_dir / document.name,
                )
                for source_slide, document in documents_by_slide.items()
            }
            for page in pages:
                result = self._check_roundtrip_file(
                    authoring_dir / page.svg_name,
                    documents_by_slide[page.source_slide],
                    baseline_roots[page.source_slide],
                    current_assets,
                    baseline_assets,
                    output_index=page.output_index,
                    source_slide=page.source_slide,
                )
                self._print_result(result)
        finally:
            baseline_temporary.cleanup()
        return self.results


    def _check_roundtrip_file(
        self,
        svg_path: Path,
        source_document,
        baseline_root: ET.Element,
        current_assets,
        baseline_assets,
        *,
        output_index: int,
        source_slide: int,
    ) -> Dict:
        """Run only edited-text checks for one resolved round-trip page."""
        result = {
            'file': svg_path.name,
            'path': str(svg_path),
            'exists': svg_path.is_file(),
            'errors': [],
            'warnings': [],
            'info': {
                'output_page': output_index,
                'source_slide': source_slide,
            },
            'passed': True,
        }
        if not svg_path.is_file():
            result['errors'].append('File does not exist')
            result['passed'] = False
            return self._record_result(result)

        try:
            source_bytes = svg_path.read_bytes()
            result['source_sha256'] = hashlib.sha256(source_bytes).hexdigest()
            content = source_bytes.decode('utf-8')
            root = self._parse_xml_root(content, result)
            if root is not None:
                self._check_viewbox(root, svg_path, result, None)
                included_text_ids, unchanged_text_ids = (
                    self._roundtrip_text_diff_ids(
                        root,
                        source_document,
                        baseline_root,
                        current_assets,
                        baseline_assets,
                    )
                )
                result['info']['edited_text_elements'] = len(included_text_ids)
                if included_text_ids:
                    scoped_content = self._roundtrip_text_scope_content(
                        root,
                        included_text_ids,
                    )
                    changed_font_families = (
                        self._roundtrip_changed_font_families(
                            root,
                            baseline_root,
                            included_text_ids,
                        )
                    )
                    svg_contracts.check_font_size_values(scoped_content, result)
                    self._check_fonts(
                        scoped_content,
                        result,
                        font_families=changed_font_families,
                    )
                    self._check_text_output_geometry(
                        root,
                        result,
                        included_text_ids=included_text_ids,
                    )
                    self._check_text_bounds(
                        root,
                        result,
                        included_text_ids=included_text_ids,
                    )
                self._check_roundtrip_text_frames(
                    root,
                    result,
                    included_text_ids,
                    unchanged_text_ids,
                )
            result['passed'] = len(result['errors']) == 0
        except Exception as exc:
            result['errors'].append(f"Failed to read file: {exc}")
            result['passed'] = False
        return self._record_result(result)


    @staticmethod
    def _roundtrip_text_diff_ids(
        root: ET.Element,
        source_document,
        baseline_root: ET.Element,
        current_assets,
        baseline_assets,
    ) -> tuple[set[int], set[int]]:
        """Return edited and hash-unchanged source text carrier identities."""
        from svg_to_pptx.authoring_roundtrip import (
            _definition_changes,
            authoring_source_ref_is_unchanged,
        )
        from svg_to_pptx.svg_authoring_view import (
            SOURCE_PROXY_ATTRIBUTE,
            SOURCE_PROXY_KIND,
            SOURCE_REF_ATTRIBUTE,
        )

        baseline_by_ref: Dict[str, ET.Element] = {}
        for element in baseline_root.iter():
            source_ref = element.get(SOURCE_REF_ATTRIBUTE)
            if source_ref is None:
                continue
            if source_ref in baseline_by_ref:
                raise ValueError(
                    f"Regenerated baseline repeats source ref {source_ref!r}"
                )
            baseline_by_ref[source_ref] = element
        _, changed_definition_ids = _definition_changes(
            root,
            baseline_root,
            current_assets,
            baseline_assets,
        )
        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        unchanged_by_owner: Dict[int, bool] = {}
        included: set[int] = set()
        unchanged_text: set[int] = set()
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            current: ET.Element | None = text_element
            source_owner: ET.Element | None = None
            source_ref: str | None = None
            source_proxy = False
            while current is not None:
                if (
                    current.get(SOURCE_PROXY_ATTRIBUTE)
                    == SOURCE_PROXY_KIND
                ):
                    source_proxy = True
                if source_owner is None:
                    candidate = current.get(SOURCE_REF_ATTRIBUTE)
                    if candidate:
                        source_owner = current
                        source_ref = candidate
                current = parent_by_id.get(id(current))
            if source_proxy:
                continue
            if source_owner is not None and source_ref is not None:
                record = source_document.source_refs.get(source_ref)
                if record is not None and record.representation == 'source-proxy':
                    continue
                owner_key = id(source_owner)
                unchanged = unchanged_by_owner.get(owner_key)
                if unchanged is None:
                    baseline_owner = baseline_by_ref.get(source_ref)
                    unchanged = bool(
                        record is not None
                        and baseline_owner is not None
                        and authoring_source_ref_is_unchanged(
                            source_owner,
                            baseline_owner,
                            current_assets,
                            baseline_assets,
                            changed_definition_ids=changed_definition_ids,
                        )
                    )
                    unchanged_by_owner[owner_key] = unchanged
                if unchanged:
                    unchanged_text.add(id(text_element))
                    continue
            included.add(id(text_element))
        return included, unchanged_text


    @staticmethod
    def _roundtrip_edited_text_ids(
        root: ET.Element,
        source_document,
        baseline_root: ET.Element,
        current_assets,
        baseline_assets,
    ) -> set[int]:
        """Return edited text carrier identities for compatibility callers."""
        included, _unchanged = RoundtripChecks._roundtrip_text_diff_ids(
            root,
            source_document,
            baseline_root,
            current_assets,
            baseline_assets,
        )
        return included


    @staticmethod
    def _roundtrip_text_scope_content(
        root: ET.Element,
        included_text_ids: set[int],
    ) -> str:
        """Serialize only edited text and its inherited presentation chain."""
        def clone_relevant(element: ET.Element) -> ET.Element | None:
            if (
                _local_name(element) == 'text'
                and id(element) in included_text_ids
            ):
                return copy.deepcopy(element)
            children = [
                clone
                for child in list(element)
                if (clone := clone_relevant(child)) is not None
            ]
            if element is not root and not children:
                return None
            clone = ET.Element(element.tag, dict(element.attrib))
            for child in children:
                clone.append(child)
            return clone

        scoped_root = clone_relevant(root)
        if scoped_root is None:
            return ''
        return ET.tostring(scoped_root, encoding='unicode')


    @staticmethod
    def _roundtrip_resolved_font_family(
        element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> str | None:
        """Resolve inherited SVG font-family at one text carrier."""
        current: ET.Element | None = element
        while current is not None:
            style_values = (
                _parse_inline_style(current.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            value = style_values.get('font-family')
            if value is None:
                value = current.get('font-family')
            if isinstance(value, str) and value.strip():
                normalized = value.strip()
                if normalized.casefold() not in {'inherit', 'unset'}:
                    return normalized
            current = parent_by_id.get(id(current))
        return None


    @staticmethod
    def _roundtrip_relative_path(
        element: ET.Element,
        ancestor: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> tuple[int, ...] | None:
        """Return child indices from one source-ref owner to a descendant."""
        reverse_path: List[int] = []
        current = element
        while current is not ancestor:
            parent = parent_by_id.get(id(current))
            if parent is None:
                return None
            reverse_path.append(
                next(
                    index
                    for index, child in enumerate(list(parent))
                    if child is current
                )
            )
            current = parent
        return tuple(reversed(reverse_path))


    @staticmethod
    def _roundtrip_baseline_context(
        current_owner: ET.Element,
        baseline_owner: ET.Element,
        path: tuple[int, ...],
    ) -> ET.Element:
        """Follow a matching relative path, stopping at the closest context."""
        current = current_owner
        baseline = baseline_owner
        for index in path:
            current_children = list(current)
            baseline_children = list(baseline)
            if index >= len(current_children) or index >= len(baseline_children):
                break
            current_child = current_children[index]
            baseline_child = baseline_children[index]
            if _local_name(current_child) != _local_name(baseline_child):
                break
            current = current_child
            baseline = baseline_child
        return baseline


    @classmethod
    def _roundtrip_changed_font_families(
        cls,
        root: ET.Element,
        baseline_root: ET.Element,
        included_text_ids: set[int],
    ) -> List[str]:
        """Return resolved edited fonts that differ from the source baseline."""
        from svg_to_pptx.svg_authoring_view import SOURCE_REF_ATTRIBUTE

        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        baseline_parent_by_id = {
            id(child): parent
            for parent in baseline_root.iter()
            for child in list(parent)
        }
        baseline_by_ref = {
            source_ref: element
            for element in baseline_root.iter()
            if (source_ref := element.get(SOURCE_REF_ATTRIBUTE))
        }
        changed: List[str] = []
        seen: set[str] = set()
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            if id(text_element) not in included_text_ids:
                continue
            current_owner: ET.Element | None = text_element
            source_ref: str | None = None
            while current_owner is not None:
                source_ref = current_owner.get(SOURCE_REF_ATTRIBUTE)
                if source_ref:
                    break
                current_owner = parent_by_id.get(id(current_owner))
            baseline_owner = (
                baseline_by_ref.get(source_ref)
                if source_ref is not None
                else None
            )
            for carrier in text_element.iter():
                if _local_name(carrier) not in {'text', 'tspan'}:
                    continue
                if not re.sub(r'\s+', ' ', ''.join(carrier.itertext())).strip():
                    continue
                current_family = cls._roundtrip_resolved_font_family(
                    carrier,
                    parent_by_id,
                )
                if current_family is None:
                    continue
                baseline_context = baseline_root
                if current_owner is not None and baseline_owner is not None:
                    relative_path = cls._roundtrip_relative_path(
                        carrier,
                        current_owner,
                        parent_by_id,
                    )
                    if relative_path is not None:
                        baseline_context = cls._roundtrip_baseline_context(
                            current_owner,
                            baseline_owner,
                            relative_path,
                        )
                baseline_family = cls._roundtrip_resolved_font_family(
                    baseline_context,
                    baseline_parent_by_id,
                )
                normalized = cls._normalize_font_stack(current_family)
                if (
                    baseline_family is not None
                    and normalized
                    == cls._normalize_font_stack(baseline_family)
                ):
                    continue
                if normalized and normalized not in seen:
                    changed.append(current_family)
                    seen.add(normalized)
        return changed


    def _check_roundtrip_text_frames(
        self,
        root: ET.Element,
        result: Dict,
        included_text_ids: set[int],
        unchanged_text_ids: set[int],
    ) -> None:
        """Calibrate source text widths, then check edited owning frames."""
        helpers = (
            _estimate_single_line_text_frame_width,
            _parse_project_font_weight,
            _parse_project_geometry_length,
            _parse_project_text_anchor,
            _resolve_project_font_sizes,
            _resolve_project_letter_spacings,
        )
        if any(helper is None for helper in helpers):
            result['warnings'].append(
                'Unable to import text metrics; skipped edited-text frame-fit check'
            )
            return
        try:
            font_sizes = _resolve_project_font_sizes(root)
            letter_spacings = _resolve_project_letter_spacings(
                root,
                font_sizes,
            )
        except ValueError:
            return

        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        measured_unchanged = 0
        positive_overflow_ratios: List[float] = []
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            if id(text_element) not in unchanged_text_ids:
                continue
            if self._has_non_visual_ancestor(
                text_element,
                root,
                parent_by_id,
            ):
                continue
            if (
                self._is_hidden_element(text_element, parent_by_id)
                or self._has_zero_opacity(text_element, parent_by_id)
            ):
                continue
            visible_text = ''.join(text_element.itertext())
            if (
                not visible_text.strip()
                or ('{{' in visible_text and '}}' in visible_text)
            ):
                continue
            estimated = self._estimated_text_bounds(
                text_element,
                parent_by_id,
                font_sizes,
                letter_spacings,
                include_headroom=True,
            )
            if estimated is None:
                continue
            _frame_label, frame, _frame_error, frame_is_inferred = (
                self._roundtrip_text_frame(
                    text_element,
                    parent_by_id,
                )
            )
            if frame is None or frame_is_inferred:
                continue
            measured_unchanged += 1
            metrics = self._bounds_overflow_metrics(estimated, frame)
            if metrics is not None and metrics[1] > 0:
                positive_overflow_ratios.append(metrics[1])

        calibration = min(
            max(positive_overflow_ratios, default=0.0),
            _ROUNDTRIP_TEXT_CALIBRATION_CAP,
        )
        result['info']['roundtrip_text_calibration'] = {
            'factor': calibration,
            'measured_unchanged': measured_unchanged,
            'positive_unchanged': len(positive_overflow_ratios),
        }
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            if id(text_element) not in included_text_ids:
                continue
            if self._has_non_visual_ancestor(
                text_element,
                root,
                parent_by_id,
            ):
                continue
            if (
                self._is_hidden_element(text_element, parent_by_id)
                or self._has_zero_opacity(text_element, parent_by_id)
            ):
                continue
            visible_text = ''.join(text_element.itertext())
            if (
                not visible_text.strip()
                or ('{{' in visible_text and '}}' in visible_text)
            ):
                continue
            estimated = self._estimated_text_bounds(
                text_element,
                parent_by_id,
                font_sizes,
                letter_spacings,
                include_headroom=True,
            )
            if estimated is None:
                continue
            frame_label, frame, frame_error, frame_is_inferred = (
                self._roundtrip_text_frame(
                    text_element,
                    parent_by_id,
                )
            )
            text_label = self._text_diagnostic_label(text_element)
            if frame is None:
                detail = f': {frame_error}' if frame_error else ''
                result['warnings'].append(
                    f'Cannot verify owning frame for edited {text_label}{detail}; '
                    'keep data-pptx-frame on the logical object or place a '
                    'rect frame beside the text'
                )
                continue
            metrics = self._bounds_overflow_metrics(estimated, frame)
            if metrics is None or metrics[1] <= 0:
                continue
            _axes, horizontal_ratio, _vertical_ratio = metrics
            corrected_horizontal_ratio = max(
                horizontal_ratio - calibration,
                0.0,
            )
            if corrected_horizontal_ratio <= 0:
                continue
            left, top, right, bottom = estimated
            frame_left, frame_top, frame_right, frame_bottom = frame
            overflow_detail = (
                f'overflow horizontal {corrected_horizontal_ratio:.1%} after '
                f'{calibration:.1%} same-page source calibration '
                f'(raw {horizontal_ratio:.1%})'
                if calibration > 0
                else f'overflow horizontal {horizontal_ratio:.1%}'
            )
            finding = (
                f'{text_label} exceeds owning frame {frame_label} on the '
                f'horizontal axis: estimated text ({left:.1f}, {top:.1f})-'
                f'({right:.1f}, {bottom:.1f}), frame ({frame_left:.1f}, '
                f'{frame_top:.1f})-({frame_right:.1f}, {frame_bottom:.1f}), '
                f'{overflow_detail}; shorten or '
                'reflow the edited text, or '
                'enlarge its owning frame'
            )
            if frame_is_inferred:
                result['warnings'].append(
                    f'{finding}; frame inferred from nearest rect sibling; '
                    'add data-pptx-frame to make this blocking'
                )
            else:
                result['errors'].append(finding)


    @classmethod
    def _roundtrip_text_frame(
        cls,
        text_element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[
        str | None,
        Tuple[float, float, float, float] | None,
        str | None,
        bool,
    ]:
        """Resolve an explicit logical frame or the nearest rect sibling."""
        current: ET.Element | None = text_element
        while current is not None:
            raw_frame = current.get('data-pptx-frame')
            if raw_frame is not None:
                label = f'{_element_label(current)} data-pptx-frame'
                try:
                    frame = _parse_positive_bounds(raw_frame)
                except ValueError as exc:
                    return label, None, f'{label} {exc}', False
                transformed = cls._transformed_rect_bounds(
                    current,
                    frame,
                    parent_by_id,
                )
                if transformed is None:
                    return (
                        label,
                        None,
                        f'{label} has an unresolved transform',
                        False,
                    )
                return label, transformed, None, False
            current = parent_by_id.get(id(current))

        if _parse_project_geometry_length is None:
            return None, None, 'the SVG length parser is unavailable', False
        current = text_element
        rect_errors: List[str] = []
        while current is not None:
            parent = parent_by_id.get(id(current))
            if parent is None:
                break
            siblings = list(parent)
            try:
                current_index = siblings.index(current)
            except ValueError:
                current_index = 0
            rects = sorted(
                (
                    (abs(index - current_index), index, sibling)
                    for index, sibling in enumerate(siblings)
                    if _local_name(sibling) == 'rect'
                ),
                key=lambda item: (item[0], item[1]),
            )
            for _distance, _index, rect in rects:
                label = _element_label(rect)
                try:
                    x = _parse_project_geometry_length(
                        rect.get('x') or '0',
                        'x',
                    )
                    y = _parse_project_geometry_length(
                        rect.get('y') or '0',
                        'y',
                    )
                    width = _parse_project_geometry_length(
                        rect.get('width'),
                        'width',
                    )
                    height = _parse_project_geometry_length(
                        rect.get('height'),
                        'height',
                    )
                    if (
                        not all(math.isfinite(value) for value in (x, y, width, height))
                        or width <= 0
                        or height <= 0
                    ):
                        raise ValueError('must use finite positive width and height')
                except (TypeError, ValueError) as exc:
                    rect_errors.append(f'{label} {exc}')
                    continue
                transformed = cls._transformed_rect_bounds(
                    rect,
                    (x, y, width, height),
                    parent_by_id,
                )
                if transformed is not None:
                    return label, transformed, None, True
                rect_errors.append(f'{label} has an unresolved transform')
            current = parent
        return (
            None,
            None,
            rect_errors[0] if rect_errors else None,
            False,
        )


