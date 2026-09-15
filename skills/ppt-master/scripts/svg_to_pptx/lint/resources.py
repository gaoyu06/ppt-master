"""Image, icon, and carrier-receipt checks."""

from .common import *  # noqa: F401,F403


class ResourceChecks:
    """Mixin: Image, icon, and carrier-receipt checks."""

    def _check_image_contract(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Validate picture frames, references, and bytes before export."""
        if _project_image_errors is None:
            result['errors'].append(
                'Unable to import the image validator; cannot verify picture '
                'frames or media'
            )
            return
        _working_root, _parent_by_id, images = self._visible_image_elements(root)
        for image in images:
            result['errors'].extend(
                _project_image_errors(
                    image,
                    svg_path.parent,
                    allow_template_placeholders=self.template_mode,
                )
            )


    def _record_carrier_receipt(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Record factual visible-carrier use without grading the design."""
        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        geometry_tags = (
            'rect',
            'circle',
            'ellipse',
            'line',
            'polyline',
            'polygon',
            'path',
        )
        geometry_counts = Counter({tag: 0 for tag in geometry_tags})
        preset_names: Counter[str] = Counter()
        native_objects = Counter({
            'chart': 0,
            'table': 0,
            'formula_block': 0,
            'formula_inline': 0,
            'other': 0,
        })
        marker_counts = Counter({'start': 0, 'mid': 0, 'end': 0})
        text_count = 0
        icon_count = 0
        page_frame_geometry = 0

        for element in root.iter():
            if (
                element is root
                or self._is_hidden_element(element, parent_by_id)
                or self._has_non_visual_ancestor(element, root, parent_by_id)
                or self._has_zero_opacity(element, parent_by_id)
            ):
                continue

            tag = _local_name(element)
            if tag == 'text':
                text_count += 1
            elif tag == 'use' and element.get('data-icon') is not None:
                icon_count += 1

            if element.get(_INLINE_FORMULA_ATTR) is not None:
                native_objects['formula_inline'] += 1
            replacement_kind = self._carrier_native_replacement_kind(element)
            if replacement_kind:
                key = (
                    'formula_block'
                    if replacement_kind == 'formula'
                    else replacement_kind
                )
                native_objects[key if key in native_objects else 'other'] += 1

            preset = (element.get('data-pptx-prst') or '').strip()
            if preset:
                preset_names[preset] += 1
                if self._carrier_page_frame_role(element, root, parent_by_id):
                    page_frame_geometry += 1
                continue
            if tag not in geometry_counts or self._has_preset_ancestor(
                element,
                root,
                parent_by_id,
            ):
                continue

            geometry_counts[tag] += 1
            if self._carrier_page_frame_role(element, root, parent_by_id):
                page_frame_geometry += 1
            style_values = (
                _parse_inline_style(element.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            for position in ('start', 'mid', 'end'):
                raw_marker = (
                    style_values.get(f'marker-{position}')
                    or element.get(f'marker-{position}')
                    or ''
                ).strip().lower()
                if raw_marker and raw_marker != 'none':
                    marker_counts[position] += 1

        image_receipt = self._carrier_image_receipt(root)
        result['info']['carrier_receipt'] = {
            'text_elements': text_count,
            'images': image_receipt,
            'icons': icon_count,
            'effects': self._carrier_effect_receipt(root, parent_by_id),
            'geometry': {
                'svg_elements': dict(geometry_counts),
                'preset_shapes': sum(preset_names.values()),
                'preset_names': dict(sorted(preset_names.items())),
                'page_frame_elements': page_frame_geometry,
                'marker_uses': dict(marker_counts),
            },
            'native_objects': dict(native_objects),
        }


    @classmethod
    def _carrier_effect_receipt(
        cls,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Dict:
        """Count factual visible effect declarations and resolved references."""
        ignored_tags = frozenset({
            'clippath',
            'defs',
            'marker',
            'mask',
            'pattern',
            'symbol',
        })
        emphasis_properties = (
            'fill',
            'font-weight',
            'font-size',
            'font-style',
            'text-decoration',
            'letter-spacing',
        )
        definition_kinds: Dict[str, str] = {}
        for element in root.iter():
            definition_id = (element.get('id') or '').strip()
            if definition_id:
                definition_kinds[definition_id] = _local_name(element).casefold()

        def declared_value(
            element: ET.Element,
            style_values: Dict[str, str],
            name: str,
        ) -> str | None:
            if name in style_values:
                return style_values[name]
            return element.get(name)

        def reference_kind(value: str | None) -> str:
            match = re.fullmatch(
                r'url\(\s*#([^)]+?)\s*\)',
                (value or '').strip(),
                re.IGNORECASE,
            )
            return definition_kinds.get(match.group(1), '') if match else ''

        def has_ignored_ancestor(element: ET.Element) -> bool:
            current: ET.Element | None = element
            while current is not None and current is not root:
                if _local_name(current).casefold() in ignored_tags:
                    return True
                current = parent_by_id.get(id(current))
            return False

        effects = Counter({
            'inline_emphasis_runs': 0,
            'gradient_uses': 0,
            'filter_uses': 0,
            'text_effects': 0,
        })
        gradient_kinds = {'lineargradient', 'radialgradient'}
        text_paint_kinds = gradient_kinds | {'pattern'}

        for element in root.iter():
            if (
                element is root
                or has_ignored_ancestor(element)
                or cls._has_non_visual_ancestor(element, root, parent_by_id)
                or cls._is_hidden_element(element, parent_by_id)
                or cls._has_zero_opacity(element, parent_by_id)
            ):
                continue

            style_values = (
                _parse_inline_style(element.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            fill_kind = reference_kind(
                declared_value(element, style_values, 'fill')
            )
            raw_stroke = declared_value(element, style_values, 'stroke')
            stroke_kind = reference_kind(raw_stroke)
            effects['gradient_uses'] += sum(
                kind in gradient_kinds for kind in (fill_kind, stroke_kind)
            )

            raw_filter = declared_value(element, style_values, 'filter')
            if reference_kind(raw_filter) == 'filter':
                effects['filter_uses'] += 1

            tag = _local_name(element).casefold()
            if tag == 'tspan':
                current = parent_by_id.get(id(element))
                inside_text = False
                while current is not None:
                    if _local_name(current).casefold() == 'text':
                        inside_text = True
                        break
                    current = parent_by_id.get(id(current))
                if (
                    inside_text
                    and not any(
                        element.get(name) is not None
                        for name in ('x', 'y', 'dx', 'dy')
                    )
                    and any(
                        name in style_values or element.get(name) is not None
                        for name in emphasis_properties
                    )
                ):
                    effects['inline_emphasis_runs'] += 1

            if tag in {'text', 'tspan'}:
                has_filter = (
                    'filter' in style_values
                    or element.get('filter') is not None
                )
                has_stroke = bool(
                    raw_stroke
                    and raw_stroke.strip()
                    and raw_stroke.strip().casefold() != 'none'
                )
                if (
                    fill_kind in text_paint_kinds
                    or stroke_kind in text_paint_kinds
                    or has_filter
                    or has_stroke
                ):
                    effects['text_effects'] += 1

        return dict(effects)


    @staticmethod
    def _carrier_native_replacement_kind(element: ET.Element) -> str:
        """Return one native replacement kind without turning bad data into a check."""
        if _native_replacement_kind is not None:
            try:
                return (_native_replacement_kind(element) or '').strip().lower()
            except ValueError:
                pass
        return (
            element.get('data-pptx-replace-with')
            or element.get('data-pptx-native')
            or ''
        ).strip().lower()


    @staticmethod
    def _has_preset_ancestor(
        element: ET.Element,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Return whether geometry is only the visible detail of a preset atom."""
        current = parent_by_id.get(id(element))
        while current is not None and current is not root:
            if (current.get('data-pptx-prst') or '').strip():
                return True
            current = parent_by_id.get(id(current))
        return False


    @staticmethod
    def _carrier_page_frame_role(
        element: ET.Element,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Return whether an element belongs to declared page framing."""
        current: ET.Element | None = element
        while current is not None:
            role = (current.get('data-pptx-role') or '').strip().lower()
            if role in {'background', 'decoration'}:
                return True
            if current is root:
                break
            current = parent_by_id.get(id(current))
        return False


    def _carrier_image_receipt(self, root: ET.Element) -> Dict:
        """Summarize visible image placements and their frame share."""
        working_root, parent_by_id, images = self._visible_image_elements(root)
        viewbox = _parse_viewbox_values(working_root.get('viewBox') or '')
        canvas_area = (
            abs(viewbox[2] * viewbox[3])
            if viewbox is not None and viewbox[2] and viewbox[3]
            else 0.0
        )
        frame_shares: List[float] = []
        filenames = set()

        for image in images:
            href = image.get('href') or image.get(f'{{{XLINK_NS}}}href') or ''
            if href.startswith('data:'):
                filenames.add('(embedded)')
            elif href:
                path_name = Path(unquote(urlsplit(href).path)).name
                filenames.add(path_name or href[:80])

            display_owner = image
            parent = parent_by_id.get(id(image))
            if (
                parent is not None
                and parent is not working_root
                and _local_name(parent) == 'svg'
            ):
                display_owner = parent
            try:
                x = float(display_owner.get('x') or '0')
                y = float(display_owner.get('y') or '0')
                width = float(display_owner.get('width') or '0')
                height = float(display_owner.get('height') or '0')
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0 or canvas_area <= 0:
                continue
            transformed = self._transformed_rect_edge_lengths(
                display_owner,
                (x, y, width, height),
                parent_by_id,
            )
            if transformed is not None:
                width, height = transformed
            frame_shares.append(abs(width * height) / canvas_area)

        return {
            'placements': len(images),
            'files': sorted(filenames),
            'max_frame_share': round(max(frame_shares), 4) if frame_shares else 0.0,
        }


    @classmethod
    def _visible_image_elements(
        cls,
        root: ET.Element,
    ) -> Tuple[ET.Element, Dict[int, ET.Element], List[ET.Element]]:
        """Return rendered image instances after expanding static local uses."""
        working_root = copy.deepcopy(root)
        if (
            _expand_local_use_references is not None
            and _UseExpansionError is not None
        ):
            try:
                _expand_local_use_references(working_root)
            except _UseExpansionError:
                # The local-reference validator owns the actionable failure.
                working_root = copy.deepcopy(root)

        parent_by_id = {
            id(child): parent
            for parent in working_root.iter()
            for child in list(parent)
        }
        images = [
            element
            for element in working_root.iter(f'{{{SVG_NS}}}image')
            if not cls._is_hidden_element(element, parent_by_id)
            and not cls._has_non_visual_ancestor(
                element,
                working_root,
                parent_by_id,
            )
            and not cls._has_zero_opacity(element, parent_by_id)
        ]
        return working_root, parent_by_id, images


    @staticmethod
    def _has_non_visual_ancestor(
        element: ET.Element,
        module: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Return whether an element lives in a non-rendered module subtree."""
        current: ET.Element | None = element
        while current is not None and current is not module:
            if _local_name(current) in _NON_VISUAL_SVG_TAGS:
                return True
            current = parent_by_id.get(id(current))
        return False


    def _check_image_references(self, root: ET.Element, svg_path: Path, result: Dict):
        """Check image file existence and effective rendered resolution."""
        svg_dir = svg_path.parent
        working_root, parent_by_id, images = self._visible_image_elements(root)

        for image in images:
            href = image.get('href') or image.get(f'{{{XLINK_NS}}}href')
            if not href or href.startswith('data:'):
                continue
            if self.template_mode and '{{' in href and '}}' in href:
                continue
            if _resolve_external_image_reference is None:
                result['warnings'].append(
                    "Detected image references, but shared image resolver could not be imported; "
                    "export will still validate them."
                )
                return

            img_path = _resolve_external_image_reference(svg_dir, href)
            if img_path is None:
                # The shared image-source contract already reports the
                # blocking resolution failure. This pass adds quality advice
                # only for valid, resolved images.
                continue

            # Check resolution vs display size
            display_owner = image
            parent = parent_by_id.get(id(image))
            if (
                parent is not None
                and parent is not working_root
                and parent.tag == f'{{{SVG_NS}}}svg'
            ):
                # Imported crops use a unit-frame inner image. Quality advice
                # must compare the source against the visible outer frame.
                display_owner = parent
            display_w_str = display_owner.get('width')
            display_h_str = display_owner.get('height')
            if not display_w_str or not display_h_str:
                continue

            try:
                display_x = float(display_owner.get('x') or '0')
                display_y = float(display_owner.get('y') or '0')
                local_display_w = float(display_w_str)
                local_display_h = float(display_h_str)
            except (ValueError, TypeError):
                continue
            if local_display_w <= 0 or local_display_h <= 0:
                continue
            display_w = local_display_w
            display_h = local_display_h
            transformed_size = self._transformed_rect_edge_lengths(
                display_owner,
                (display_x, display_y, local_display_w, local_display_h),
                parent_by_id,
            )
            if transformed_size is not None:
                display_w, display_h = transformed_size
            axis_scale_x = display_w / local_display_w
            axis_scale_y = display_h / local_display_h

            try:
                from PIL import Image as PILImage, ImageOps
                with PILImage.open(img_path) as img:
                    actual_w, actual_h = ImageOps.exif_transpose(img).size
                source_bytes = img_path.stat().st_size

                visible_w = float(actual_w)
                visible_h = float(actual_h)
                fit_owner = image
                if display_owner is not image:
                    fit_owner = display_owner
                    viewbox = (display_owner.get('viewBox') or '').split()
                    if len(viewbox) == 4:
                        try:
                            viewbox_w = float(viewbox[2])
                            viewbox_h = float(viewbox[3])
                        except ValueError:
                            pass
                        else:
                            if 0 < viewbox_w <= 1 and 0 < viewbox_h <= 1:
                                visible_w *= viewbox_w
                                visible_h *= viewbox_h

                raw_aspect = fit_owner.get('preserveAspectRatio')
                try:
                    align, mode = (
                        _parse_project_image_aspect_ratio(raw_aspect)
                        if _parse_project_image_aspect_ratio is not None
                        else ('xMidYMid', 'meet')
                    )
                except ValueError:
                    continue

                local_scale_x = local_display_w / visible_w
                local_scale_y = local_display_h / visible_h
                if align == 'none':
                    render_scale = max(
                        local_scale_x * axis_scale_x,
                        local_scale_y * axis_scale_y,
                    )
                    fit_label = 'none'
                elif mode == 'slice':
                    render_scale = (
                        max(local_scale_x, local_scale_y)
                        * max(axis_scale_x, axis_scale_y)
                    )
                    fit_label = 'slice'
                else:
                    render_scale = (
                        min(local_scale_x, local_scale_y)
                        * max(axis_scale_x, axis_scale_y)
                    )
                    fit_label = 'meet'

                if render_scale > IMAGE_UPSCALE_WARN_RATIO:
                    result['warnings'].append(
                        f"Image {href} is {actual_w}x{actual_h} and renders at "
                        f"{render_scale:.2f}x scale in a "
                        f"{int(display_w)}x{int(display_h)} {fit_label} frame "
                        f"— about {render_scale * 1.5:.1f}x on a 1080p projector, "
                        "visibly soft; use a larger source or a smaller frame"
                    )
                elif (
                    render_scale < 1.0 / IMAGE_DOWNSIZE_WARN_RATIO
                    and source_bytes >= IMAGE_DOWNSIZE_WARN_MIN_BYTES
                ):
                    source_mib = source_bytes / (1024 * 1024)
                    result['warnings'].append(
                        f"Image {href} is {actual_w}x{actual_h} and renders at "
                        f"{render_scale:.2f}x scale in a "
                        f"{int(display_w)}x{int(display_h)} {fit_label} frame; "
                        f"the source is {source_mib:.1f} MiB — file-size "
                        "advisory only, not an aspect-ratio warning; consider "
                        "a smaller source asset, or export with "
                        "svg_to_pptx.py --image-sizing display to downsize "
                        "at export time"
                    )
            except ImportError:
                pass  # PIL not available, skip resolution check
            except Exception:
                pass  # Image unreadable, skip resolution check


    def _check_icon_placeholders(self, root: ET.Element, svg_path: Path, result: Dict) -> None:
        """Check that <use data-icon="..."> placeholders resolve."""
        placeholders = [
            elem for elem in root.iter()
            if _local_name(elem).lower() == 'use' and elem.get('data-icon') is not None
        ]
        if not placeholders:
            return

        if _resolve_icon_path is None:
            result['warnings'].append(
                "Detected data-icon placeholders, but icon resolver could not be imported; "
                "post-processing/export will still validate them."
            )
            return
        if _icon_dir_for_svg is None:
            result['warnings'].append(
                "Detected data-icon placeholders, but the project icon helper could not be imported; "
                "post-processing/export will still validate them."
            )
            return

        icons_dir = _icon_dir_for_svg(svg_path)
        seen = set()
        for elem in placeholders:
            icon_name = (elem.get('data-icon') or '').strip()
            if not icon_name:
                result['errors'].append("Icon placeholder has empty data-icon value")
                continue
            if icon_name in seen:
                continue
            seen.add(icon_name)

            try:
                icon_path, _ = _resolve_icon_path(icon_name, icons_dir)
            except ValueError as exc:
                result['errors'].append(str(exc))
                continue
            if not icon_path.exists():
                suggestion = (
                    _suggest_icon_name(icon_name, icons_dir)
                    if _suggest_icon_name is not None else None
                )
                hint = (
                    f"; identifiers are case-sensitive; use '{suggestion}'"
                    if suggestion else ""
                )
                result['errors'].append(
                    f"Project-local icon not found: {icon_name} "
                    f"(expected under {icons_dir}){hint}"
                )
                continue
            try:
                icon_root = ET.parse(icon_path).getroot()
                hydrated = hydrate_native_payload_refs(icon_root, icon_path)
            except (OSError, ET.ParseError, NativePayloadError) as exc:
                result['errors'].append(
                    f"Icon {icon_name} has invalid native payload metadata: {exc}"
                )
                continue
            if _project_mask_errors is not None:
                result['errors'].extend(
                    f'Icon {icon_name}: {error}'
                    for error in _project_mask_errors(icon_root)
                )
            if hydrated:
                result['info']['native_icon_payload_refs'] = (
                    result['info'].get('native_icon_payload_refs', 0) + hydrated
                )

    @classmethod
    def _text_image_fill_images(cls, root: ET.Element) -> List[ET.Element]:
        """Return <image> children of text picture-fill patterns in use."""
        patterns = {
            pattern.get('id'): pattern
            for pattern in root.iter(f'{{{SVG_NS}}}pattern')
            if pattern.get('data-pptx-text-image-fill') and pattern.get('id')
        }
        if not patterns:
            return []
        used: set[str] = set()
        for element in root.iter():
            if _local_name(element) not in {'text', 'tspan'}:
                continue
            style_values = (
                _parse_inline_style(element.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            fill = style_values.get('fill') or element.get('fill') or ''
            match = re.match(r'\s*url\(\s*[\'"]?#([^)\'"\s]+)', fill)
            if match and match.group(1) in patterns:
                used.add(match.group(1))
        return [
            image
            for pattern_id in used
            for image in patterns[pattern_id].iter(f'{{{SVG_NS}}}image')
        ]

    @staticmethod
    def _image_frame_geometry(
        image: ET.Element,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[float, float, float, float] | None:
        """Return (frame width, frame height, source fraction w, h) of one instance.

        Inside the nested-``<svg>`` crop transport the frame is the wrapper and
        the ``viewBox`` selects a unit-coordinate fraction of the source; a
        plain ``<image>`` shows the whole source in its own box.
        """
        def _number(raw: str | None) -> float | None:
            if raw is None:
                return None
            try:
                value = float(raw.strip().removesuffix('px'))
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value > 0 else None

        parent = parent_by_id.get(id(image))
        if (
            parent is not None
            and parent is not root
            and _local_name(parent) == 'svg'
            and parent.get('viewBox')
        ):
            parts = [
                part for part in re.split(r'[\s,]+', parent.get('viewBox', '').strip())
                if part
            ]
            if len(parts) != 4:
                return None
            width = _number(parent.get('width'))
            height = _number(parent.get('height'))
            fraction_width = _number(parts[2])
            fraction_height = _number(parts[3])
            if None in (width, height, fraction_width, fraction_height):
                return None
            return (width, height, fraction_width, fraction_height)
        width = _number(image.get('width'))
        height = _number(image.get('height'))
        if width is None or height is None:
            return None
        return (width, height, 1.0, 1.0)

    def _measure_image_pixels(
        self,
        paths: set[Path] | None,
    ) -> Tuple[int, int] | None:
        """Return the EXIF-oriented pixel size of the first readable file."""
        for path in sorted(paths or ()):
            cached = self._image_pixel_sizes.get(path)
            if cached is not None:
                return cached
            try:
                from PIL import Image, ImageOps  # type: ignore
                with Image.open(path) as image:
                    oriented = ImageOps.exif_transpose(image)
                    size = (int(oriented.width), int(oriented.height))
            except Exception:  # noqa: BLE001 - unreadable files are reported elsewhere
                continue
            if size[0] > 0 and size[1] > 0:
                self._image_pixel_sizes[path] = size
                return size
        return None

    @staticmethod
    def _stretch_deviation(
        geometry: Tuple[float, float, float, float] | None,
        source_size: Tuple[int, int] | None,
    ) -> float | None:
        """Return how far a ``none`` placement departs from the source aspect."""
        if geometry is None or source_size is None:
            return None
        frame_width, frame_height, fraction_width, fraction_height = geometry
        expected = (fraction_width * source_size[0]) / (fraction_height * source_size[1])
        if expected <= 0:
            return None
        return abs((frame_width / frame_height) / expected - 1.0)

    @staticmethod
    def _image_crop_mechanisms(
        image: ET.Element,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[str, ...]:
        """Return objective clipping mechanisms affecting one image instance."""
        mechanisms: List[str] = []
        current: ET.Element | None = image
        while current is not None:
            tag = _local_name(current)
            style_values = (
                _parse_inline_style(current.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            for property_name in ('clip-path', 'mask'):
                value = style_values.get(property_name)
                if value is None:
                    value = current.get(property_name)
                if value and value.strip().lower() != 'none':
                    mechanisms.append(f"<{tag}> {property_name}")
            overflow = style_values.get('overflow')
            if overflow is None:
                overflow = current.get('overflow')
            if overflow and overflow.strip().lower() in {'hidden', 'clip'}:
                mechanisms.append(f"<{tag}> overflow={overflow.strip()!r}")
            if current is not root and tag == 'svg':
                mechanisms.append('nested <svg> viewport')
            current = parent_by_id.get(id(current))
        return tuple(dict.fromkeys(mechanisms))

