#!/usr/bin/env python3

"""Helpers that turn designer source maps into LSP ranges.

Source maps are keyed by JSON pointer (``/instances/0/name``) and hold 1-based
line/column pairs, while LSP positions are 0-based.
"""

import re
from typing import Optional

from lsprotocol import types as lsp

_SOURCE_SUFFIX_RE = re.compile(r"\s*\((?:source=[^)]*?)?(?:yaml_path=(?P<yaml_path>\S+?))?\s*\)\s*$")


def source_map_line(source_map: Optional[dict], yaml_path: str) -> Optional[int]:
    """Return the 0-based line a YAML pointer starts at."""
    if not source_map:
        return None
    entry = source_map.get(yaml_path)
    if not isinstance(entry, dict) or entry.get("line") is None:
        return None
    return max(int(entry["line"]) - 1, 0)


def source_map_range(source_map: Optional[dict], yaml_path: str, document_content: str = None) -> Optional[lsp.Range]:
    """Return the range covering the value a YAML pointer addresses."""
    line = source_map_line(source_map, yaml_path)
    if line is None:
        return None

    entry = source_map[yaml_path]
    column = max(int(entry.get("column", 1)) - 1, 0)
    end = column + 1
    if document_content:
        lines = document_content.split("\n")
        if line < len(lines):
            end = max(len(lines[line]), column + 1)

    return lsp.Range(start=lsp.Position(line=line, character=column), end=lsp.Position(line=line, character=end))


def split_source_suffix(message: str) -> "tuple[str, Optional[str]]":
    """Split a designer diagnostic into its text and the YAML pointer it points at."""
    match = _SOURCE_SUFFIX_RE.search(message)
    if not match:
        return message.strip(), None
    return message[: match.start()].strip(), match.group("yaml_path")
