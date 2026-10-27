# Copyright 2026 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Variant scanner for ``*.data.yaml`` artifact bundles.

A data entity declares *variant axes* (``variants[]``) and one *path pattern*
(``path_pattern``) describing how those axes map onto a sub-directory layout
under a system-supplied ``root``.  This module resolves a requested variant
tuple (possibly containing ``latest`` for sortable axes) into a concrete bundle
directory by scanning ``root``.

Definition rules live in :func:`~...parsing.yaml_schema.data_semantics`, shared
with the linter so a build and a lint agree on what a valid data entity is.

Every declared axis appears in the pattern, so a resolved variant carries a
value for each axis.  A requested value with no directory on disk is an error.
Resolution requires ``root`` to exist and to hold the layout; anything else is
an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from autoware_system_designer.schema.yaml_schema import VAR_TOKEN, data_semantics

_LATEST = "latest"


class DataVariantError(Exception):
    """Raised when a data variant cannot be validated or resolved."""


@dataclass
class ResolvedVariant:
    """The outcome of resolving a requested variant tuple against ``root``."""

    values: Dict[str, Any]  # axis name -> concrete value, one per declared axis
    relative_path: str  # rendered sub-directory under root ("" for root-level)
    path: Path  # root / relative_path


@dataclass
class _Axis:
    name: str
    values: Optional[List[Any]]
    default: Any
    sortable: bool


def _is_candidate_name(name: str) -> bool:
    """Directory names eligible to stand for an axis value; dot-entries are tooling state."""
    return not name.startswith(".")


def _natural_key(value: Any) -> List[Tuple[int, int, str]]:
    """Natural sort key so e.g. ``v2`` < ``v10`` and ``2024-01`` < ``2024-10``.

    Each part becomes a ``(kind, number, text)`` triple; a numeric part outranks a
    textual one at the same position.  A sortable axis enumerates its ``values``,
    so this ordering only decides among declared names.
    """
    parts = re.split(r"(\d+)", str(value))
    return [(1, int(p), "") if p.isdigit() else (0, 0, p) for p in parts if p != ""]


