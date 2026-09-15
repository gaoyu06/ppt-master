"""Compiler lint pass: advisory SVG quality checks before export."""

from .checker import SVGLinter, SVGQualityChecker

__all__ = ['SVGLinter', 'SVGQualityChecker']
