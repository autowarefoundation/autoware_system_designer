"""File I/O related utilities.

This package groups small modules that primarily deal with reading/writing files and
formatting file-backed diagnostics.
"""

from .source_location import SourceLocation, lookup_source, source_from_config, format_source
from .template_renderer import TemplateRenderer
from ..models.system_structure import SCHEMA_VERSION
from .system_structure_json import (
    build_system_structure,
    build_system_structure_snapshot,
    save_system_structure,
    save_system_structure_snapshot,
    load_system_structure,
    extract_system_structure_data,
)

__all__ = [
    "SourceLocation",
    "lookup_source",
    "source_from_config",
    "format_source",
    "TemplateRenderer",
    "SCHEMA_VERSION",
    "build_system_structure",
    "build_system_structure_snapshot",
    "save_system_structure",
    "save_system_structure_snapshot",
    "load_system_structure",
    "extract_system_structure_data",
]