class DataVariantScanner:
    """Enumerate and resolve variant sub-directories for a data bundle."""

    def __init__(
        self,
        root: Any,
        variants: Optional[List[Dict[str, Any]]],
        path_pattern: Optional[str],
    ):
        self.root = Path(root) if root is not None else None
        self._raw_variants = variants
        self._raw_pattern = path_pattern

        self._axes: Dict[str, _Axis] = {}
        for axis in variants or []:
            if not isinstance(axis, dict) or "name" not in axis:
                continue
            name = axis["name"]
            self._axes[name] = _Axis(
                name=name,
                values=axis.get("values"),
                default=axis.get("default"),
                sortable=bool(axis.get("sortable")),
            )

        # segments of the pattern; empty for a root-level bundle (null pattern)
        self._segments: List[str] = [] if path_pattern is None else [s for s in str(path_pattern).split("/") if s != ""]

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def validate_definition(self) -> List[str]:
        """Return a list of human-readable definition errors (empty == valid)."""
        config = {"variants": self._raw_variants, "path_pattern": self._raw_pattern}
        return [issue.message for issue in data_semantics(config)]

    def _require_valid(self) -> None:
        errors = self.validate_definition()
        if errors:
            raise DataVariantError("; ".join(errors))

    # ------------------------------------------------------------------
    # pattern helpers
    # ------------------------------------------------------------------
    def _axis_for_segment(self, seg: str) -> Optional[str]:
        """Return the axis name if ``seg`` is a single pure ``$(var AXIS)`` token."""
        m = VAR_TOKEN.fullmatch(seg.strip())
        if m and m.group(1) in self._axes:
            return m.group(1)
        return None

    def _rendered_glob(self) -> str:
        """The pattern with every axis segment replaced by a wildcard."""
        return "/".join("*" if self._axis_for_segment(seg) else seg for seg in self._segments)

    # ------------------------------------------------------------------
    # scanning
    # ------------------------------------------------------------------
    def _scan_pattern(self) -> List[Tuple[Dict[str, Any], List[str]]]:
        """Return [(axis_values, rendered_segments)] for existing dirs matching the pattern."""
        if self.root is None or not self.root.is_dir():
            return []

        if not self._segments:
            # root-level bundle: the "match" is root itself.
            return [({}, [])]

        axis_at: Dict[int, str] = {}
        for idx, seg in enumerate(self._segments):
            axis = self._axis_for_segment(seg)
            if axis is not None:
                axis_at[idx] = axis

        results: List[Tuple[Dict[str, Any], List[str]]] = []
        for match in sorted(self.root.glob(self._rendered_glob())):
            if not match.is_dir():
                continue
            rel_parts = match.relative_to(self.root).parts
            if len(rel_parts) != len(self._segments):
                continue
            if not all(_is_candidate_name(rel_parts[idx]) for idx in axis_at):
                continue
            values = {axis: rel_parts[idx] for idx, axis in axis_at.items()}
            results.append((values, list(rel_parts)))
        return results

    # ------------------------------------------------------------------
    # resolution
    # ------------------------------------------------------------------
    def _effective_request(self, requested: Dict[str, Any]) -> Dict[str, Any]:
        """Merge requested values with axis defaults, rejecting unknown axes."""
        for name in requested:
            if name not in self._axes:
                raise DataVariantError(f"Requested unknown variant axis '{name}' (declared axes: {sorted(self._axes)})")
        return {name: requested.get(name, axis.default) for name, axis in self._axes.items()}

    def _check_declared_values(self, effective: Dict[str, Any]) -> None:
        """Reject values outside an axis' declared enumeration and stray ``latest``."""
        for name, axis in self._axes.items():
            val = effective[name]
            if val == _LATEST:
                if not axis.sortable:
                    raise DataVariantError(f"Variant axis '{name}' cannot be 'latest' (not sortable)")
                continue
            if isinstance(axis.values, list) and axis.values and val not in axis.values:
                raise DataVariantError(f"Variant axis '{name}' value '{val}' not in declared values {axis.values}")

    def resolve(self, requested: Optional[Dict[str, Any]] = None) -> ResolvedVariant:
        """Resolve ``requested`` (axis -> value, ``latest`` allowed) to a bundle dir."""
        self._require_valid()
        if self.root is None:
            raise DataVariantError("no bundle 'root' was provided")

        effective = self._effective_request(requested or {})
        self._check_declared_values(effective)

        matches = self._scan_pattern()
        if not matches:
            raise DataVariantError(
                f"no bundle directory under '{self.root}' matches path_pattern '{self._raw_pattern}' "
                f"(glob '{self._rendered_glob()}'); is the root correct and the data present?"
            )

        resolved = self._select(matches, effective)
        if resolved is None:
            found = sorted("/".join(rel) for _, rel in matches)
            raise DataVariantError(
                f"no bundle under '{self.root}' matches the requested variant {self._describe(effective)}; "
                f"the on-disk layout does not provide it (path_pattern '{self._raw_pattern}', found: {found})"
            )
        return resolved

    def _select(
        self,
        matches: List[Tuple[Dict[str, Any], List[str]]],
        effective: Dict[str, Any],
    ) -> Optional[ResolvedVariant]:
        """Pick the directory serving ``effective``, or None if none does."""
        candidates = [
            (vals, rel)
            for vals, rel in matches
            if all(self._accepts(axis, effective.get(axis), vals.get(axis)) for axis in vals)
        ]
        if not candidates:
            return None

        latest_axes = [a for a in self._axes if effective.get(a) == _LATEST]
        if latest_axes:
            vals, rel = max(
                candidates,
                key=lambda c: tuple(_natural_key(c[0].get(a)) for a in latest_axes),
            )
        else:
            vals, rel = candidates[0]

        rel_path = "/".join(rel)
        return ResolvedVariant(
            values=dict(vals),
            relative_path=rel_path,
            path=self.root / rel_path if rel_path else self.root,
        )

    def _accepts(self, axis_name: str, requested: Any, found: Any) -> bool:
        """Whether an on-disk value satisfies the request; ``latest`` ranges over declared values only."""
        if requested != _LATEST:
            return str(requested) == str(found)
        declared = self._axes[axis_name].values
        if isinstance(declared, list) and declared:
            return str(found) in {str(v) for v in declared}
        return True

    @staticmethod
    def _describe(effective: Dict[str, Any]) -> str:
        return "{" + ", ".join(f"{k}={v}" for k, v in effective.items()) + "}"
