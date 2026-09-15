"""Text bounds estimation and canvas/module overflow checks."""

from .common import *  # noqa: F401,F403


class TextGeometryChecks:
    """Mixin: Text bounds estimation and canvas/module overflow checks."""

    @staticmethod
    def _text_line_vertical_extent(
        runs: List[Dict],
        font_size: float,
    ) -> Tuple[float, float]:
        """Return native-math-aware ascent/descent for checker bounds."""
        ascent = font_size * 0.85
        descent = font_size * 0.35
        if _estimate_inline_formula_vertical_extent is None:
            return ascent, descent
        for run in runs:
            latex = run.get('inline_formula')
            if latex is None:
                continue
            try:
                run_font_size = float(run.get('font_size', font_size))
                extent = _estimate_inline_formula_vertical_extent(str(latex))
            except (TypeError, ValueError):
                continue
            ascent = max(ascent, run_font_size * extent.ascent_em)
            descent = max(descent, run_font_size * extent.descent_em)
        return ascent, descent


    def _check_text_output_geometry(
        self,
        root: ET.Element,
        result: Dict,
        *,
        included_text_ids: set[int] | None = None,
    ) -> None:
        """Reject measurable run advances or frames with non-positive geometry."""
        helpers = (
            _drawingml_text_frame_width_emu,
            _estimate_single_line_text_frame_width,
            _parse_project_font_weight,
            _resolve_project_font_sizes,
            _resolve_project_letter_spacings,
            _validate_single_line_text_run_advances,
        )
        if any(helper is None for helper in helpers):
            return
        try:
            font_sizes = _resolve_project_font_sizes(root)
            letter_spacings = _resolve_project_letter_spacings(root, font_sizes)
        except ValueError:
            return

        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        unchanged_groups = self._unchanged_txbody_group_ids(root)
        errors: List[str] = []
        for text_el in root.iter(f'{{{SVG_NS}}}text'):
            if (
                included_text_ids is not None
                and id(text_el) not in included_text_ids
            ):
                continue
            chain: List[ET.Element] = []
            current: ET.Element | None = text_el
            while current is not None:
                chain.append(current)
                current = parent_by_id.get(id(current))
            if any(
                _local_name(current) in _NON_VISUAL_SVG_TAGS
                for current in chain
            ):
                continue
            if self._has_ancestor_id(text_el, parent_by_id, unchanged_groups):
                continue
            try:
                runs = self._resolved_single_line_text_runs(
                    text_el,
                    parent_by_id,
                    font_sizes,
                    letter_spacings,
                )
                if not runs:
                    continue
                if not ''.join(str(run['text']) for run in runs).strip():
                    continue
                text_width = _estimate_single_line_text_frame_width(runs)
                ext_cx = _drawingml_text_frame_width_emu(
                    text_width,
                    font_sizes[id(text_el)],
                )
            except (KeyError, TypeError, ValueError):
                continue
            if ext_cx < 1:
                errors.append(
                    f'{_element_label(text_el)} negative letter-spacing '
                    'produces a non-positive DrawingML text-frame extent '
                    f'(cx={ext_cx})'
                )
                continue
            try:
                _validate_single_line_text_run_advances(runs)
            except ValueError as exc:
                errors.append(f'{_element_label(text_el)} {exc}')
        result['errors'].extend(errors)


    @classmethod
    def _positioned_text_lines(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Tuple[ET.Element, float, float, List[Dict], float]] | None:
        """Resolve direct positioned tspans into estimable visual lines."""
        if _parse_project_geometry_length is None:
            return None
        children = list(text_el)
        if not children:
            return None
        line_groups = None
        synthetic_first = None
        leads_with_inline_run = (
            cls._is_tspan(children[0]) and not cls._is_line_tspan(children[0])
        )
        if (text_el.text or '').strip() or leads_with_inline_run:
            if _classify_paragraph_block is None:
                return None
            paragraph = _classify_paragraph_block(
                text_el,
                preserve_line_breaks=True,
            )
            if paragraph is None:
                return None
            _base, _extras, _breaks, line_groups, synthetic_first = paragraph
        elif any(
            descendant.get('dx') is not None
            for child in children
            if not cls._is_line_tspan(child)
            for descendant in child.iter()
        ):
            line_groups = []
            for child in children:
                if not (cls._is_tspan(child) or _local_name(child) == 'a'):
                    return None
                if cls._is_line_tspan(child):
                    line_groups.append([child])
                elif line_groups:
                    line_groups[-1].append(child)
                else:
                    return None
        if line_groups is not None:
            try:
                current_y = _parse_project_geometry_length(
                    text_el.get('y') or '0',
                    'y',
                )
                parent_x = _parse_project_geometry_length(
                    text_el.get('x') or '0',
                    'x',
                )
            except ValueError:
                return None

            lines: List[
                Tuple[ET.Element, float, float, List[Dict], float]
            ] = []
            for line_group in line_groups:
                starter = line_group[0]
                if starter is synthetic_first:
                    line_element = text_el
                    line_x = parent_x
                    line_y = current_y
                else:
                    if starter.get('x') is None:
                        return None
                    line_element = starter
                    try:
                        line_x = _parse_project_geometry_length(
                            starter.get('x'),
                            'x',
                        )
                        line_y = (
                            _parse_project_geometry_length(
                                starter.get('y'),
                                'y',
                            )
                            if starter.get('y') is not None
                            else current_y
                        )
                        if starter.get('dx') is not None:
                            line_x += _parse_project_geometry_length(
                                starter.get('dx'),
                                'dx',
                            )
                        if starter.get('dy') is not None:
                            line_y += _parse_project_geometry_length(
                                starter.get('dy'),
                                'dy',
                            )
                    except ValueError:
                        return None

                current_y = line_y
                source_runs = cls._paragraph_line_text_runs(
                    text_el,
                    line_group,
                    synthetic_first,
                )
                if source_runs is None:
                    return None
                try:
                    runs = cls._resolved_text_runs(
                        source_runs,
                        parent_by_id,
                        font_sizes,
                        letter_spacings,
                    )
                except (KeyError, TypeError, ValueError):
                    return None
                if not runs:
                    continue
                try:
                    font_size = max(float(run['font_size']) for run in runs)
                except (KeyError, TypeError, ValueError):
                    return None
                lines.append((
                    line_element,
                    line_x,
                    line_y,
                    runs,
                    font_size,
                ))
            return lines or None

        if any(
            not cls._is_tspan(child)
            or not cls._is_line_tspan(child)
            or child.get('x') is None
            or (child.tail or '').strip()
            for child in children
        ):
            return None

        try:
            current_y = _parse_project_geometry_length(
                text_el.get('y') or '0',
                'y',
            )
        except ValueError:
            return None

        lines: List[Tuple[ET.Element, float, float, List[Dict], float]] = []
        for child in children:
            try:
                line_x = _parse_project_geometry_length(child.get('x'), 'x')
                line_y = (
                    _parse_project_geometry_length(child.get('y'), 'y')
                    if child.get('y') is not None
                    else current_y
                )
                if child.get('dx') is not None:
                    line_x += _parse_project_geometry_length(
                        child.get('dx'),
                        'dx',
                    )
                if child.get('dy') is not None:
                    line_y += _parse_project_geometry_length(
                        child.get('dy'),
                        'dy',
                    )
                runs = cls._resolved_single_line_text_runs(
                    child,
                    parent_by_id,
                    font_sizes,
                    letter_spacings,
                )
            except (KeyError, TypeError, ValueError):
                return None
            current_y = line_y
            if not runs:
                continue
            try:
                font_size = max(float(run['font_size']) for run in runs)
            except (KeyError, TypeError, ValueError):
                return None
            lines.append((child, line_x, line_y, runs, font_size))
        return lines or None


    @classmethod
    def _estimated_text_line_bounds(
        cls,
        line_el: ET.Element,
        x: float,
        y: float,
        runs: List[Dict],
        font_size: float,
        parent_by_id: Dict[int, ET.Element],
        *,
        include_headroom: bool = True,
    ) -> Tuple[float, float, float, float] | None:
        """Estimate one line's transformed visible bounds in SVG coordinates."""
        if any(helper is None for helper in (
            _estimate_single_line_text_frame_width,
            _IDENTITY_MATRIX,
            _matrix_multiply,
            _parse_project_text_anchor,
            _parse_transform_matrix,
            _transform_point,
        )):
            return None
        try:
            width = float(_estimate_single_line_text_frame_width(
                runs,
                include_headroom=include_headroom,
            ))
            raw_anchor = (
                _effective_presentation_value(
                    line_el,
                    'text-anchor',
                    parent_by_id,
                )
                or 'start'
            ).strip().lower()
            anchor = _parse_project_text_anchor(raw_anchor).value
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in (x, y, width, font_size)):
            return None
        if width <= 0 or font_size <= 0:
            return None

        if anchor == 'middle':
            left = x - width / 2
            right = x + width / 2
        elif anchor == 'end':
            left = x - width
            right = x
        elif anchor == 'start':
            left = x
            right = x + width
        else:
            return None
        ascent, descent = cls._text_line_vertical_extent(runs, font_size)
        top = y - ascent
        bottom = y + descent

        return cls._transformed_rect_bounds(
            line_el,
            (left, top, right - left, bottom - top),
            parent_by_id,
        )


    @classmethod
    def _resolved_text_lines(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Tuple[ET.Element, float, float, List[Dict], float]] | None:
        """Resolve one text carrier into the lines used by width estimation."""
        lines: List[Tuple[ET.Element, float, float, List[Dict], float]] | None
        try:
            runs = cls._resolved_single_line_text_runs(
                text_el,
                parent_by_id,
                font_sizes,
                letter_spacings,
            )
        except (KeyError, TypeError, ValueError):
            return None
        if runs:
            try:
                lines = [(
                    text_el,
                    _parse_project_geometry_length(text_el.get('x') or '0', 'x'),
                    _parse_project_geometry_length(text_el.get('y') or '0', 'y'),
                    runs,
                    max(float(run['font_size']) for run in runs),
                )]
            except (KeyError, TypeError, ValueError):
                return None
        else:
            lines = cls._positioned_text_lines(
                text_el,
                parent_by_id,
                font_sizes,
                letter_spacings,
            )
        return lines or None


    @classmethod
    def _estimated_text_bounds(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
        *,
        include_headroom: bool = True,
    ) -> Tuple[float, float, float, float] | None:
        """Estimate one single- or multi-line text carrier's visual bounds."""
        lines = cls._resolved_text_lines(
            text_el,
            parent_by_id,
            font_sizes,
            letter_spacings,
        )
        if lines is None:
            return None

        bounds = [
            cls._estimated_text_line_bounds(
                line_el,
                x,
                y,
                line_runs,
                font_size,
                parent_by_id,
                include_headroom=include_headroom,
            )
            for line_el, x, y, line_runs, font_size in lines
        ]
        resolved = [item for item in bounds if item is not None]
        if not resolved:
            return None
        return (
            min(item[0] for item in resolved),
            min(item[1] for item in resolved),
            max(item[2] for item in resolved),
            max(item[3] for item in resolved),
        )


    @staticmethod
    def _accumulated_transform_matrix(
        element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ):
        """Return the element-to-root transform matrix when available."""
        if any(helper is None for helper in (
            _IDENTITY_MATRIX,
            _matrix_multiply,
            _parse_transform_matrix,
        )):
            return None
        chain: List[ET.Element] = []
        current: ET.Element | None = element
        while current is not None:
            chain.append(current)
            current = parent_by_id.get(id(current))
        matrix = _IDENTITY_MATRIX
        try:
            for current in reversed(chain):
                raw_transform = current.get('transform')
                if raw_transform:
                    matrix = _matrix_multiply(
                        matrix,
                        _parse_transform_matrix(raw_transform),
                    )
        except (TypeError, ValueError):
            return None
        return matrix


    @classmethod
    def _transformed_rect_bounds(
        cls,
        element: ET.Element,
        bounds: Tuple[float, float, float, float],
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[float, float, float, float] | None:
        """Transform one local rectangle into root SVG coordinates."""
        if _transform_point is None:
            return None
        matrix = cls._accumulated_transform_matrix(element, parent_by_id)
        if matrix is None:
            return None
        x, y, width, height = bounds
        try:
            corners = [
                _transform_point(matrix, corner_x, corner_y)
                for corner_x, corner_y in (
                    (x, y),
                    (x + width, y),
                    (x + width, y + height),
                    (x, y + height),
                )
            ]
        except (TypeError, ValueError):
            return None
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        return min(xs), min(ys), max(xs), max(ys)


    @classmethod
    def _transformed_rect_edge_lengths(
        cls,
        element: ET.Element,
        bounds: Tuple[float, float, float, float],
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[float, float] | None:
        """Return frame-axis lengths after accumulated SVG transforms."""
        if _transform_point is None:
            return None
        matrix = cls._accumulated_transform_matrix(element, parent_by_id)
        if matrix is None:
            return None
        x, y, width, height = bounds
        try:
            origin = _transform_point(matrix, x, y)
            width_end = _transform_point(matrix, x + width, y)
            height_end = _transform_point(matrix, x, y + height)
        except (TypeError, ValueError):
            return None
        rendered_w = math.hypot(
            width_end[0] - origin[0],
            width_end[1] - origin[1],
        )
        rendered_h = math.hypot(
            height_end[0] - origin[0],
            height_end[1] - origin[1],
        )
        if rendered_w <= 0 or rendered_h <= 0:
            return None
        return rendered_w, rendered_h


    @classmethod
    def _text_width_diagnostic(
        cls,
        text_element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
        *,
        container_width: float,
        include_headroom: bool,
    ) -> str | None:
        """Summarize the overflowing line's effective per-cluster width."""
        if (
            _estimate_single_line_text_frame_width is None
            or _is_cjk_char is None
            or _split_project_text_clusters is None
            or not math.isfinite(container_width)
            or container_width <= 0
        ):
            return None
        lines = cls._resolved_text_lines(
            text_element,
            parent_by_id,
            font_sizes,
            letter_spacings,
        )
        if lines is None:
            return None

        widest: Tuple[float, List[str], float] | None = None
        for line_element, _x, _y, runs, _font_size in lines:
            clusters: List[str] = []
            weighted_font_size = 0.0
            try:
                for run in runs:
                    run_clusters = [
                        cluster
                        for cluster in _split_project_text_clusters(
                            str(run.get('text', ''))
                        )
                        if not cluster.isspace()
                    ]
                    run_font_size = float(run['font_size'])
                    clusters.extend(run_clusters)
                    weighted_font_size += run_font_size * len(run_clusters)
                raw_width = float(_estimate_single_line_text_frame_width(
                    runs,
                    include_headroom=include_headroom,
                ))
            except (KeyError, TypeError, ValueError):
                continue
            if not clusters or not math.isfinite(raw_width) or raw_width <= 0:
                continue
            transformed = cls._transformed_rect_edge_lengths(
                line_element,
                (0.0, 0.0, raw_width, 1.0),
                parent_by_id,
            )
            rendered_width = transformed[0] if transformed is not None else raw_width
            if not math.isfinite(rendered_width) or rendered_width <= 0:
                continue
            rendered_font_size = (
                weighted_font_size
                / len(clusters)
                * rendered_width
                / raw_width
            )
            if widest is None or rendered_width > widest[0]:
                widest = rendered_width, clusters, rendered_font_size

        if widest is None:
            return None
        rendered_width, clusters, rendered_font_size = widest
        per_cluster = rendered_width / len(clusters)
        if not math.isfinite(per_cluster) or per_cluster <= 0:
            return None

        cjk_clusters = [
            any(_is_cjk_char(ch) for ch in cluster)
            for cluster in clusters
        ]
        if all(cjk_clusters):
            cluster_label = 'CJK char'
        elif not any(cjk_clusters) and all(
            all(ord(ch) < 128 for ch in cluster)
            for cluster in clusters
        ):
            cluster_label = 'Latin char'
        else:
            cluster_label = 'mixed char'

        font_size = (
            f'{rendered_font_size:.0f}'
            if math.isclose(rendered_font_size, round(rendered_font_size), abs_tol=0.05)
            else f'{rendered_font_size:.1f}'
        )
        width = (
            f'{container_width:.0f}'
            if math.isclose(container_width, round(container_width), abs_tol=0.05)
            else f'{container_width:.1f}'
        )
        headroom = 'incl. headroom' if include_headroom else 'without headroom'
        fits = max(0, math.floor(container_width / per_cluster))
        return (
            f'≈{per_cluster:.1f} px per {cluster_label} at {font_size}px '
            f'{headroom}; ≈{fits} chars fit in {width} px'
        )


    @staticmethod
    def _resolved_root_module_bounds(
        group: ET.Element,
    ) -> Tuple[str, Tuple[float, float, float, float]] | None:
        """Return one root module's explicit boundary in root coordinates."""
        raw = group.get(_BOUNDS_ATTR)
        if raw is None:
            return None
        try:
            x, y, width, height = _parse_positive_bounds(raw)
        except ValueError:
            return None
        return _BOUNDS_ATTR, (x, y, x + width, y + height)


    @staticmethod
    def _bounds_overflow_metrics(
        inner: Tuple[float, float, float, float],
        outer: Tuple[float, float, float, float],
        *,
        tolerance: float = _BOUNDS_OVERFLOW_TOLERANCE,
    ) -> Tuple[str, float, float] | None:
        """Return overflow axes and ratios relative to the outer dimensions."""
        left, top, right, bottom = inner
        outer_left, outer_top, outer_right, outer_bottom = outer
        left_overflow = max(outer_left - left, 0.0)
        right_overflow = max(right - outer_right, 0.0)
        top_overflow = max(outer_top - top, 0.0)
        bottom_overflow = max(bottom - outer_bottom, 0.0)
        horizontal = (
            left_overflow > tolerance
            or right_overflow > tolerance
        )
        vertical = (
            top_overflow > tolerance
            or bottom_overflow > tolerance
        )
        if not horizontal and not vertical:
            return None

        outer_width = outer_right - outer_left
        outer_height = outer_bottom - outer_top
        if outer_width <= 0.0 or outer_height <= 0.0:
            return None
        horizontal_ratio = (
            max(left_overflow, right_overflow) / outer_width
            if horizontal else 0.0
        )
        vertical_ratio = (
            max(top_overflow, bottom_overflow) / outer_height
            if vertical else 0.0
        )
        if horizontal and vertical:
            axes = 'horizontal and vertical'
        elif horizontal:
            axes = 'horizontal'
        else:
            axes = 'vertical'
        return axes, horizontal_ratio, vertical_ratio


    @staticmethod
    def _bounds_are_disjoint(
        first: Tuple[float, float, float, float],
        second: Tuple[float, float, float, float],
    ) -> bool:
        """Return whether two root-coordinate rectangles do not intersect."""
        left, top, right, bottom = first
        other_left, other_top, other_right, other_bottom = second
        return (
            right <= other_left
            or left >= other_right
            or bottom <= other_top
            or top >= other_bottom
        )


    @staticmethod
    def _bounds_overlap_dimensions(
        first: Tuple[float, float, float, float],
        second: Tuple[float, float, float, float],
    ) -> Tuple[float, float]:
        """Return positive intersection width and height for two bounds."""
        left, top, right, bottom = first
        other_left, other_top, other_right, other_bottom = second
        return (
            max(min(right, other_right) - max(left, other_left), 0.0),
            max(min(bottom, other_bottom) - max(top, other_top), 0.0),
        )


    @classmethod
    def _is_off_canvas_morph_group(
        cls,
        group: ET.Element,
        canvas: Tuple[float, float, float, float],
    ) -> bool:
        """Return whether a group declares one wholly off-canvas Morph state."""
        if group.get(_MORPH_STAGING_ATTR) != 'true':
            return False
        resolved = cls._resolved_root_module_bounds(group)
        return (
            resolved is not None
            and cls._bounds_are_disjoint(resolved[1], canvas)
        )


    @classmethod
    def _root_module_overlap_exempt(
        cls,
        group: ET.Element,
        *,
        structured_page: bool,
        canvas: Tuple[float, float, float, float] | None,
    ) -> bool:
        """Return whether one root group is not an ordinary module zone."""
        role = (group.get('data-pptx-role') or '').strip().lower()
        if role in _STRUCTURAL_ROLES:
            return True
        if structured_page and group.get('data-pptx-placeholder') is not None:
            return True
        return (
            canvas is not None
            and cls._is_off_canvas_morph_group(group, canvas)
        )


    @classmethod
    def _record_bounds_overflow(
        cls,
        result: Dict,
        *,
        subject: str,
        inner: Tuple[float, float, float, float],
        container: str,
        outer: Tuple[float, float, float, float],
        repair: str,
        width_diagnostic: str | None = None,
    ) -> None:
        """Record a warning through 5% overflow and an error above it."""
        metrics = cls._bounds_overflow_metrics(inner, outer)
        if metrics is None:
            return
        axes, horizontal_ratio, vertical_ratio = metrics
        overflow_ratio = max(horizontal_ratio, vertical_ratio)
        exceeds_error_ratio = (
            overflow_ratio > _BOUNDS_OVERFLOW_ERROR_RATIO
            and not math.isclose(
                overflow_ratio,
                _BOUNDS_OVERFLOW_ERROR_RATIO,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        )
        bucket = (
            result['errors']
            if exceeds_error_ratio
            else result['warnings']
        )
        left, top, right, bottom = inner
        outer_left, outer_top, outer_right, outer_bottom = outer
        width_suffix = (
            f'; {width_diagnostic}'
            if horizontal_ratio > 0 and width_diagnostic
            else ''
        )
        bucket.append(
            f'{subject} exceeds {container} on the {axes} axis: '
            f'content ({left:.1f}, {top:.1f})-({right:.1f}, '
            f'{bottom:.1f}), container ({outer_left:.1f}, '
            f'{outer_top:.1f})-({outer_right:.1f}, '
            f'{outer_bottom:.1f}), overflow horizontal '
            f'{horizontal_ratio:.1%}, vertical {vertical_ratio:.1%} '
            f'(error above {_BOUNDS_OVERFLOW_ERROR_RATIO:.0%}); '
            f'{repair}{width_suffix}'
        )


    @classmethod
    def _record_canvas_text_overflow(
        cls,
        result: Dict,
        *,
        subject: str,
        inner: Tuple[float, float, float, float],
        canvas: Tuple[float, float, float, float],
        width_diagnostic: str | None = None,
    ) -> bool:
        """Record one page-boundary error and return whether it overflowed."""
        metrics = cls._bounds_overflow_metrics(inner, canvas)
        if metrics is None:
            return False
        axes, horizontal_ratio, vertical_ratio = metrics
        left, top, right, bottom = inner
        canvas_left, canvas_top, canvas_right, canvas_bottom = canvas
        width_suffix = (
            f'; {width_diagnostic}'
            if horizontal_ratio > 0 and width_diagnostic
            else ''
        )
        result['errors'].append(
            f'{subject} exceeds the root viewBox on the {axes} axis: '
            f'content ({left:.1f}, {top:.1f})-({right:.1f}, '
            f'{bottom:.1f}), canvas ({canvas_left:.1f}, '
            f'{canvas_top:.1f})-({canvas_right:.1f}, '
            f'{canvas_bottom:.1f}), overflow horizontal '
            f'{horizontal_ratio:.1%}, vertical {vertical_ratio:.1%}; '
            'move or reflow the text until its estimated bounds stay on-page'
            f'{width_suffix}'
        )
        return True


    @staticmethod
    def _text_diagnostic_label(text_element: ET.Element) -> str:
        """Return a locatable label for one SVG text carrier."""
        label = _element_label(text_element)
        if (text_element.get('id') or '').strip():
            return label

        details: List[str] = []
        raw_x = (text_element.get('x') or '').strip()
        raw_y = (text_element.get('y') or '').strip()
        if raw_x or raw_y:
            details.append(f'x={raw_x or "?"}, y={raw_y or "?"}')
        snippet = re.sub(r'\s+', ' ', ''.join(text_element.itertext())).strip()
        if snippet:
            preview = snippet[:20] + ('…' if len(snippet) > 20 else '')
            details.append(f'text={preview!r}')
        return f'{label} ({"; ".join(details)})' if details else label


    def _check_module_bounds_contract(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Validate ordinary direct-root module boundaries in the SVG canvas."""
        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        viewbox = _parse_viewbox_values(root.get('viewBox') or '')
        canvas = None
        if viewbox is not None:
            x, y, width, height = viewbox
            canvas = (x, y, x + width, y + height)

        for element in root.iter():
            if element.get(_BOUNDS_ATTR) is None:
                continue
            if _local_name(element) != 'g':
                result['errors'].append(
                    f'{_element_label(element)} {_BOUNDS_ATTR} is valid '
                    'only on <g> layout modules'
                )

        for element in root.iter():
            raw_staging = element.get(_MORPH_STAGING_ATTR)
            if raw_staging is None:
                continue
            label = _element_label(element)
            if raw_staging != 'true':
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} must equal "true"; '
                    'set the exact value or remove the marker'
                )
                continue
            if _local_name(element) != 'g':
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} is valid only on <g>; '
                    'move it to the enclosing ordinary direct-root group'
                )
                continue
            if parent_by_id.get(id(element)) is not root:
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} requires a direct-root '
                    '<g>; move the marked group directly under <svg> or remove '
                    'the marker'
                )
                continue
            if not (element.get('id') or '').strip():
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} requires a stable non-empty '
                    'id; add an id to the marked direct-root group'
                )
                continue
            incompatible = [
                attribute
                for attribute in (
                    'data-pptx-layer',
                    'data-pptx-placeholder',
                )
                if element.get(attribute) is not None
            ]
            if incompatible:
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} cannot be combined with '
                    f'{", ".join(incompatible)}; use an ordinary Slide-local '
                    'group or remove the marker'
                )
                continue
            resolved = self._resolved_root_module_bounds(element)
            if resolved is None:
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} requires valid '
                    f'{_BOUNDS_ATTR}; add or fix positive root-coordinate '
                    'x y width height bounds'
                )
                continue
            if canvas is None:
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} cannot verify an off-canvas '
                    'endpoint without a valid root viewBox; fix the root viewBox'
                )
                continue
            if not self._bounds_are_disjoint(resolved[1], canvas):
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} requires wholly off-canvas '
                    f'{_BOUNDS_ATTR}; move the full bounds outside the root '
                    'viewBox or remove the marker from partially visible content'
                )

        missing: List[str] = []
        bounded_root_groups: List[
            Tuple[ET.Element, Tuple[float, float, float, float]]
        ] = []
        root_groups = [
            child
            for child in list(root)
            if _local_name(child) == 'g'
        ]
        require_bounds = (
            self.template_mode
            or root.get('data-pptx-page-role') is not None
            or any(
                root.get(attribute) is not None
                for attribute in _PPTX_ROOT_STRUCTURE_ATTRS
            )
        )
        for group in root_groups:
            if self._is_hidden_element(group, parent_by_id):
                continue
            if (
                _authored_preset_encoding is not None
                and _authored_preset_encoding(group) == 'compact'
            ):
                continue
            raw_bounds = group.get(_BOUNDS_ATTR)
            if raw_bounds is None:
                missing.append(_element_label(group))
                continue
            try:
                _parse_positive_bounds(raw_bounds)
            except ValueError as exc:
                result['errors'].append(
                    f'{_element_label(group)} {_BOUNDS_ATTR} {exc}'
                )
                continue

            resolved = self._resolved_root_module_bounds(group)
            if resolved is None:
                continue
            bounded_root_groups.append((group, resolved[1]))
            if canvas is None:
                continue
            if self._is_off_canvas_morph_group(group, canvas):
                continue
            attribute, bounds = resolved
            self._record_bounds_overflow(
                result,
                subject=f'{_element_label(group)} {attribute}',
                inner=bounds,
                container='canvas viewBox',
                outer=canvas,
                repair=(
                    'keep the root module subcanvas inside the SVG viewBox'
                ),
            )

        structured_page = all(
            (root.get(attribute) or '').strip()
            for attribute in _PPTX_ROOT_STRUCTURE_ATTRS
        )
        for index, (first_group, first_bounds) in enumerate(
            bounded_root_groups,
        ):
            if self._root_module_overlap_exempt(
                first_group,
                structured_page=structured_page,
                canvas=canvas,
            ):
                continue
            for second_group, second_bounds in bounded_root_groups[index + 1:]:
                if self._root_module_overlap_exempt(
                    second_group,
                    structured_page=structured_page,
                    canvas=canvas,
                ):
                    continue
                overlap_width, overlap_height = self._bounds_overlap_dimensions(
                    first_bounds,
                    second_bounds,
                )
                if (
                    overlap_width <= _BOUNDS_OVERFLOW_TOLERANCE
                    or overlap_height <= _BOUNDS_OVERFLOW_TOLERANCE
                ):
                    continue
                result['errors'].append(
                    f'{_element_label(first_group)} {_BOUNDS_ATTR} overlaps '
                    f'{_element_label(second_group)} {_BOUNDS_ATTR} by '
                    f'{overlap_width:.1f}px x {overlap_height:.1f}px; keep '
                    'ordinary direct-root module zones disjoint beyond the '
                    f'{_BOUNDS_OVERFLOW_TOLERANCE:.0f}px tolerance'
                )

        if missing:
            sample = '; '.join(missing[:3])
            suffix = '' if len(missing) <= 3 else f'; +{len(missing) - 3} more'
            bucket = result['errors'] if require_bounds else result['warnings']
            prefix = 'Detected' if require_bounds else 'Reference SVG: detected'
            bucket.append(
                f'{prefix} {len(missing)} visible root-level <g> '
                f'module(s) without explicit {_BOUNDS_ATTR} '
                f'({sample}{suffix}); every final-page/template root <g> other '
                'than a compact authored-preset atom declares its root-coordinate '
                'layout subcanvas even when it also carries native coordinates'
            )


    def _check_text_bounds(
        self,
        root: ET.Element,
        result: Dict,
        *,
        included_text_ids: set[int] | None = None,
    ) -> None:
        """Validate visible text against page and root-module bounds."""
        helpers = (
            _estimate_single_line_text_frame_width,
            _parse_project_font_weight,
            _parse_project_geometry_length,
            _parse_project_text_anchor,
            _resolve_project_font_sizes,
            _resolve_project_letter_spacings,
        )
        if any(helper is None for helper in helpers):
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
        unchanged_groups = self._unchanged_txbody_group_ids(root)
        viewbox = _parse_viewbox_values(root.get('viewBox') or '')
        canvas = None
        if viewbox is not None:
            x, y, width, height = viewbox
            canvas = (x, y, x + width, y + height)

        estimated_by_id: Dict[
            int,
            Tuple[float, float, float, float],
        ] = {}
        page_overflow_text_ids: set[int] = set()
        unverified: List[str] = []
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            if (
                included_text_ids is not None
                and id(text_element) not in included_text_ids
            ):
                continue
            if self._has_ancestor_id(
                text_element,
                parent_by_id,
                unchanged_groups,
            ):
                continue
            if self._has_non_visual_ancestor(
                text_element,
                root,
                parent_by_id,
            ):
                continue
            if self._is_hidden_element(text_element, parent_by_id):
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
            if estimated is not None:
                estimated_by_id[id(text_element)] = estimated

            if (
                canvas is None
                or self._has_zero_opacity(text_element, parent_by_id)
            ):
                continue

            page_estimated = self._estimated_text_bounds(
                text_element,
                parent_by_id,
                font_sizes,
                letter_spacings,
                include_headroom=False,
            )
            if page_estimated is None:
                unverified.append(self._text_diagnostic_label(text_element))
                continue
            direct_child = text_element
            parent = parent_by_id.get(id(direct_child))
            while parent is not None and parent is not root:
                direct_child = parent
                parent = parent_by_id.get(id(direct_child))
            morph_staging = (
                parent is root
                and _local_name(direct_child) == 'g'
                and self._is_off_canvas_morph_group(
                    direct_child,
                    canvas,
                )
                and self._bounds_are_disjoint(page_estimated, canvas)
            )
            if (
                not morph_staging
                and self._record_canvas_text_overflow(
                    result,
                    subject=self._text_diagnostic_label(text_element),
                    inner=page_estimated,
                    canvas=canvas,
                    width_diagnostic=self._text_width_diagnostic(
                        text_element,
                        parent_by_id,
                        font_sizes,
                        letter_spacings,
                        container_width=canvas[2] - canvas[0],
                        include_headroom=False,
                    ),
                )
            ):
                page_overflow_text_ids.add(id(text_element))

        if unverified:
            sample = ', '.join(unverified[:3])
            suffix = (
                ''
                if len(unverified) <= 3
                else f', +{len(unverified) - 3} more'
            )
            result['warnings'].append(
                'Cannot verify root viewBox bounds for visible text with '
                f'unsupported or unresolved geometry: {sample}{suffix}; use '
                'supported explicit text positioning when page fit matters '
                '(continuation <tspan> lines repeat the parent x; a hanging '
                'indent needs its own <text>)'
            )

        root_groups = [
            child
            for child in list(root)
            if _local_name(child) == 'g'
        ]
        for module in root_groups:
            if self._is_hidden_element(module, parent_by_id):
                continue
            resolved_module = self._resolved_root_module_bounds(module)
            if resolved_module is None:
                continue
            boundary_attribute, boundary = resolved_module
            for text_element in module.iter(f'{{{SVG_NS}}}text'):
                if id(text_element) in page_overflow_text_ids:
                    continue
                estimated = estimated_by_id.get(id(text_element))
                if estimated is None:
                    continue
                self._record_bounds_overflow(
                    result,
                    subject=self._text_diagnostic_label(text_element),
                    inner=estimated,
                    container=(
                        f'{_element_label(module)} {boundary_attribute}'
                    ),
                    outer=boundary,
                    repair=(
                        'expand the root module bounds into available '
                        'non-overlapping space; otherwise reflow the text'
                    ),
                    width_diagnostic=self._text_width_diagnostic(
                        text_element,
                        parent_by_id,
                        font_sizes,
                        letter_spacings,
                        container_width=boundary[2] - boundary[0],
                        include_headroom=True,
                    ),
                )


