# Copyright 2025 TIER IV, inc.
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

"""Format version utilities for autoware_system_design_format files.

The ``autoware_system_design_format`` field in YAML design files declares
which schema version the file conforms to (e.g. ``0.2.0``).

Compatibility rule (semver-like):
  * **Major** must match exactly - a mismatch is an error that stops processing.
  * **Minor** of the file newer than the tool → warning.
  * **Patch** is ignored for compatibility purposes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .. import DESIGN_FORMAT_VERSION
from ..exceptions import FormatVersionError


# ---- version string → tuple ------------------------------------------------

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class SemanticVersion:
    """A parsed semantic version (major, minor, patch)."""

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_format_version(raw: str) -> SemanticVersion:
    """Parse a version string like ``0.2.0`` (with or without 'v' prefix).

    Returns:
        A :class:`SemanticVersion` instance.

    Raises:
        FormatVersionError: If the string cannot be parsed.
    """
    if not isinstance(raw, str):
        raise FormatVersionError(
            f"Format version must be a string, got {type(raw).__name__}: {raw!r}"
        )

    m = _VERSION_RE.match(raw.strip())
    if m is None:
        raise FormatVersionError(
            f"Invalid format version string: '{raw}'. "
            "Expected 'MAJOR.MINOR.PATCH' (e.g. '0.2.0')."
        )
    return SemanticVersion(int(m.group(1)), int(m.group(2)), int(m.group(3)))


# ---- supported version (singleton) -----------------------------------------

_SUPPORTED: Optional[SemanticVersion] = None


def get_supported_format_version() -> SemanticVersion:
    """Return the format version supported by this tool (cached)."""
    global _SUPPORTED
    if _SUPPORTED is None:
        _SUPPORTED = parse_format_version(DESIGN_FORMAT_VERSION)
    return _SUPPORTED


# ---- compatibility check ----------------------------------------------------


@dataclass(frozen=True)
class VersionCheckResult:
    """Result of a format-version compatibility check."""

    compatible: bool
    message: str
    file_version: Optional[SemanticVersion] = None
    supported_version: Optional[SemanticVersion] = None
    minor_newer: bool = False


def check_format_version(raw_version: Optional[str]) -> VersionCheckResult:
    """Check whether *raw_version* is compatible with the tool.

    Compatibility rules:
    * Missing version → warning (compatible=True, message describes the issue).
    * Same major & file minor ≤ tool minor → fully compatible.
    * Major mismatch → incompatible (error, must stop).
    * File minor > tool minor → compatible with warning
      (``minor_newer=True``).  The file may use features unknown to this
      tool version; the build proceeds but the mismatch is tracked so
      that it can be surfaced if the build fails for another reason.

    Returns:
        A :class:`VersionCheckResult`.
    """
    supported = get_supported_format_version()

    if raw_version is None:
        return VersionCheckResult(
            compatible=True,
            message=(
                f"Missing 'autoware_system_design_format' field. "
                f"Consider adding 'autoware_system_design_format: {supported}'."
            ),
            supported_version=supported,
        )

    try:
        file_ver = parse_format_version(raw_version)
    except FormatVersionError as exc:
        return VersionCheckResult(
            compatible=False,
            message=str(exc),
            supported_version=supported,
        )

    if file_ver.major != supported.major:
        return VersionCheckResult(
            compatible=False,
            message=(
                f"Incompatible format version: file declares {file_ver} "
                f"but this tool supports major version {supported.major} "
                f"(supported: {supported})."
            ),
            file_version=file_ver,
            supported_version=supported,
        )

    if file_ver.minor > supported.minor:
        return VersionCheckResult(
            compatible=True,
            minor_newer=True,
            message=(
                f"Format version {file_ver} has a newer minor version than "
                f"the supported {supported}. "
                f"Some features may not be fully supported. "
                f"Consider upgrading autoware_system_designer."
            ),
            file_version=file_ver,
            supported_version=supported,
        )

    return VersionCheckResult(
        compatible=True,
        message=f"Format version {file_ver} is compatible (supported: {supported}).",
        file_version=file_ver,
        supported_version=supported,
    )
