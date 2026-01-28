from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SourceLocation:
    file_path: Optional[Path] = None
    yaml_path: Optional[str] = None
    line: Optional[int] = None  # 1-based
    column: Optional[int] = None  # 1-based


def lookup_source(source_map: Optional[Dict[str, Dict[str, int]]], yaml_path: Optional[str]) -> SourceLocation:
    if not source_map or not yaml_path:
        return SourceLocation(yaml_path=yaml_path)

    entry = source_map.get(yaml_path)
    if not entry:
        return SourceLocation(yaml_path=yaml_path)

    return SourceLocation(
        yaml_path=yaml_path,
        line=entry.get("line"),
        column=entry.get("column"),
    )


def source_from_config(config: Any, yaml_path: Optional[str]) -> SourceLocation:
    """Create a SourceLocation using a Config-like object (file_path + optional source_map)."""
    file_path = getattr(config, "file_path", None)
    source_map = getattr(config, "source_map", None)

    loc = lookup_source(source_map, yaml_path)
    return SourceLocation(
        file_path=Path(file_path) if file_path is not None else None,
        yaml_path=loc.yaml_path,
        line=loc.line,
        column=loc.column,
    )


def format_source(loc: Optional[SourceLocation]) -> str:
    if not loc:
        return ""

    parts = []
    if loc.file_path is not None:
        if loc.line is not None and loc.column is not None:
            parts.append(f"source= {loc.file_path}:{loc.line}:{loc.column} ")
        elif loc.line is not None:
            parts.append(f"source= {loc.file_path}:{loc.line} ")
        else:
            parts.append(f"source= {loc.file_path} ")

    if loc.yaml_path:
        parts.append(f"yaml_path= {loc.yaml_path} ")

    if not parts:
        return ""

    return " (" + " ".join(parts) + ")"
