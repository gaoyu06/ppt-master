"""Spec-lock, template contract, and pptx_structure checks."""

from .common import *  # noqa: F401,F403


class StructureChecks:
    """Mixin: Spec-lock, template contract, and pptx_structure checks."""

    def _check_canonical_authoring(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Report SVG that was not compact when authored (advisory).

        The exporter accepts explicit declarations, so drift from the compact
        form never blocks. ``compact_svg_styles.py --inplace`` applies the
        deterministic normalization on request for authored project pages; it
        is not applied to structured template rosters, where per-slide
        compaction would make shared Master/Layout atoms diverge and shift
        native fallback hashes. Mirror materialization compacts its own tree
        before publication.
        """
        if not self.canonical_authoring:
            return
        errors = canonical_authoring_errors(
            root,
            # Authored-preset/native frames can intentionally retain the
            # helper's exact precision. Imported projections compact their
            # model-facing frames before publication, where provenance is
            # still known.
            compact_native_frames=False,
        )
        if not errors:
            return
        if self.template_mode:
            result['warnings'].extend(
                f"Noncanonical compact authoring: {error} "
                "(advisory; structured rosters keep their explicit form)"
                for error in errors
            )
            return
        def normalizer(error: str) -> str:
            if "page-space metadata" in error:
                return (
                    "`python3 scripts/compact_svg_coordinates.py <svg_output> "
                    "--inplace --keep-native-frames`"
                )
            return "`python3 scripts/compact_svg_styles.py <svg_output> --inplace`"

        result['warnings'].extend(
            f"Noncanonical compact authoring: {error} "
            f"(advisory; normalize with {normalizer(error)}, "
            "re-stamp pages that carry Chart/Table fallbacks, and rerun the "
            "final gate, or leave the explicit form)"
            for error in errors
        )


    def _get_spec_lock(self, svg_path: Path):
        """Locate and parse spec_lock.md near the SVG. Returns dict or None.

        Looks in svg_path.parent and svg_path.parent.parent (covers the two
        common layouts: SVG directly under <project>/ or under
        <project>/svg_output/). Results are cached per lock path.
        """
        if self.quick_generate:
            return None
        if _parse_spec_lock is None:
            return None
        for candidate in (svg_path.parent / 'spec_lock.md',
                          svg_path.parent.parent / 'spec_lock.md'):
            if candidate in self._lock_cache:
                return self._lock_cache[candidate]
            if candidate.exists():
                try:
                    data = _parse_spec_lock(candidate)
                except Exception:
                    data = None
                self._lock_cache[candidate] = data
                if data is not None:
                    self._lock_seen = True
                return data
        return None


    def _prototype_drift_allowances(
        self,
    ) -> Tuple[set[str], set[str], set[str]]:
        """Return color/font/size values owned by the selected mirror page."""
        prototype_root = self._active_prototype_root()
        if prototype_root is None:
            return set(), set(), set()
        try:
            content = self._active_prototype_path.read_text(encoding='utf-8')
        except (AttributeError, OSError):
            return set(), set(), set()

        colors: set[str] = set()
        for attribute in _PAINT_PROPERTIES or ():
            for raw_value in self._svg_property_values(content, attribute):
                normalized = raw_value.strip()
                if normalized.lower() in {'none', 'transparent'} or re.fullmatch(
                    r'url\(#[^)]+\)', normalized
                ):
                    continue
                if _parse_export_color is not None:
                    color, _alpha = _parse_export_color(normalized)
                else:
                    color = _normalize_hex_rgb(normalized)
                if color:
                    colors.add(color)
        fonts = {
            self._normalize_font_stack(value)
            for value in self._font_family_values(content)
            if self._normalize_font_stack(value)
        }
        sizes = set(self._effective_text_size_counts(prototype_root))
        return colors, fonts, sizes


    def _declared_typography_size_anchors(
        self,
        lock: Dict,
    ) -> Tuple[Dict, set[str], List[float], List[str]]:
        """Return valid declared size anchors and malformed lock rows."""
        typography = lock.get('typography', {})
        positive_numeric_re = re.compile(
            r'^(?=.*[1-9])(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$'
        )
        locked_sizes: set[str] = set()
        anchor_sizes: List[float] = []
        invalid_sizes: List[str] = []
        for key, raw_value in typography.items():
            if key == 'font_family' or key.endswith('_family'):
                continue
            value = raw_value.strip()
            if positive_numeric_re.fullmatch(value) is None:
                invalid_sizes.append(f"{key}: {raw_value}")
                continue
            try:
                anchor = float(value)
            except (TypeError, ValueError):
                invalid_sizes.append(f"{key}: {raw_value}")
                continue
            if not math.isfinite(anchor) or anchor <= 0:
                invalid_sizes.append(f"{key}: {raw_value}")
                continue
            locked_sizes.add(self._canonical_font_size_key(anchor))
            anchor_sizes.append(anchor)
        return typography, locked_sizes, anchor_sizes, invalid_sizes


    def _count_undeclared_size_occurrences(
        self,
        root: ET.Element,
        *,
        locked_sizes: set[str],
        anchor_sizes: List[float],
        prototype_sizes: set[str],
    ) -> Counter[str]:
        """Count text objects using valid sizes outside all declared bands."""
        counts: Counter[str] = Counter()
        if not locked_sizes:
            return counts
        for value, occurrence_count in self._effective_text_size_counts(root).items():
            if value in prototype_sizes and value not in locked_sizes:
                continue
            if value in locked_sizes:
                continue
            try:
                used_px = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(used_px) or used_px < 0:
                continue
            if any(
                abs(used_px - anchor_px) <= FONT_SIZE_ANCHOR_TOLERANCE_PX
                for anchor_px in anchor_sizes
            ):
                continue
            counts[value] += occurrence_count
        return counts


    def _effective_text_size_counts(self, root: ET.Element) -> Counter[str]:
        """Count each effective size once per non-empty SVG text object."""
        counts: Counter[str] = Counter()
        if _resolve_project_font_sizes is None:
            return counts
        working_root = root
        if (
            _expand_local_use_references is not None
            and _UseExpansionError is not None
        ):
            expanded_root = copy.deepcopy(root)
            try:
                _expand_local_use_references(expanded_root)
            except _UseExpansionError:
                pass
            else:
                working_root = expanded_root
        try:
            effective_sizes = _resolve_project_font_sizes(working_root)
        except ValueError:
            return counts

        def collect_text_object_sizes(element: ET.Element) -> set[str]:
            values: set[str] = set()

            def visit(node: ET.Element) -> None:
                if (node.text or '').strip():
                    values.add(
                        self._canonical_font_size_key(effective_sizes[id(node)])
                    )
                for child in node:
                    visit(child)
                    if (child.tail or '').strip():
                        values.add(
                            self._canonical_font_size_key(
                                effective_sizes[id(node)]
                            )
                        )

            visit(element)
            return values

        definition_containers = {
            'clippath',
            'defs',
            'marker',
            'mask',
            'pattern',
            'symbol',
        }

        def visit_visible(element: ET.Element) -> None:
            local_name = _local_name(element).casefold()
            if local_name in definition_containers:
                return
            if local_name == 'text':
                counts.update(collect_text_object_sizes(element))
                return
            for child in element:
                visit_visible(child)

        visit_visible(working_root)
        return counts


    @staticmethod
    def _canonical_font_size_key(value: float) -> str:
        """Canonicalize equivalent numeric spellings for deck-wide counting."""
        return format(value, '.12g')


    def _prepare_undeclared_size_occurrences(
        self,
        svg_files: List[Path],
    ) -> None:
        """Pre-count sparse undeclared sizes before per-file diagnostics."""
        previous_prototype = self._active_prototype_path
        try:
            for svg_path in svg_files:
                lock = self._get_spec_lock(svg_path)
                if lock is None:
                    continue
                _typography, locked_sizes, anchor_sizes, _invalid = (
                    self._declared_typography_size_anchors(lock)
                )
                self._active_prototype_path = self._prototype_by_output.get(
                    svg_path.resolve()
                )
                _colors, _fonts, prototype_sizes = (
                    self._prototype_drift_allowances()
                )
                try:
                    content = svg_path.read_text(encoding='utf-8')
                except OSError:
                    continue
                try:
                    root = ET.fromstring(content)
                except ET.ParseError:
                    continue
                self._undeclared_size_occurrences.update(
                    self._count_undeclared_size_occurrences(
                        root,
                        locked_sizes=locked_sizes,
                        anchor_sizes=anchor_sizes,
                        prototype_sizes=prototype_sizes,
                    )
                )
        finally:
            self._active_prototype_path = previous_prototype
            self._undeclared_size_counts_ready = True


    def _check_spec_lock_alignment(
        self,
        content: str,
        svg_path: Path,
        result: Dict,
        *,
        root: ET.Element,
    ):
        """Compare SVG values with reusable anchors in spec_lock.md.

        Covers colors (fill / stroke / stop-color / flood-color / pattern
        metadata), font-family, and font-size.
        Additional colors and font families are valid contextual authoring and
        are recorded as information. A valid undeclared display size may occur
        at most twice across generated pages; its third occurrence makes it a
        recurring role and blocks ``svg_output`` until the role is declared.
        Structural text still maps to declared role bands. Exact mirror-
        prototype values remain inherited information. Exact values are
        accumulated in self._anchor_value_summary for end-of-run aggregation.
        When spec_lock.md is missing, silently skip this local comparison; the
        Generate route's required-artifact gate owns whether execution may begin.
        """
        lock = self._get_spec_lock(svg_path)
        if lock is None:
            return
        prototype_colors, prototype_fonts, prototype_sizes = (
            self._prototype_drift_allowances()
        )

        # Build allow-sets from the lock
        allowed_colors = set()
        for v in lock.get('colors', {}).values():
            if _parse_export_color is not None:
                color, _alpha = _parse_export_color(v)
                if color:
                    allowed_colors.add(color)
            else:
                color = _normalize_hex_rgb(v)
                if color:
                    allowed_colors.add(color)

        # A validated compact preset may contain registry-derived darken/lighten
        # layer colors.  Their base paint still comes from spec_lock; the exact
        # child HEX values are deterministic compiler evidence, not color drift.
        if (
            _authored_preset_encoding is not None
            and _validate_authored_preset_group is not None
        ):
            for group in root.iter():
                if (
                    _authored_preset_encoding(group) != 'compact'
                    or _validate_authored_preset_group(group)
                ):
                    continue
                for child in group:
                    for attribute in ('fill', 'stroke'):
                        raw_value = child.get(attribute)
                        if raw_value is None:
                            continue
                        if _parse_export_color is not None:
                            color, _alpha = _parse_export_color(raw_value)
                        else:
                            color = _normalize_hex_rgb(raw_value)
                        if color:
                            allowed_colors.add(color)
        locked_colors = set(allowed_colors)
        allowed_colors.update(prototype_colors)

        typo, locked_sizes, anchor_sizes, invalid_lock_sizes = (
            self._declared_typography_size_anchors(lock)
        )
        if invalid_lock_sizes:
            shown = ', '.join(invalid_lock_sizes[:5])
            more = len(invalid_lock_sizes) - 5
            suffix = f" (+{more} more)" if more > 0 else ""
            result['errors'].append(
                f"spec_lock typography sizes must be positive finite unitless px values; "
                f"found {shown}{suffix}."
            )

        # Font families: default `font_family` plus any per-role `*_family`
        # override (title_family / body_family / emphasis_family / code_family,
        # per templates/schemas/spec_lock.schema.json). Any of these is a legitimate declared
        # value; an SVG that uses any one of them is not drifting.
        allowed_fonts = set()
        if typo:
            default_font = typo.get('font_family', '').strip()
            if default_font:
                allowed_fonts.add(self._normalize_font_stack(default_font))
            for k, v in typo.items():
                if k == 'font_family' or not k.endswith('_family'):
                    continue
                v_clean = v.strip()
                # Skip placeholder text like "same as body (omit if identical)"
                if not v_clean or v_clean.lower().startswith('same as'):
                    continue
                allowed_fonts.add(self._normalize_font_stack(v_clean))
        locked_fonts = set(allowed_fonts)
        allowed_fonts.update(prototype_fonts)

        # Sizes: declared slots are anchors. Checker cannot infer which role a
        # text node carries, so it uses the union of their ±2px bands as a cheap
        # numeric safety net; prompt rules own semantic role mapping.
        # Scan SVG for used values
        color_drifts = set()
        inherited_colors = set()
        for attr in _PAINT_PROPERTIES or ():
            for raw_value in self._svg_property_values(content, attr):
                normalized = raw_value.strip()
                if normalized.lower() in {'none', 'transparent'} or re.fullmatch(
                    r'url\(#[^)]+\)', normalized
                ):
                    continue
                if _BARE_HEX_VALUE_RE.fullmatch(normalized):
                    continue
                if _parse_export_color is not None:
                    val, _alpha = _parse_export_color(normalized)
                    if val is None:
                        continue
                else:
                    val = _normalize_hex_rgb(normalized)
                    if val is None:
                        continue
                if val not in allowed_colors:
                    color_drifts.add(f'#{val}')
                elif val in prototype_colors and val not in locked_colors:
                    inherited_colors.add(f'#{val}')

        font_drifts = set()
        inherited_fonts = set()
        for val in self._font_family_values(content):
            normalized_font = self._normalize_font_stack(val)
            if allowed_fonts and normalized_font not in allowed_fonts:
                font_drifts.add(val)
            elif (
                normalized_font in prototype_fonts
                and normalized_font not in locked_fonts
            ):
                inherited_fonts.add(val)

        size_drift_counts = self._count_undeclared_size_occurrences(
            root,
            locked_sizes=locked_sizes,
            anchor_sizes=anchor_sizes,
            prototype_sizes=prototype_sizes,
        )
        size_drifts = set(size_drift_counts)
        inherited_sizes = set()
        for val in self._effective_text_size_counts(root):
            if val in prototype_sizes and val not in locked_sizes:
                inherited_sizes.add(val)

        # Record in run-wide aggregation. Colors/fonts beyond the anchor set are
        # contextual values, not release issues. Generated-page sizes enforce
        # role-anchor ownership; other spec-backed locations retain review.
        fname = svg_path.name
        for v in color_drifts:
            self._anchor_value_summary['colors'][v].add(fname)
        for v in font_drifts:
            self._anchor_value_summary['fonts'][v].add(fname)
        for v in size_drifts:
            self._anchor_value_summary['sizes'][v].add(fname)

        contextual_values = {}
        if color_drifts:
            contextual_values['colors'] = sorted(color_drifts)
        if font_drifts:
            contextual_values['font_families'] = sorted(font_drifts)
        if contextual_values:
            result['info']['contextual_values'] = contextual_values

        sparse_sizes = {}
        recurring_sizes = {}
        for value, local_count in size_drift_counts.items():
            total_count = (
                self._undeclared_size_occurrences.get(value, local_count)
                if self._undeclared_size_counts_ready
                else local_count
            )
            target = (
                sparse_sizes
                if total_count <= SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES
                else recurring_sizes
            )
            target[value] = total_count

        if sparse_sizes:
            result['info']['sparse_typography_sizes'] = {
                value: count for value, count in sorted(sparse_sizes.items())
            }

        if recurring_sizes:
            shown = ', '.join(
                f"{value} ({count} occurrences)"
                for value, count in sorted(recurring_sizes.items())
            )
            size_issue = (
                f"undeclared font-size {shown} exceeds the sparse-display limit "
                f"of {SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES} occurrences"
            )
            if svg_path.parent.name == 'svg_output':
                result['errors'].append(
                    "spec_lock typography-size recurrence: "
                    f"{size_issue}. Structural text must return to its declared "
                    "role band; a genuinely recurring display treatment needs a "
                    "justified named role in the Design Spec and spec_lock."
                )
            else:
                result['warnings'].append(
                    f"spec_lock typography-size recurrence review: {size_issue}"
                )
        inherited_parts = []
        if inherited_colors:
            inherited_parts.append(f"{len(inherited_colors)} color(s)")
        if inherited_fonts:
            inherited_parts.append(f"{len(inherited_fonts)} font-family value(s)")
        if inherited_sizes:
            inherited_parts.append(f"{len(inherited_sizes)} font-size value(s)")
        if inherited_parts:
            self._append_inherited_info(
                result,
                'spec_lock_alignment',
                f"{', '.join(inherited_parts)} come unchanged from mirror "
                "prototype and are accepted without expanding spec_lock.md",
            )


    @staticmethod
    def _normalize_size(value: str) -> str:
        """Normalize a font-size value for drift comparison.

        Unit-bearing SVG values are reported as errors before drift checking.
        The legacy `px` strip remains to avoid a duplicate drift warning after
        the hard error has already identified the unit problem.
        """
        v = value.strip().lower()
        if v.endswith('px'):
            v = v[:-2].strip()
        return v


    @staticmethod
    def _normalize_font_stack(stack: str) -> str:
        """Normalize a font-family stack for comparison: split on commas, strip
        quotes / whitespace, lowercase, rejoin. Collapses cosmetic differences
        (comma spacing, single vs double quotes, case) so that
        `Consolas,'Courier New',monospace` matches `Consolas, "Courier New", monospace`."""
        parts = [p.strip().strip('"\'').lower() for p in stack.split(',')]
        return ','.join(p for p in parts if p)


    def _check_pptx_structure_contract(
        self,
        target_path: Path,
        svg_files: List[Path],
    ) -> None:
        """Validate the all-page structured lock and reusable contracts."""
        if self.quick_generate:
            if (
                _parse_optional_layout_slides is None
                or _TemplateStructureError is None
            ):
                self._pptx_structure_issues.append((
                    'error',
                    'Quick PPTX structure inference is unavailable because '
                    'the template_structure module could not be imported.',
                ))
                return
            try:
                specs = _parse_optional_layout_slides(svg_files)
            except _TemplateStructureError as exc:
                self._pptx_structure_issues.append(('error', str(exc)))
                return
            if specs is None:
                return
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in self._shared_fixed_layer_errors(specs)
            )
            self._pptx_structure_issues.extend(
                ('warning', message)
                for message in self._duplicate_layout_key_warnings(specs)
            )
            return
        project_path = self._resolve_project_path(target_path)
        standard_project = bool(
            not self.template_mode
            and (project_path / 'svg_output').is_dir()
        )
        declared_mode = (
            _declared_pptx_structure_mode(project_path)
            if standard_project
            else None
        )
        implicit_flat = bool(standard_project) and not declared_mode
        if implicit_flat:
            self._pptx_structure_issues.append((
                'warning',
                'spec_lock.md declares no pptx_structure.mode; the project is '
                'treated as mode: flat, matching the exporter default. Declare '
                'mode: flat explicitly, or mode: structured for a deck/layout '
                'template.',
            ))
            declared_mode = 'flat'
        if standard_project and declared_mode in {'flat', 'structured'}:
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in _generated_theme_contract_errors(project_path)
            )
        if standard_project and declared_mode == 'flat':
            if (
                _load_pptx_structure_lock is None
                or _TemplateStructureError is None
            ):
                self._pptx_structure_issues.append((
                    'error',
                    'Flat PPTX project validation is unavailable because the '
                    'template_structure module could not be imported.',
                ))
                return
            try:
                structure_lock = _load_pptx_structure_lock(project_path)
            except _TemplateStructureError as exc:
                self._pptx_structure_issues.append(('error', str(exc)))
                return
            if structure_lock is None and implicit_flat:
                return
            if structure_lock is None or structure_lock.mode != 'flat':
                self._pptx_structure_issues.append((
                    'error',
                    'spec_lock.md must contain one complete '
                    'pptx_structure.mode: flat contract.',
                ))
            return
        has_metadata = False
        for svg_path in svg_files:
            try:
                root = ET.parse(svg_path).getroot()
            except (OSError, ET.ParseError):
                continue
            if any(
                elem.get(attr) is not None
                for elem in root.iter()
                for attr in _PPTX_STRUCTURE_ATTRS
            ):
                has_metadata = True
                break

        if not standard_project and not self.template_mode and not has_metadata:
            return
        if (
            _load_pptx_structure_lock is None
            or _parse_template_structure_slide is None
            or _parse_template_structure_slides is None
            or _structure_subtree_signature is None
            or _template_lock_errors is None
            or _TemplateStructureError is None
        ):
            self._pptx_structure_issues.append((
                'error',
                'Structured PPTX project validation is unavailable because the '
                'template_structure module could not be imported.',
            ))
            return

        if self.template_mode:
            try:
                specs = _parse_template_structure_slides(svg_files)
            except _TemplateStructureError as exc:
                self._pptx_structure_issues.append(('error', str(exc)))
                return
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in self._shared_fixed_layer_errors(specs)
            )
            self._pptx_structure_issues.extend(
                ('warning', message)
                for message in self._duplicate_layout_key_warnings(specs)
            )
            return

        if standard_project and declared_mode != 'structured':
            self._pptx_structure_issues.append((
                'error',
                'release SVG projects require spec_lock.md pptx_structure.mode: '
                'flat (free design / brand-only) or structured (deck/layout '
                f'template); found {declared_mode!r}. Create a template '
                'workspace through register_template.py '
                'before generating structured SVG pages. Existing PPTX/SVG files '
                'are not upgraded in place.',
            ))
            return

        try:
            structure_lock = _load_pptx_structure_lock(project_path)
        except _TemplateStructureError as exc:
            self._pptx_structure_issues.append(('error', str(exc)))
            return
        if structure_lock is None or structure_lock.mode != 'structured':
            self._pptx_structure_issues.append((
                'error',
                'spec_lock.md must contain one complete '
                'pptx_structure.mode: structured contract.',
            ))
            return
        complete_roster = target_path.is_dir()
        try:
            if not complete_roster and target_path.is_file():
                sibling_files = discover_slide_svgs(target_path.parent)
                resolved_target = target_path.resolve()
                slide_num = next(
                    (
                        index
                        for index, sibling in enumerate(sibling_files, start=1)
                        if sibling.resolve() == resolved_target
                    ),
                    1,
                )
                specs = [
                    _parse_template_structure_slide(target_path, slide_num)
                ]
            else:
                specs = _parse_template_structure_slides(svg_files)
        except _TemplateStructureError as exc:
            self._pptx_structure_issues.append(('error', str(exc)))
            return

        self._structured_native_slots = sorted({
            str(item.placeholder)
            for spec in specs
            for item in spec.placeholders
            if item.placeholder in {'chart', 'table'}
        })
        if complete_roster:
            actual_slides = {spec.slide_num for spec in specs}
            expected_slides = {
                reference.slide_num
                for reference in structure_lock.layouts
            }
            expected_slides.update(
                reference.slide_num
                for reference in structure_lock.prototypes
            )
            self._has_incomplete_page_roster = bool(
                expected_slides - actual_slides
            )
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in _template_lock_errors(specs, structure_lock)
            )
        else:
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in self._partial_structure_lock_errors(
                    specs,
                    structure_lock,
                )
            )
        if _template_prototype_errors is not None:
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in _template_prototype_errors(
                    specs,
                    structure_lock,
                    require_complete_roster=complete_roster,
                )
            )
        self._pptx_structure_issues.extend(
            ('error', message)
            for message in self._shared_fixed_layer_errors(specs)
        )
        self._pptx_structure_issues.extend(
            ('warning', message)
            for message in self._duplicate_layout_key_warnings(specs)
        )


    @staticmethod
    def _partial_structure_lock_errors(specs, structure_lock) -> List[str]:
        """Compare explicitly checked pages without requiring the full roster."""
        references = {
            reference.slide_num: reference
            for reference in structure_lock.layouts
        }
        master_names = {
            master.master_key: master.master_name
            for master in structure_lock.masters
        }
        definitions = {
            definition.layout_key: definition
            for definition in structure_lock.layout_definitions
        }
        errors: List[str] = []
        for spec in specs:
            page = f"P{spec.slide_num:02d}"
            reference = references.get(spec.slide_num)
            if reference is None:
                errors.append(
                    f"spec_lock.md page_pptx_layouts is missing {page}"
                )
                continue
            definition = definitions.get(reference.layout_key)
            if definition is None:
                errors.append(
                    f"spec_lock.md pptx_layouts is missing Layout "
                    f"{reference.layout_key!r}"
                )
                continue
            if spec.master_key != definition.master_key:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-master={spec.master_key!r} "
                    f"does not match spec_lock Layout {reference.layout_key!r} "
                    f"Master key {definition.master_key!r}"
                )
            if spec.layout_key != reference.layout_key:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-layout={spec.layout_key!r} "
                    f"does not match spec_lock {page} layout key "
                    f"{reference.layout_key!r}"
                )
            if spec.layout_name != definition.layout_name:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-layout-name="
                    f"{spec.layout_name!r} does not match spec_lock Layout "
                    f"{reference.layout_key!r} name {definition.layout_name!r}"
                )
            expected_master_name = master_names.get(spec.master_key)
            if expected_master_name != spec.master_name:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-master-name="
                    f"{spec.master_name!r} does not match spec_lock Master "
                    f"{spec.master_key!r} name {expected_master_name!r}"
                )
        return errors


    def _duplicate_layout_key_warnings(self, specs) -> List[str]:
        """Flag distinct layout keys whose static contracts are identical.

        Keys split by page topic over one shared skeleton compile into
        duplicate PowerPoint Layouts; the fingerprint compares the
        id-insensitive layout-layer drawing plus the placeholder contract.
        """
        prototypes: Dict[Tuple[str, str], Path] = {}
        for spec in specs:
            prototypes.setdefault(
                (getattr(spec, 'master_key', ''), spec.layout_key),
                spec.svg_path,
            )
        if len(prototypes) < 2:
            return []
        fingerprint_keys: Dict[tuple, List[str]] = {}
        for (master_key, layout_key), svg_path in prototypes.items():
            fingerprint = self._layout_contract_fingerprint(svg_path)
            if fingerprint is None:
                continue
            fingerprint_keys.setdefault(
                (master_key, fingerprint),
                [],
            ).append(layout_key)
        messages = []
        for keys in fingerprint_keys.values():
            if len(keys) < 2:
                continue
            joined = ', '.join(sorted(keys))
            messages.append(
                f"layout keys {joined} declare identical static Layout framing "
                "and placeholder contracts; they compile to duplicate Layouts. "
                "Either merge them into one reusable key (spec_lock.md "
                "pptx_layouts + each SVG root), or — when their reusable "
                "contracts genuinely differ — assign distinct explicit default "
                "placeholder bounds and/or mark only truly stable framing as "
                'data-pptx-layer="layout". Slide-local content geometry does not '
                "define a Layout. This recommendation is advisory; no change or "
                "disposition is required."
            )
        return messages


    @classmethod
    def _shared_fixed_layer_errors(cls, specs) -> List[str]:
        """Reject fixed atoms whose payload varies inside one reuse scope."""
        master_groups = defaultdict(list)
        layout_groups = defaultdict(list)
        for spec in specs:
            master_groups[spec.master_key].append(spec)
            layout_groups[(spec.master_key, spec.layout_key)].append(spec)

        try:
            errors = cls._fixed_layer_group_errors(master_groups, 'master')
            errors.extend(cls._fixed_layer_group_errors(layout_groups, 'layout'))
        except _TemplateStructureError as exc:
            return [str(exc)]
        return errors


    @classmethod
    def _fixed_layer_group_errors(cls, groups, layer: str) -> List[str]:
        """Compare fixed atom payloads across grouped slide specifications."""
        errors = []
        for scope_key, group_specs in groups.items():
            if len(group_specs) < 2:
                continue
            variants = defaultdict(lambda: defaultdict(list))
            for spec in group_specs:
                payloads = cls._fixed_layer_payloads(spec, layer)
                for element_id, payload in payloads.items():
                    variants[element_id][payload].append(spec)
            for element_id, payload_specs in variants.items():
                if len(payload_specs) < 2:
                    continue
                slide_names = ', '.join(
                    spec.svg_path.name
                    for spec in sorted(group_specs, key=lambda item: item.slide_num)
                )
                if layer == 'master':
                    scope = f"Master {scope_key!r}"
                else:
                    master_key, layout_key = scope_key
                    scope = (
                        f"Layout {layout_key!r} under Master {master_key!r}"
                    )
                if element_id is None:
                    subject = "fixed visual resources"
                    verb = "differ"
                else:
                    subject = f"fixed element {element_id!r}"
                    verb = "differs"
                errors.append(
                    f"{scope} {subject} {verb} across slides: "
                    f"{slide_names}. Values marked data-pptx-layer={layer!r} must "
                    "remain identical throughout their reuse scope; move variable "
                    "text or images into a placeholder slot or keep them Slide-local."
                )
        return errors


    @staticmethod
    def _fixed_layer_payloads(spec, layer: str) -> Dict[object, tuple]:
        """Return resolved fixed-layer visual payloads keyed by SVG id."""
        elements = (
            spec.master_elements if layer == 'master' else spec.layout_elements
        )
        if not elements:
            return {}
        signature = _structure_subtree_signature(
            spec.svg_path,
            elements,
            include_skin=True,
            include_text=True,
            asset_identity=True,
        )
        return {
            None if element_id == '__visual_resources__' else element_id: payload
            for element_id, payload in signature
        }


    @staticmethod
    def _layout_contract_fingerprint(svg_path: Path):
        """Id-insensitive static contract: layout-layer XML + placeholder slots."""
        try:
            root = ET.parse(str(svg_path)).getroot()
        except (OSError, ET.ParseError):
            return None
        layout_parts = []
        placeholder_parts = []
        for child in list(root):
            if child.get('data-pptx-layer') == 'layout':
                clone = copy.deepcopy(child)
                for elem in clone.iter():
                    elem.attrib.pop('id', None)
                xml = ET.tostring(clone, encoding='unicode')
                layout_parts.append(re.sub(r'\s+', ' ', xml).strip())
            placeholder = child.get('data-pptx-placeholder')
            if placeholder is not None:
                carrier_tags = tuple(
                    grandchild.tag.rsplit('}', 1)[-1]
                    for grandchild in list(child)
                    if (
                        grandchild.get('data-pptx-carrier') or ''
                    ).strip().lower() == 'true'
                )
                placeholder_parts.append((
                    placeholder,
                    child.tag.rsplit('}', 1)[-1],
                    child.get('data-pptx-bounds') or '',
                    child.get('data-pptx-idx') or '',
                    (
                        child.get('data-pptx-binding') or 'carrier'
                    ).strip().lower(),
                    carrier_tags,
                ))
        return (
            tuple(layout_parts),
            tuple(sorted(placeholder_parts)),
        )


    def _check_template_contract(
        self,
        dir_path: Path,
        svg_files: List[Path],
        *,
        check_structure: bool,
    ) -> None:
        """Check reusable-template structure, roster, and placeholder hints.

        - **Roster mismatch (orphan / missing)** is reported as an *error*: a
          stale roster will produce a wrong ``layouts_index.json`` entry.
        - **Explicit structure gaps** are errors when positive structure checks
          are enabled: every current reusable SVG declares its Master and Layout
          identity. Zero-placeholder Layouts are valid. Legacy template-mode
          packages fail and must be replaced by a new create-template workspace.
        - **Placeholder gaps** are reported as *warnings*. Templates may
          legitimately omit conventional placeholders or swap them out (e.g.
          ``{{CLOSING_MESSAGE}}`` instead of ``{{THANK_YOU}}``), and a content
          variant may use a bespoke slot vocabulary. Designers can declare
          their own per-stem expectations via ``placeholders:`` frontmatter
          in ``design_spec.md`` to suppress these warnings explicitly.

        Issues are aggregated and printed in :py:meth:`print_summary` so the
        per-file report stays focused on intrinsic SVG validity.
        """
        spec_path = _roster_spec_path(dir_path)
        spec_text = (
            spec_path.read_text(encoding='utf-8')
            if spec_path is not None and spec_path.exists()
            else ""
        )
        declared_structure_mode = _declared_template_structure_mode(dir_path)
        mode_error_recorded = False
        if declared_structure_mode != 'structured':
            mode_error_recorded = True
            self._template_issues.append((
                'error',
                'explicit_structure_mode',
                "design_spec.md frontmatter must declare "
                "native_structure_mode: structured; legacy template-mode "
                "workspaces must be re-created through create-template",
            ))
        if check_structure:
            native_contract_path = dir_path / NATIVE_STRUCTURE_PATH
            source_template_path = dir_path / SOURCE_PPTX_PATH
            legacy_structure_detected = False
            for svg_file in svg_files:
                try:
                    root = ET.parse(svg_file).getroot()
                except (OSError, ET.ParseError):
                    continue
                if not root.get('data-pptx-master'):
                    legacy_structure_detected = True
                    self._template_issues.append((
                        'error',
                        'explicit_master_missing',
                        f"{svg_file.name}: reusable templates require root "
                        "data-pptx-master metadata",
                    ))
                if not root.get('data-pptx-master-name'):
                    legacy_structure_detected = True
                    self._template_issues.append((
                        'error',
                        'explicit_master_name_missing',
                        f"{svg_file.name}: reusable templates require root "
                        "data-pptx-master-name metadata",
                    ))
                if not root.get('data-pptx-layout'):
                    self._template_issues.append((
                        'error',
                        'explicit_structure_missing',
                        f"{svg_file.name}: reusable templates require root "
                        "data-pptx-layout metadata",
                    ))
                if not root.get('data-pptx-layout-name'):
                    self._template_issues.append((
                        'error',
                        'explicit_structure_name_missing',
                        f"{svg_file.name}: reusable templates require root "
                        "data-pptx-layout-name metadata",
                    ))
                if root.get('data-pptx-layout-kind') is not None:
                    legacy_structure_detected = True
                    self._template_issues.append((
                        'error',
                        'deck_instance_layout_kind',
                        f"{svg_file.name}: reusable template prototypes must omit "
                        "legacy data-pptx-layout-kind metadata",
                    ))
                if any(
                    child.get('data-pptx-placeholder') is not None
                    and child.tag.rsplit('}', 1)[-1] != 'g'
                    for child in list(root)
                ):
                    legacy_structure_detected = True
                missing_bounds = [
                    child.get('id') or child.tag.rsplit('}', 1)[-1]
                    for child in list(root)
                    if child.get('data-pptx-placeholder') is not None
                    and child.get('data-pptx-bounds') is None
                ]
                if missing_bounds:
                    legacy_structure_detected = True
                    self._template_issues.append((
                        'error',
                        'placeholder_bounds_missing',
                        f"{svg_file.name}: reusable templates require "
                        "explicit design-zone data-pptx-bounds; missing: "
                        + ', '.join(missing_bounds),
                    ))
            if native_contract_path.exists() or source_template_path.exists():
                legacy_structure_detected = True
                self._template_issues.append((
                    'error',
                    'legacy_native_structure_pair',
                    "source-analysis native_structure/source.pptx contracts "
                    "must not be packaged as reusable template inputs; rebuild "
                    "through "
                    "register_template.py",
                ))

            if declared_structure_mode != 'structured':
                legacy_structure_detected = True
                if not mode_error_recorded:
                    self._template_issues.append((
                        'error',
                        'explicit_structure_mode',
                        "design_spec.md frontmatter must declare "
                        "native_structure_mode: structured",
                    ))
            if legacy_structure_detected:
                self._template_issues.append((
                    'error',
                    'legacy_structure_contract',
                    "legacy template structure detected; create a new current "
                    "workspace through register_template.py before Step 3 consumption",
                ))
        spec_pages = self._extract_spec_roster(spec_text) if spec_text else []
        custom_contract = self._extract_frontmatter_placeholders(spec_text) if spec_text else {}

        on_disk = {p.stem for p in svg_files}

        if spec_pages:
            spec_set = set(spec_pages)
            orphan = sorted(on_disk - spec_set)
            missing = sorted(spec_set - on_disk)
            for page in orphan:
                self._template_issues.append((
                    'error',
                    'roster_orphan',
                    f"{page}.svg exists on disk but is not listed in design_spec.md Page Roster",
                ))
            for page in missing:
                self._template_issues.append((
                    'error',
                    'roster_missing',
                    f"design_spec.md Page Roster lists {page} but {page}.svg is missing on disk",
                ))
        elif spec_path is not None and spec_path.exists():
            # design_spec.md is present but the roster parser found nothing —
            # reusable template workspaces always fail closed.
            self._template_issues.append((
                'error',
                'roster_unknown',
                f"could not extract page roster from {spec_path.name}; "
                "skipping orphan/missing checks",
            ))
        else:
            self._template_issues.append((
                'error',
                'spec_missing',
                "one Layout or Deck Design Spec is required for every SVG roster",
            ))

        # Per-file placeholder coverage. Variants reuse the parent type's set
        # (e.g. 03a_content_two_col.svg ↔ 03_content rules) unless the spec
        # frontmatter overrides that page (custom_contract takes precedence).
        for svg_file in svg_files:
            expected = self._lookup_template_contract(
                svg_file.stem, overrides=custom_contract,
            )
            if expected is None:
                continue  # extension pages or stems with no convention
            try:
                content = svg_file.read_text(encoding='utf-8')
            except OSError:
                continue
            for placeholder in expected:
                if placeholder not in content:
                    self._template_issues.append((
                        'warning',
                        'placeholder_hint',
                        f"{svg_file.name}: missing conventional placeholder {placeholder} "
                        "(declare 'placeholders:' frontmatter in design_spec.md to silence)",
                    ))


    @staticmethod
    def _extract_frontmatter_placeholders(spec_text: str) -> Dict[str, Tuple[str, ...]]:
        """Read the optional ``placeholders:`` map from design_spec.md frontmatter.

        Shape:

        .. code-block:: yaml

            placeholders:
              01_cover: ["{{TITLE}}", "{{BRAND_LOGO}}"]
              03_content: []        # explicitly assert "no expectation"
              03a_content_two_col:  # variant-specific override
                - "{{LEFT_TITLE}}"
                - "{{RIGHT_TITLE}}"

        Each key is a stem (full filename without ``.svg``) or page-type prefix
        (``01_cover``). An empty list silences the default convention for that
        stem; a populated list replaces the default. Stems / prefixes not
        listed fall back to ``DEFAULT_PLACEHOLDER_CONVENTION``.

        We parse with PyYAML when available; otherwise we fall back to a
        minimal regex that handles the documented shape.
        """
        if not spec_text.startswith("---\n"):
            return {}
        end = spec_text.find("\n---\n", 4)
        if end == -1:
            return {}
        block = spec_text[4:end]

        try:
            import yaml  # type: ignore
        except ImportError:
            return _parse_placeholders_fallback(block)

        try:
            data = yaml.safe_load(block) or {}
        except yaml.YAMLError:
            return {}
        if not isinstance(data, dict):
            return {}
        raw = data.get("placeholders")
        if not isinstance(raw, dict):
            return {}

        out: Dict[str, Tuple[str, ...]] = {}
        for stem, value in raw.items():
            if not isinstance(stem, str):
                continue
            if isinstance(value, list):
                out[stem] = tuple(str(v) for v in value)
            elif value is None:
                out[stem] = ()
        return out


    @staticmethod
    def _extract_spec_roster(spec_text: str) -> List[str]:
        """Best-effort: extract the page roster from design_spec.md.

        Templates do not share a uniform section index for the roster — the
        personality-only skeleton puts it at §V "Page Roster"; legacy specs use
        §VI "Page Roster" or bury filenames under §VII "Page Types" as
        ``### N. Cover Page (01_cover.svg)``. We match by title (any roman
        index), then fall back to scanning the whole document for any
        backtick-wrapped ``<stem>.svg`` reference.

        Returns the deduplicated stem list in document order. Empty result
        means we can't determine the roster confidently — caller should treat
        that as "skip orphan/missing checks", not as "no pages declared".
        """
        # Pass 1: explicit roster section, any roman numeral.
        sections = list(re.finditer(
            r"^##\s+[IVX]+\.\s+(?:(?:SVG\s+)?Page Roster|Page Structure|Pages|Page Types)\b.*?(?=^##\s+|\Z)",
            spec_text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        ))
        roster_scope = next(
            (
                section.group(0)
                for section in sections
                if re.match(
                    r"^##\s+[IVX]+\.\s+(?:SVG\s+)?Page Roster\b",
                    section.group(0),
                    re.IGNORECASE,
                )
            ),
            None,
        )
        scope = roster_scope or next(
            (
                section.group(0)
                for section in sections
                if re.search(r"[`\(][0-9A-Za-z_]+\.svg[`\)]", section.group(0))
            ),
            sections[0].group(0) if sections else None,
        )

        # Pass 2: full document. We *only* trust this scan when the explicit
        # roster scan came up empty (no `<stem>.svg` references inside it) —
        # otherwise the explicit section's deliberate roster wins over loose
        # mentions elsewhere.
        explicit_scope = bool(
            scope and re.search(r"[`\(][0-9A-Za-z_]+\.svg[`\)]", scope)
        )
        if explicit_scope:
            text = scope
        else:
            text = spec_text

        stems: List[str] = []
        seen: set = set()
        # Accept backtick-quoted (`01_cover.svg`) and parenthesized
        # (01_cover.svg) forms — existing specs use either.
        svg_ref_re = re.compile(r"[`\(]([0-9A-Za-z_]+\.svg)[`\)]")
        for match in svg_ref_re.finditer(text):
            stem = match.group(1)[:-4]
            if stem in seen or (not explicit_scope and not re.match(r"^\d", stem)):
                continue
            seen.add(stem)
            stems.append(stem)

        # If the explicit §VI scan listed bare stems (without .svg), accept
        # those as fallback — but only when they were inside that section.
        if not stems and scope:
            for match in re.finditer(r"`([0-9]{2}[a-z]?_[A-Za-z0-9_]+)`", scope):
                stem = match.group(1)
                if stem in seen:
                    continue
                seen.add(stem)
                stems.append(stem)

        return stems


    @classmethod
    def _lookup_template_contract(
        cls, stem: str, *,
        overrides: Dict[str, Tuple[str, ...]] | None = None,
    ) -> Tuple[str, ...] | None:
        """Resolve a SVG stem to its expected placeholder set.

        Resolution order, first hit wins:
        1. ``overrides[stem]`` — frontmatter entry for the exact filename
        2. ``overrides[<page_type_prefix>]`` — frontmatter entry for the
           variant's parent type (e.g. ``03_content`` for
           ``03a_content_two_col``)
        3. ``DEFAULT_PLACEHOLDER_CONVENTION[<page_type>]`` — keyed by the
           type token alone, so it applies regardless of where the type
           lands in the template's presentation-order numbering

        Returns ``None`` for stems with no matching convention or override —
        e.g. extension pages like ``05_section_break``. ``()`` (empty tuple)
        is a valid value meaning "no expected placeholders" — used to
        explicitly silence the default convention.
        """
        overrides = overrides or {}
        if stem in overrides:
            return overrides[stem]

        # Variant convention: <NN><letter>?_<rest>; strip the letter to find
        # the parent type prefix, e.g. "03a_content_two_col" -> "03_content".
        match = re.match(r"^(\d{2})([a-z])?_([a-z]+)", stem)
        if not match:
            return None
        num, _letter, kind = match.groups()
        key = f"{num}_{kind}"
        if key in overrides:
            return overrides[key]
        return cls.DEFAULT_PLACEHOLDER_CONVENTION.get(kind)


