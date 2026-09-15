"""pptx: marker, animation anchor, and native-object checks."""

from .common import *  # noqa: F401,F403


class SemanticChecks:
    """Mixin: pptx: marker, animation anchor, and native-object checks."""

    def _check_animation_group_ids(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ):
        """Validate top-level animation anchors without policing inner groups."""
        non_visual = {'defs', 'title', 'desc', 'metadata', 'style'}
        group_indexes: Dict[str, List[int]] = defaultdict(list)
        ungrouped: List[str] = []
        ungrouped_signatures: List[Tuple[object, ...]] = []
        visual_index = 0

        for child in root:
            tag = _local_name(child)
            if tag in non_visual:
                continue
            visual_index += 1
            is_first_visual = visual_index == 1

            if tag == 'g':
                group_id = _usable_animation_group_id(child.get('id'))
                if group_id is None:
                    result['warnings'].append(
                        f"Top-level visible <g> #{visual_index} has no id; "
                        "object-level animation config cannot reference it"
                    )
                    continue
                group_indexes[group_id].append(visual_index)
                continue

            if svg_path.parent.name != 'svg_output':
                continue
            if child.get('data-pptx-layer') is not None:
                continue
            if (
                _is_static_page_frame is not None
                and _is_static_page_frame(
                    child.get('data-pptx-role'),
                    child.get('data-pptx-placeholder'),
                )
            ):
                continue
            if is_first_visual and self._is_full_canvas_root_rect(root, child):
                continue
            child_id = (child.get('id') or '').strip()
            ungrouped.append(
                f'<{tag} id="{child_id}">'
                if child_id else f'<{tag}> #{visual_index}'
            )
            ungrouped_signatures.append(
                self._prototype_element_signature(child)
            )

        for group_id, indexes in sorted(group_indexes.items()):
            if len(indexes) > 1:
                positions = ', '.join(str(item) for item in indexes)
                result['errors'].append(
                    f'Duplicate top-level group id {group_id!r} at visible '
                    f'positions {positions}; animation target ids must be unique'
                )

        if ungrouped:
            samples = ', '.join(ungrouped[:3])
            if len(ungrouped) > 3:
                samples += ', ...'
            message = (
                f'{len(ungrouped)} ungrouped top-level Slide-local element(s) '
                f'in svg_output ({samples}); group only logical content units '
                'in a top-level <g id="...">. Keep genuine static page framing '
                'as a root primitive and declare a supported data-pptx-role such '
                'as "background" or "decoration"'
            )
            prototype_root = self._active_prototype_root()
            prototype_ungrouped = (
                self._ungrouped_slide_local_facts(prototype_root)
                if prototype_root is not None
                else ([], [])
            )
            if (
                prototype_root is not None
                and ungrouped == prototype_ungrouped[0]
                and ungrouped_signatures == prototype_ungrouped[1]
            ):
                self._append_inherited_info(
                    result,
                    'animation_anchor',
                    message,
                )
            else:
                result['warnings'].append(message)


    @staticmethod
    def _prototype_element_signature(
        element: ET.Element,
    ) -> Tuple[object, ...]:
        """Compare warning-owned topology/style while ignoring visible text."""
        return (
            _local_name(element),
            tuple(sorted(element.attrib.items())),
            tuple(
                SemanticChecks._prototype_element_signature(child)
                for child in element
            ),
        )


    def _ungrouped_slide_local_facts(
        self,
        root: ET.Element,
    ) -> Tuple[List[str], List[Tuple[object, ...]]]:
        """Describe and fingerprint top-level non-group Slide-local atoms."""
        non_visual = {'defs', 'title', 'desc', 'metadata', 'style'}
        descriptors: List[str] = []
        signatures: List[Tuple[object, ...]] = []
        visual_index = 0
        for child in root:
            tag = _local_name(child)
            if tag in non_visual:
                continue
            visual_index += 1
            if tag == 'g' or child.get('data-pptx-layer') is not None:
                continue
            if (
                _is_static_page_frame is not None
                and _is_static_page_frame(
                    child.get('data-pptx-role'),
                    child.get('data-pptx-placeholder'),
                )
            ):
                continue
            if visual_index == 1 and self._is_full_canvas_root_rect(root, child):
                continue
            child_id = (child.get('id') or '').strip()
            descriptors.append(
                f'<{tag} id="{child_id}">'
                if child_id else f'<{tag}> #{visual_index}'
            )
            signatures.append(self._prototype_element_signature(child))
        return descriptors, signatures


    def _check_native_object_markers(self, root: ET.Element, result: Dict) -> None:
        """Validate explicit native replacement markers before PPTX export."""
        inline_formula_markers = [
            elem for elem in root.iter()
            if elem.get(_INLINE_FORMULA_ATTR) is not None
        ]
        if inline_formula_markers and _inline_formula_marker_errors is None:
            result['errors'].append(
                "Unable to import inline-formula validator; cannot verify "
                f"{_INLINE_FORMULA_ATTR} markers"
            )
        elif _inline_formula_marker_errors is not None:
            for error in _inline_formula_marker_errors(root):
                result['errors'].append(f"Invalid inline formula marker: {error}")

        invalid_status_elements: set[ET.Element] = set()
        for elem in root.iter():
            marker_id = elem.get('id') or elem.get('data-name') or '<unnamed>'
            if elem.tag.rsplit('}', 1)[-1] == 'metadata':
                continue
            has_status = any(
                elem.get(name) is not None
                for name in (
                    'data-pptx-replace-with',
                    'data-pptx-native',
                    'data-pptx-fallback-kind',
                    'data-pptx-visual-status',
                    'data-pptx-route-status',
                    'data-pptx-replacement-status',
                    'data-pptx-native-status',
                    'data-pptx-native-authority',
                    'data-pptx-import-source',
                    'data-pptx-native-source',
                )
            )
            if not has_status:
                continue
            if (
                _native_marker_status_errors is None
                or _native_marker_release_block_reason is None
            ):
                result['errors'].append(
                    "Unable to import native-object status validator; "
                    f"cannot verify PPTX graphic {marker_id}"
                )
                continue
            status_errors = _native_marker_status_errors(elem)
            for error in status_errors:
                result['errors'].append(
                    f"PPTX graphic {marker_id} has invalid status metadata: {error}"
                )
            if status_errors:
                invalid_status_elements.add(elem)
                continue
            if _native_marker_legacy_warnings is not None:
                for warning in _native_marker_legacy_warnings(elem):
                    result['warnings'].append(
                        f"PPTX replacement marker {marker_id}: {warning}"
                    )
            try:
                fallback_kind = (
                    _native_fallback_kind(elem)
                    if _native_fallback_kind is not None else None
                )
                replacement_kind = (
                    _native_replacement_kind(elem)
                    if _native_replacement_kind is not None else ''
                )
            except ValueError:
                # The shared status validator reported the alias conflict.
                continue
            if fallback_kind == 'placeholder':
                route = (
                    "the native Chart/Table route may reconstruct its active marker"
                    if replacement_kind
                    else "default export keeps the visible placeholder"
                )
                result['warnings'].append(
                    f"PPTX graphic {marker_id} is a reconstruction-only placeholder; "
                    f"it has no baked preview and {route}"
                )

        for elem in root.iter():
            if elem.tag.rsplit('}', 1)[-1] == 'metadata':
                continue
            if _native_replacement_status is None or _native_replacement_kind is None:
                continue
            try:
                status = _native_replacement_status(elem)
                replacement_kind = _native_replacement_kind(elem)
            except ValueError:
                continue
            if not status or replacement_kind:
                continue
            marker_id = elem.get('id') or elem.get('data-name') or '<unnamed>'
            result['warnings'].append(
                f"Native PPTX object {marker_id} is fallback-only: {status}"
            )

        markers = [
            elem for elem in root.iter()
            if (
                _native_replacement_kind is not None
                and elem.tag.rsplit('}', 1)[-1] != 'metadata'
                and elem not in invalid_status_elements
                and _native_replacement_kind(elem)
            )
        ]
        if not markers:
            return
        if _validate_native_object_marker is None:
            result['warnings'].append(
                "Detected data-pptx-replace-with markers, but replacement validator "
                "could not be imported; export-time validation will still run."
            )
            return

        parent_map = {
            child: parent
            for parent in root.iter()
            for child in parent
        }

        def append_metadata_legacy_warnings(marker: ET.Element) -> None:
            if _native_marker_legacy_warnings is None:
                return
            marker_id = marker.get('id') or '<unnamed>'
            for child in marker:
                if child.tag.rsplit('}', 1)[-1] != 'metadata':
                    continue
                for warning in _native_marker_legacy_warnings(child):
                    result['warnings'].append(
                        f"PPTX replacement marker {marker_id}: {warning}"
                    )

        for marker in markers:
            marker_id = marker.get('id') or '<unnamed>'
            replacement_kind = _native_replacement_kind(marker)
            if (
                self.canonical_authoring
                and replacement_kind in {'chart', 'table'}
            ):
                if _require_fresh_native_fallback is None:
                    result['errors'].append(
                        "Unable to import native fallback freshness validator; "
                        f"cannot verify canonical marker {marker_id}"
                    )
                else:
                    try:
                        _require_fresh_native_fallback(
                            marker,
                            document_root=root,
                        )
                    except RuntimeError as exc:
                        result['errors'].append(
                            f"Canonical SVG-first native marker {marker_id}: {exc}"
                        )
            ancestors = []
            parent = parent_map.get(marker)
            while parent is not None and parent is not root:
                if parent.tag.rsplit('}', 1)[-1] == 'g':
                    ancestors.append(parent)
                parent = parent_map.get(parent)
            ancestors_tuple = tuple(reversed(ancestors))
            if _validate_native_object_marker_with_warnings is not None:
                try:
                    warnings = _validate_native_object_marker_with_warnings(
                        marker,
                        ancestors=ancestors_tuple,
                        document_root=root,
                    )
                except RuntimeError as exc:
                    result['errors'].append(
                        f"Invalid data-pptx-replace-with marker {marker_id}: {exc}"
                    )
                    continue
                for warning in warnings:
                    result['warnings'].append(
                        f"data-pptx-replace-with marker {marker_id}: {warning}"
                    )
                append_metadata_legacy_warnings(marker)
                continue

            try:
                _validate_native_object_marker(marker, ancestors=ancestors_tuple)
            except RuntimeError as exc:
                result['errors'].append(
                    f"Invalid data-pptx-replace-with marker {marker_id}: {exc}"
                )
                continue
            append_metadata_legacy_warnings(marker)
            if _native_object_marker_warnings is None:
                continue
            for warning in _native_object_marker_warnings(
                marker,
                ancestors=ancestors_tuple,
                document_root=root,
            ):
                result['warnings'].append(
                    f"data-pptx-replace-with marker {marker_id}: {warning}"
                )


    def _check_pptx_structure_metadata(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Validate the intrinsic structured Master/Layout SVG contract."""
        has_structure_metadata = any(
            elem.get(attr) is not None
            for elem in root.iter()
            for attr in _PPTX_STRUCTURE_ATTRS
        )
        if self.quick_generate and not has_structure_metadata:
            return
        if (
            not self.quick_generate
            and not self.template_mode
            and svg_path.parent.name == 'svg_output'
        ):
            declared_mode = _declared_pptx_structure_mode(
                self._resolve_project_path(svg_path)
            )
            if declared_mode == 'flat':
                forbidden_attrs = sorted({
                    attr
                    for elem in root.iter()
                    for attr in _PPTX_STRUCTURE_ATTRS
                    if elem.get(attr) is not None
                })
                if forbidden_attrs:
                    result['errors'].append(
                        f"{svg_path.name}: pptx_structure.mode: flat forbids "
                        "Master/Layout/layer/placeholder metadata; remove "
                        + ', '.join(forbidden_attrs)
                    )
                return
            if declared_mode != 'structured':
                # The project-level gate emits one actionable migration error.
                # Avoid burying it under repeated per-page structure failures.
                return
        require_structure = bool(
            self.template_mode
            or svg_path.parent.name == 'svg_output'
        )
        if not has_structure_metadata and not require_structure:
            return
        result['errors'].extend(_local_pptx_structure_errors(
            root,
            svg_path,
            require_structure=require_structure,
        ))
        self._check_placeholder_carrier_flattening(root, svg_path, result)
        if svg_path.parent.name == 'svg_output':
            self._append_structure_coverage_warnings(root, result)
        if _validate_template_structure_svg is None:
            result['errors'].append(
                "Structured PPTX metadata validator could not be imported; "
                "the quality gate cannot verify this SVG"
            )
            return
        result['errors'].extend(_validate_template_structure_svg(svg_path))
        result['errors'] = list(dict.fromkeys(result['errors']))


    @staticmethod
    def _check_placeholder_carrier_flattening(
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Reject slot carriers that export as multiple native children.

        Default export flattens non-mergeable positional ``<tspan>`` lines
        before converting the surrounding slot group to DrawingML. Reuse that
        exact transform here so the quality gate fails before the later
        placeholder-unwrapping step does.
        """
        if _flatten_positional_tspans is None:
            return

        candidate_ids: List[str] = []
        for slot in root.iter(f'{{{SVG_NS}}}g'):
            if not (slot.get('data-pptx-placeholder') or '').strip():
                continue
            binding = (
                slot.get('data-pptx-binding') or 'carrier'
            ).strip().lower()
            if binding != 'carrier':
                continue
            visual_children = [
                child for child in list(slot)
                if _local_name(child) not in _NON_VISUAL_SVG_TAGS
            ]
            carriers = [
                child for child in visual_children
                if (child.get('data-pptx-carrier') or '')
                .strip()
                .lower()
                == 'true'
            ]
            slot_id = (slot.get('id') or '').strip()
            if not slot_id or len(visual_children) != 1 or len(carriers) != 1:
                continue
            if not any(
                _local_name(descendant) == 'tspan'
                and any(
                    descendant.get(name) is not None
                    for name in ('x', 'y', 'dy')
                )
                for descendant in carriers[0].iter()
            ):
                continue
            candidate_ids.append(slot_id)

        if not candidate_ids:
            return

        flattened_root = copy.deepcopy(root)
        try:
            _flatten_positional_tspans(
                ET.ElementTree(flattened_root),
                merge_paragraphs=True,
                preserve_line_breaks=True,
            )
        except ValueError:
            # The shared text check reports the unsupported nested-position
            # contract; avoid turning a quality result into a checker crash.
            return
        slots_by_id = {
            (slot.get('id') or '').strip(): slot
            for slot in flattened_root.iter(f'{{{SVG_NS}}}g')
            if (slot.get('id') or '').strip()
        }
        for slot_id in candidate_ids:
            slot = slots_by_id.get(slot_id)
            if slot is None:
                continue
            native_children = [
                child for child in list(slot)
                if _local_name(child) not in _NON_VISUAL_SVG_TAGS
            ]
            if len(native_children) == 1:
                continue
            result['errors'].append(
                f"{svg_path.name}: placeholder slot {slot_id} becomes "
                f"{len(native_children)} native children after positional "
                "<tspan> flattening; a carrier-bound slot must export as one "
                "text or picture carrier. Use one single-frame dy-stacked text "
                "frame, or move independently positioned lines outside the slot"
            )


    def _append_structure_coverage_warnings(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Warn on mapped pages that compile to bare Masters / empty Layouts.

        Zero-slot and framing-only Layouts are legal contracts, so these stay
        advisory warnings. They neither fail the workflow gate nor require a
        per-warning disposition.
        """
        messages = self._structure_coverage_messages(root)
        if not messages:
            return
        prototype_root = self._active_prototype_root()
        if (
            prototype_root is not None
            and messages == self._structure_coverage_messages(prototype_root)
        ):
            for message in messages:
                self._append_inherited_info(
                    result,
                    'structure_coverage',
                    message,
                )
            return
        result['warnings'].extend(messages)


    @staticmethod
    def _structure_coverage_messages(root: ET.Element) -> List[str]:
        """Return advisory coverage messages for one structured page."""
        if not (root.get('data-pptx-layout') or '').strip():
            return []
        messages: List[str] = []
        has_layer_mark = any(
            elem.get('data-pptx-layer') is not None
            for elem in root.iter()
        )
        has_layout_atom = any(
            child.get('data-pptx-layer') == 'layout'
            for child in list(root)
        )
        has_placeholder = any(
            elem.get('data-pptx-placeholder') is not None
            for elem in root.iter()
        )
        if not has_layer_mark:
            messages.append(
                'Mapped page declares data-pptx-layout but no data-pptx-layer '
                'mark; the exported Master gets no shared background/chrome '
                'and the Layout gets no static framing. Generated templates '
                'should mark the deck-wide '
                'background data-pptx-layer="master" and this layout key\'s '
                'framing data-pptx-layer="layout". No change or disposition '
                'is required.'
            )
        if not has_placeholder and not has_layout_atom:
            messages.append(
                'Mapped page has no placeholder slot and no '
                'data-pptx-layer="layout" atom; its Layout exports empty. '
                'Generated templates should declare the slots the page actually '
                'has (title / subtitle / '
                'body / picture / slide-number / footer) and mark the layout '
                'key\'s static framing unless this is intentionally a fixed '
                'zero-slot composition. No change or disposition is required.'
            )
        elif not has_placeholder:
            messages.append(
                'Mapped Layout has static framing but no insertable '
                'placeholder slot. Generated templates should declare the '
                'slots the page actually has (title / subtitle / body / '
                'picture / slide-number / footer) unless zero-slot is the '
                'intended reusable contract. No change or disposition is required.'
            )
        return messages


    def _check_semantic_markers(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Validate minimal compiler hints without changing SVG rendering."""
        has_semantics = any(
            elem.get(attr) is not None
            for elem in root.iter()
            for attr in _SEMANTIC_ATTRS
        )
        require_page_role = (
            svg_path.parent.name in {'svg_output', 'svg_final'}
            and root.get('data-pptx-layout') is None
        )
        if _validate_semantic_markers is None:
            if has_semantics:
                result['warnings'].append(
                    "Detected Semantic SVG markers, but their validator could "
                    "not be imported."
                )
            return
        for issue in _validate_semantic_markers(
            root,
            require_page_role=require_page_role,
        ):
            if issue.severity == 'error':
                result['errors'].append(issue.message)
            else:
                result['warnings'].append(issue.message)


    def _check_animation_config_contract(self, dir_path: Path) -> None:
        """Project-level animations.json reference checks."""
        project_path = self._resolve_project_path(dir_path)
        config_path = project_path / 'animations.json'
        if (
            _load_animation_config is None
            or _validate_animation_config is None
            or _validate_animation_config_errors is None
            or _validate_transition_config is None
        ):
            if config_path.is_file():
                detail = _animation_config_import_error or 'unknown import error'
                self._animation_issues.append((
                    'error',
                    f'animations.json validation is unavailable: {detail}',
                ))
            return
        try:
            config = _load_animation_config(project_path)
        except Exception as exc:
            self._animation_issues.append(('error', f"animations.json is invalid: {exc}"))
            return
        if not config:
            return
        fatal_errors = list(dict.fromkeys(
            _validate_transition_config(config)
            + _validate_animation_config_errors(config)
        ))
        for error in fatal_errors:
            self._animation_issues.append(('error', error))
        for message in _validate_animation_config(project_path, config):
            severity = (
                'warning'
                if ' has no id and cannot be customized in animations.json' in message
                else 'error'
            )
            self._animation_issues.append((severity, message))


