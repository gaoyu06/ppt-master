"""SVG linter: the compiler's pre-export validation pass.

Composes the per-concern check mixins in this package. Hard language
errors are reported by the converter itself; this pass adds the
advisory quality checks (fonts, bounds, structure, resources).
"""

from .common import *  # noqa: F401,F403
from .dialect import DialectChecks
from .text import TextChecks
from .text_geometry import TextGeometryChecks
from .resources import ResourceChecks
from .semantics import SemanticChecks
from .structure import StructureChecks
from .roundtrip import RoundtripChecks
from .report import ReportMixin


class SVGLinter(DialectChecks, TextChecks, TextGeometryChecks, ResourceChecks, SemanticChecks, StructureChecks, RoundtripChecks, ReportMixin):
    """Lint project SVG pages before PPTX export."""

    """SVG quality checker"""
    DEFAULT_PLACEHOLDER_CONVENTION = {
        "cover": ("{{TITLE}}",),  # only the title is universally expected
        "chapter": ("{{CHAPTER_TITLE}}",),
        "toc": (),  # TOC layouts vary too widely to assert anything
        "content": ("{{PAGE_TITLE}}",),
        "ending": (),  # ending pages legitimately use varied vocabularies
    }
    _OOXML_PATTERN_PRESETS = frozenset({
        'pct5', 'pct10', 'pct20', 'pct25', 'pct30', 'pct40', 'pct50', 'pct60',
        'pct70', 'pct75', 'pct80', 'pct90',
        'horz', 'vert', 'ltHorz', 'ltVert', 'dkHorz', 'dkVert',
        'narHorz', 'narVert', 'dashHorz', 'dashVert',
        'cross', 'dnDiag', 'upDiag', 'ltDnDiag', 'ltUpDiag', 'dkDnDiag',
        'dkUpDiag', 'wdDnDiag', 'wdUpDiag',
        'dashDnDiag', 'dashUpDiag', 'diagCross',
        'smCheck', 'lgCheck', 'smGrid', 'lgGrid', 'dotGrid', 'smConfetti',
        'lgConfetti', 'horzBrick', 'diagBrick', 'solidDmnd', 'openDmnd',
        'dotDmnd', 'plaid', 'sphere', 'weave', 'wave', 'trellis', 'zigZag',
        'divot', 'shingle',
    })

    def __init__(
        self,
        *,
        template_mode: bool = False,
        quick_generate: bool = False,
        canonical_authoring: bool = False,
    ):
        self.template_mode = template_mode
        self._image_pixel_sizes: Dict[Path, Tuple[int, int]] = {}
        self.scan_banner = True
        self.quick_generate = quick_generate
        self.canonical_authoring = canonical_authoring
        self.results = []
        self.summary = {
            'total': 0,
            'passed': 0,
            'warnings': 0,
            'errors': 0
        }
        self.issue_types = defaultdict(int)
        # spec_lock anchor comparison state (populated only when
        # _parse_spec_lock is available and a spec_lock.md is found near the SVG)
        self._lock_cache: Dict[Path, Dict] = {}
        self._anchor_value_summary: Dict[str, Dict[str, set]] = {
            'colors': defaultdict(set),
            'fonts': defaultdict(set),
            'sizes': defaultdict(set),
        }
        self._undeclared_size_occurrences: Counter[str] = Counter()
        self._undeclared_size_counts_ready = False
        self._lock_seen = False  # True once we locate at least one spec_lock.md
        self._source_manifest_cache: Dict[
            Path,
            Tuple[Dict, str | None],
        ] = {}
        self._source_manifest_errors_reported: set[Path] = set()
        # Template-mode aggregation (populated by check_directory when
        # template_mode=True). Each entry is (severity, kind, message) where
        # severity is 'error' or 'warning'. Printed in print_summary.
        self._template_issues: List[Tuple[str, str, str]] = []
        self._spec_only_template_kind: str | None = None
        self._animation_issues: List[Tuple[str, str]] = []
        self._communication_trace_issues: List[Tuple[str, str]] = []
        self._communication_traced_projects: set[Path] = set()
        self._pptx_structure_issues: List[Tuple[str, str]] = []
        self._has_incomplete_page_roster = False
        self._structured_native_slots: list[str] = []
        self._active_slide_count: int | None = None
        # Early/page stages see part of the roster, so a slide jump's upper
        # bound waits for the final gate.
        self.partial_roster = False
        self._prototype_by_output: Dict[Path, Path] = {}
        self._active_prototype_path: Path | None = None
        self._active_template_reuse_scope: str | None = None
        self._prototype_root_cache: Dict[Path, ET.Element | None] = {}
        self._source_import_summary: Dict[str, object] = {
            'warning_count': 0,
            'by_code': {},
        }
        self._aggregate_counts_applied = False


    @staticmethod
    def _append_inherited_info(
        result: Dict,
        kind: str,
        message: str,
    ) -> None:
        """Record prototype-owned diagnostics outside the warning channel."""
        result['info'].setdefault('inherited', []).append({
            'kind': kind,
            'message': message,
        })


    def _active_prototype_root(self) -> ET.Element | None:
        """Parse the selected mirror prototype once for inherited checks."""
        if (
            self._active_template_reuse_scope != 'mirror'
            or self._active_prototype_path is None
        ):
            return None
        path = self._active_prototype_path.resolve()
        if path in self._prototype_root_cache:
            return self._prototype_root_cache[path]
        try:
            root = ET.parse(path).getroot()
            hydrate_native_payload_refs(root, path)
        except (OSError, ET.ParseError, NativePayloadError):
            root = None
        self._prototype_root_cache[path] = root
        return root


    def check_file(
        self,
        svg_file: str,
        expected_format: str = None,
        *,
        expected_viewbox: str | None = None,
        expected_viewbox_label: str = "expected canvas",
    ) -> Dict:
        """
        Check a single SVG file

        Args:
            svg_file: SVG file path
            expected_format: Expected canvas format (e.g., 'ppt169')

        Returns:
            Check result dictionary
        """
        svg_path = Path(svg_file)

        if not svg_path.exists():
            return {
                'file': str(svg_file),
                'exists': False,
                'errors': ['File does not exist'],
                'warnings': [],
                'passed': False
            }

        result = {
            'file': svg_path.name,
            'path': str(svg_path),
            'exists': True,
            'errors': [],
            'warnings': [],
            'info': {},
            'passed': True
        }

        try:
            source_bytes = svg_path.read_bytes()
            result['source_sha256'] = hashlib.sha256(source_bytes).hexdigest()
            content = source_bytes.decode('utf-8')

            # 0. Parse XML once — every other check assumes the file is valid
            # XML. Bail early on failure so the regex-based checks below don't
            # produce misleading errors on a broken document.
            root = self._parse_xml_root(content, result)
            if root is not None:
                self._check_canonical_authoring(root, result)
                if normalize_language_attrs is not None:
                    try:
                        normalize_language_attrs(root, svg_path.stem)
                    except ValueError as exc:
                        result['errors'].append(f'{svg_path.name}: {exc}')
                try:
                    hydrated_payloads = hydrate_native_payload_refs(root, svg_path)
                except NativePayloadError as exc:
                    result['errors'].append(
                        f"Invalid native payload reference: {exc}"
                    )
                else:
                    if hydrated_payloads:
                        result['info']['native_payload_refs'] = hydrated_payloads

                if (
                    self.quick_generate
                    and svg_path.name == sorted(
                        p.name for p in svg_path.parent.glob('*.svg')
                    )[0]
                    and not (
                        root.get('lang')
                        or root.get('{http://www.w3.org/XML/1998/namespace}lang')
                    )
                ):
                    result['warnings'].append(
                        'Quick roster declares no deck language: put '
                        'lang="<BCP-47>" (vi-VN, he-IL, ...) on the first '
                        "page's root <svg>; export reads it for run proofing "
                        'language, right-to-left defaults, theme script slots '
                        'and docProps (advisory)'
                    )

                # 1. Check viewBox
                self._check_viewbox(
                    root,
                    svg_path,
                    result,
                    expected_format,
                    expected_viewbox=expected_viewbox,
                    expected_viewbox_label=expected_viewbox_label,
                )
                self._check_legacy_pptx_attributes(root, svg_path, result)
                self._record_carrier_receipt(root, result)

                # 1a. Validate exact importer transport before compatible
                # inline geometry is materialized on the shared tree.
                svg_contracts.check_nested_svg_crop_contract(root, result)

                # 2. Check forbidden elements
                svg_contracts.check_forbidden_elements(content, root, result)
                svg_contracts.check_mask_contract(root, result)

                # 2a. Validate direct geometry lengths and stroke widths.
                svg_contracts.check_geometry_length_values(root, result)
                self._check_shape_coordinate_ranges(root, result)

                # 2b. Validate line-presentation grammar and mappings.
                svg_contracts.check_stroke_style_values(root, result)

                # 2c. Validate image fit/crop grammar and mappings.
                self._check_image_contract(root, svg_path, result)
                svg_contracts.check_image_aspect_ratio_values(root, result)

                # 2d. Validate complete path-data and point-list grammar.
                svg_contracts.check_freeform_geometry_values(root, result)

                # 2e. Validate complete transform grammar and native mappings.
                svg_contracts.check_transform_values(root, result)

                # 2f. Validate opacity grammar and native alpha mappings.
                svg_contracts.check_opacity_values(root, result)

                # 2g. Validate the closed authoring-property surface and
                # conditional definition interfaces before export.
                svg_contracts.check_authoring_property_contract(root, result)
                svg_contracts.check_text_property_contract(root, result)
                self._check_preserved_txbody_contract(root, result)
                svg_contracts.check_paint_compatibility(root, result)
                svg_contracts.check_reference_spelling(root, result)
                svg_contracts.check_definition_contract(root, result)
                svg_contracts.check_paint_reference_contract(root, result)
                svg_contracts.check_marker_contract(root, result)
                svg_contracts.check_clip_path_contract(root, result)

                # 2h. Validate the supported shadow/glow filter interface.
                svg_contracts.check_imported_effect_status(root, result)
                svg_contracts.check_filter_effects(root, result)

                # 2i. Validate gradient definitions, stops, and coordinates.
                svg_contracts.check_gradient_interfaces(root, result)

                # 3. Check font-size values
                svg_contracts.check_font_size_values(content, result)

                # 4. Check fonts
                self._check_fonts(content, result)

                # 5. Check text wrapping methods
                self._check_text_elements(content, root, result)

                # 5b. Validate native hyperlink targets and carrier structure.
                self._check_hyperlinks(root, result)

                # 6. Check image references (file existence and resolution)
                self._check_image_references(root, svg_path, result)

                # 7. Check icon placeholders resolve before post-processing.
                self._check_icon_placeholders(root, svg_path, result)

                # 7b. Reject visual elements the native converter cannot dispatch.
                self._check_unsupported_visual_elements(root, result)
                self._check_hidden_elements(root, result)

                # 7c. Fail closed on invalid PPTX preset/adjustment metadata.
                self._check_preset_geometry_metadata(root, result)
                self._check_preset_geometry_transforms(root, result)

                # 8. Check object-level animation anchor quality.
                self._check_animation_group_ids(root, svg_path, result)

                # 8b. Check <pattern> elements declare a PPTX preset.
                self._check_pattern_fills(root, result)

                # 8c. Check explicit native replacement markers before export.
                self._check_native_object_markers(root, result)

                # 8d. Validate explicit master/layout/placeholder metadata.
                if (
                    _template_structure_checks_enabled(svg_path)
                    if self.template_mode
                    else _CHECK_PPTX_STRUCTURED_PROJECT
                ):
                    self._check_pptx_structure_metadata(root, svg_path, result)

                # 8e. Validate rendering-neutral page/structure compiler hints.
                self._check_semantic_markers(root, svg_path, result)

                # 9. Compare values with spec_lock anchors. Additional colors
                #    and fonts are informational. Generated-page type sizes may
                #    stay sparse twice; the third occurrence is an error. Other
                #    spec-backed SVG locations retain advisory review. Templates
                #    do not ship a spec_lock.md, so skip in template mode.
                if not self.template_mode:
                    self._check_spec_lock_alignment(
                        content,
                        svg_path,
                        result,
                        root=root,
                    )

            # Determine pass/fail
            result['passed'] = len(result['errors']) == 0

        except Exception as e:
            result['errors'].append(f"Failed to read file: {e}")
            result['passed'] = False

        return self._record_result(result)


    def _record_result(self, result: Dict) -> Dict:
        """Append one file result and update aggregate counters."""
        self.summary['total'] += 1
        if result['passed']:
            if result['warnings']:
                self.summary['warnings'] += 1
            else:
                self.summary['passed'] += 1
        else:
            self.summary['errors'] += 1

        # Categorize issue types
        for error in result['errors']:
            self.issue_types[self._categorize_issue(error)] += 1

        self.results.append(result)
        return result


    def _parse_xml_root(self, content: str, result: Dict) -> ET.Element | None:
        """Parse the SVG content as well-formed XML.

        SVG is strict XML.  AI-generated decks frequently produce content that
        looks fine in HTML5-tolerant previews but fails strict XML parsing —
        common causes are HTML named entities (&nbsp; &mdash; &copy;…) and
        bare XML reserved characters in text (R&D, error < 5%).  Such pages
        cannot be exported to PPTX, so we surface them here as a hard error
        before any downstream check looks at them.

        Returns the parsed root when the document is well-formed; otherwise
        appends an error and returns None.
        """
        try:
            return ET.fromstring(content)
        except ET.ParseError as e:
            result['errors'].append(
                f"Invalid XML: {e} — SVG must be well-formed XML. "
                f"Use raw Unicode for typography (—, ©, →, NBSP); "
                f"escape XML reserved chars as &amp; &lt; &gt; &quot; &apos; "
                f"(see references/shared-standards-core.md §1)."
            )
            return None


    @staticmethod
    def _resolve_project_path(dir_path: Path) -> Path:
        """Resolve a checker target directory to its project root."""
        candidate = dir_path.parent if dir_path.is_file() else dir_path
        if (
            _project_root_for_svg_path is not None
            and candidate.name in _SVG_WORK_DIR_NAMES
        ):
            return _project_root_for_svg_path(candidate)
        if (
            (candidate / 'svg_output').exists()
            or (candidate / 'design_spec.md').exists()
        ):
            return candidate
        return candidate.parent


    def _categorize_issue(self, error_msg: str) -> str:
        """Categorize issue type"""
        if 'Invalid XML' in error_msg:
            return 'XML well-formedness'
        elif 'viewBox' in error_msg:
            return 'viewBox issues'
        elif 'foreignObject' in error_msg:
            return 'foreignObject'
        elif 'paint' in error_msg.lower() or 'color value' in error_msg.lower():
            return 'Paint issues'
        elif 'font' in error_msg.lower():
            return 'Font issues'
        else:
            return 'Other'


    def _configure_prototype_context(
        self,
        target_path: Path,
        svg_files: List[Path],
    ) -> None:
        """Map generated pages to selected prototypes for inherited diagnostics."""
        self._prototype_by_output = {}
        self._active_prototype_path = None
        self._active_template_reuse_scope = None
        self._source_import_summary = {
            'warning_count': 0,
            'by_code': {},
        }
        if (
            self.template_mode
            or self.quick_generate
            or _load_pptx_structure_lock is None
        ):
            return
        project_path = self._resolve_project_path(target_path)
        try:
            structure_lock = _load_pptx_structure_lock(project_path)
        except (_TemplateStructureError, OSError):
            # The project-level structure gate reports the actionable parser
            # error. Inherited classification is optional and stays silent.
            return
        if structure_lock is None:
            return
        self._active_template_reuse_scope = getattr(
            structure_lock,
            'template_reuse_scope',
            None,
        )
        references = {
            reference.slide_num: reference.svg_path
            for reference in structure_lock.prototypes
        }
        if target_path.is_file():
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
            prototype = references.get(slide_num)
            if prototype is not None:
                self._prototype_by_output[resolved_target] = prototype.resolve()
        else:
            for slide_num, svg_path in enumerate(svg_files, start=1):
                prototype = references.get(slide_num)
                if prototype is not None:
                    self._prototype_by_output[svg_path.resolve()] = prototype.resolve()

        if self._active_template_reuse_scope not in {'mirror', 'layout'}:
            return
        manifest_path = (
            project_path / 'templates' / 'template_execution_manifest.json'
        )
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if manifest.get('schema') != 'ppt-master.template-execution-manifest.v1':
            return
        source_import = manifest.get('source_import')
        if isinstance(source_import, dict):
            self._source_import_summary = source_import


    def check_directory(self, directory: str, expected_format: str = None) -> List[Dict]:
        """
        Check all SVG files in a directory

        Args:
            directory: Directory path
            expected_format: Expected canvas format

        Returns:
            List of check results
        """
        dir_path = Path(directory)
        self._has_incomplete_page_roster = False
        self._structured_native_slots: list[str] = []
        self._undeclared_size_occurrences = Counter()
        self._undeclared_size_counts_ready = False

        if not dir_path.exists():
            print(f"[ERROR] Directory does not exist: {directory}")
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []

        # Brand and Style workspaces have no SVG roster. Validate their
        # portable contracts through the same authority used by library
        # registration, while keeping project scope independent of global
        # indexes and directory names.
        if self.template_mode and dir_path.is_dir():
            nested = dir_path / 'templates'
            spec_dir = nested if _template_spec_paths(nested) else dir_path
            specs = _template_spec_paths(spec_dir)
            bare = [spec for spec in specs if spec.name == 'design_spec.md']
            qualified = [spec for spec in specs if spec.name != 'design_spec.md']
            if bare and qualified:
                self._template_issues.append((
                    'error',
                    'spec_naming',
                    'design_spec.md and design_spec.<kind>.<id>.md cannot share '
                    f'{spec_dir}; rename the bare spec to its kind-qualified name',
                ))
                return self.results
            try:
                from register_template import (
                    SpecParseError,
                    validate_qualified_spec_identity,
                )
                for spec in qualified:
                    validate_qualified_spec_identity(spec)
            except ImportError as exc:
                self._template_issues.append((
                    'error',
                    'spec_naming',
                    f'Qualified Design Spec validator could not be imported: {exc}',
                ))
                return self.results
            except (OSError, SpecParseError) as exc:
                self._template_issues.append((
                    'error',
                    'spec_naming',
                    str(exc),
                ))
                return self.results
            declared_kinds = [
                kind
                for spec in qualified
                for kind in [_spec_declared_kind(spec)]
                if kind is not None
            ]
            duplicate_kinds = sorted({
                kind for kind in declared_kinds
                if declared_kinds.count(kind) > 1
            })
            if duplicate_kinds:
                self._template_issues.append((
                    'error',
                    'spec_naming',
                    f'{spec_dir} declares the same kind more than once: '
                    + ', '.join(duplicate_kinds),
                ))
                return self.results
            active_roster_spec = _roster_spec_path(spec_dir)
            shadowed_deck_specs = [
                spec
                for spec in _roster_spec_paths(spec_dir)
                if spec != active_roster_spec
                and _spec_declared_kind(spec) == 'deck'
            ]
            for spec in shadowed_deck_specs:
                try:
                    from register_template import (
                        SpecParseError,
                        validate_shadowed_deck_spec,
                    )
                    declared_pages = self._extract_spec_roster(
                        spec.read_text(encoding='utf-8')
                    )
                    validate_shadowed_deck_spec(spec, declared_pages)
                except ImportError as exc:
                    self._template_issues.append((
                        'error',
                        'deck_contract',
                        f'Shadowed Deck validator could not be imported: {exc}',
                    ))
                    return self.results
                except (OSError, SpecParseError) as exc:
                    self._template_issues.append((
                        'error',
                        'deck_contract',
                        str(exc),
                    ))
                    return self.results
            roster_free = [
                (spec, kind)
                for spec in _template_spec_paths(spec_dir)
                for kind in [_spec_declared_kind(spec)]
                if kind in {'brand', 'style'}
            ]
            for spec, spec_kind in roster_free:
                self._spec_only_template_kind = spec_kind
                self.summary['total'] += 1
                spec_valid = True
                pretty_kind = spec_kind.title()
                print(
                    f"[INFO] {pretty_kind} spec detected "
                    f"({spec.name}) — "
                    f"validating its portable workspace contract."
                )
                workspace_root = (
                    spec.parent.parent
                    if spec.parent.name == 'templates'
                    else spec.parent
                )
                try:
                    from register_template import (
                        SpecParseError,
                        validate_brand_workspace,
                        validate_style_workspace,
                    )
                    validator = {
                        'brand': validate_brand_workspace,
                        'style': validate_style_workspace,
                    }[spec_kind]
                    validator(workspace_root)
                except ImportError as exc:
                    spec_valid = False
                    self._template_issues.append((
                        'error',
                        f'{spec_kind}_contract',
                        f"{pretty_kind} schema validator could not be imported: {exc}",
                    ))
                except (OSError, SpecParseError) as exc:
                    spec_valid = False
                    self._template_issues.append((
                        'error',
                        f'{spec_kind}_contract',
                        str(exc),
                    ))
                if spec_valid:
                    self.summary['passed'] += 1
            # A roster-bearing Layout/Deck spec may sit beside those in one
            # project workspace; only then does SVG validation still apply.
            if roster_free and _roster_spec_path(spec_dir) is None:
                return self.results

        # Find all SVG files
        if dir_path.is_file():
            svg_files = [dir_path]
        else:
            if self.template_mode:
                # Template directories live at templates/{layouts,decks}/<id>/.
                svg_files = discover_slide_svgs(dir_path)
            else:
                svg_output = dir_path / \
                    'svg_output' if (
                        dir_path / 'svg_output').exists() else dir_path
                svg_files = discover_slide_svgs(svg_output)

        if not svg_files:
            print(f"[ERROR] No SVG files found in: {directory}")
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []

        self._active_slide_count = len(svg_files)

        self._configure_prototype_context(dir_path, svg_files)
        if not self.template_mode:
            self._prepare_undeclared_size_occurrences(svg_files)

        directory_expected_viewbox: str | None = None
        directory_expected_label = "the first SVG canvas"
        directory_lock_has_canvas = False
        if self.template_mode:
            template_viewbox = _declared_template_canvas_viewbox(dir_path)
            if template_viewbox:
                directory_expected_viewbox = template_viewbox
                directory_expected_label = "design_spec canvas_viewbox"
            else:
                directory_expected_viewbox = ""
                directory_expected_label = "design_spec canvas_viewbox"
        if expected_format is None and directory_expected_viewbox is None:
            lock = (
                None
                if self.template_mode
                else self._get_spec_lock(svg_files[0])
            )
            if lock is not None:
                if 'canvas' in lock:
                    directory_lock_has_canvas = True
                    locked_viewbox = lock.get('canvas', {}).get('viewBox')
                    if locked_viewbox:
                        directory_expected_viewbox = locked_viewbox
                        directory_expected_label = "spec_lock canvas"
                else:
                    directory_expected_viewbox = ""
                    directory_expected_label = "spec_lock canvas"
            if (
                directory_expected_viewbox is None
                and not directory_lock_has_canvas
            ):
                for svg_file in svg_files:
                    try:
                        root = ET.parse(svg_file).getroot()
                        first_canvas = parse_project_viewbox(
                            root.get('viewBox'),
                            context=f"{svg_file.name} root viewBox",
                        )
                    except (OSError, ET.ParseError, CanvasContractError):
                        continue
                    directory_expected_viewbox = first_canvas.canonical
                    directory_expected_label = f"first SVG {svg_file.name}"
                    break

        if self.scan_banner:
            print(f"\n[SCAN] Checking {len(svg_files)} SVG file(s)...\n")

        for svg_file in svg_files:
            self._active_prototype_path = self._prototype_by_output.get(
                svg_file.resolve()
            )
            result = self.check_file(
                str(svg_file),
                expected_format,
                expected_viewbox=directory_expected_viewbox,
                expected_viewbox_label=directory_expected_label,
            )
            self._print_result(result)

        if self.template_mode:
            check_structure = _template_structure_checks_enabled(dir_path)
            if check_structure:
                self._check_pptx_structure_contract(dir_path, svg_files)
            if dir_path.is_dir():
                self._check_template_contract(
                    dir_path,
                    svg_files,
                    check_structure=check_structure,
                )
        elif _CHECK_PPTX_STRUCTURED_PROJECT:
            self._check_pptx_structure_contract(dir_path, svg_files)
        if (
            not self.template_mode
            and not self.quick_generate
            and dir_path.is_dir()
        ):
            self._check_animation_config_contract(dir_path)
        if (
            not self.template_mode
            and not self.quick_generate
            and validate_communication_trace is not None
        ):
            project_path = self._resolve_project_path(dir_path)
            if project_path not in self._communication_traced_projects:
                self._communication_traced_projects.add(project_path)
                self._communication_trace_issues.extend(
                    ('error', message)
                    for message in validate_communication_trace(project_path)
                )
                if not self.partial_roster and validate_outline_roster is not None:
                    self._communication_trace_issues.extend(
                        ('error', message)
                        for message in validate_outline_roster(project_path)
                    )
        return self.results




SVGQualityChecker = SVGLinter
