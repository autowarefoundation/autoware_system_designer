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

"""Bundle manifest: the file a bundle records its own contract in.

A data entity names its bundles' manifest (``manifest: {file, version_key}``;
default ``deploy_metadata.yaml`` key ``version``).  A node's ``required_data``
entry declares its compatibility contract against that manifest:
``requires_version: {major, min_minor}`` — the major must match and the minor
must be at least ``min_minor`` — and ``requires_paths``, manifest keys beside
the version key holding bundle-relative file names, existence-checked at build
time.  A shortfall is a hard error.

Distinct from :mod:`...utils.format_version`, which validates the
``autoware_system_design_format`` field of design files under a different
policy (newer minor is a warning there).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import yaml

DEFAULT_VERSION_FILE = "deploy_metadata.yaml"
DEFAULT_VERSION_KEY = "version"

_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


class BundleVersionError(Exception):
    """Raised when a bundle version cannot be read, parsed or does not satisfy a requirement."""


@dataclass(frozen=True)
class BundleVersion:
    major: int
    minor: int
    raw: str

    def __str__(self) -> str:
        return self.raw


@dataclass(frozen=True)
class VersionRequirement:
    """``{major, min_minor}`` as declared on a ``required_data`` entry."""

    major: int
    min_minor: int = 0

    @classmethod
    def from_config(cls, spec: Any) -> "VersionRequirement":
        if not isinstance(spec, dict) or not isinstance(spec.get("major"), int):
            raise BundleVersionError(f"requires_version must be a mapping with an integer 'major' (got {spec!r})")
        min_minor = spec.get("min_minor", 0)
        if not isinstance(min_minor, int) or min_minor < 0:
            raise BundleVersionError(f"requires_version.min_minor must be a non-negative integer (got {min_minor!r})")
        return cls(major=spec["major"], min_minor=min_minor)

    def satisfied_by(self, version: BundleVersion) -> bool:
        return version.major == self.major and version.minor >= self.min_minor

    def describe(self) -> str:
        return f"major {self.major}, minor >= {self.min_minor}"


def parse_bundle_version(value: Any) -> BundleVersion:
    """Parse ``v4.1`` / ``4.1`` / ``5`` / ``5`` (int) / ``v5.0.1``; a patch component is ignored."""
    if isinstance(value, bool) or value is None:
        raise BundleVersionError(f"bundle version must be a string or integer (got {value!r})")
    text = str(value).strip()
    m = _VERSION_RE.match(text)
    if not m:
        raise BundleVersionError(f"bundle version '{text}' is not of the form [v]MAJOR[.MINOR[.PATCH]]")
    return BundleVersion(major=int(m.group(1)), minor=int(m.group(2) or 0), raw=text)


def _key_path(key: Any) -> List[str]:
    if isinstance(key, str):
        return [key]
    if isinstance(key, list) and key and all(isinstance(k, str) for k in key):
        return list(key)
    raise BundleVersionError(f"manifest.version_key must be a string or a non-empty list of strings (got {key!r})")


def _load_document(path: Path) -> Any:
    suffix = path.suffix.lower()
    with path.open("r", encoding="utf-8") as handle:
        if suffix in (".yaml", ".yml"):
            return yaml.safe_load(handle)
        if suffix == ".json":
            return json.load(handle)
    raise BundleVersionError(f"version file '{path.name}' must be YAML or JSON")


def _load_manifest(bundle_dir: Union[str, Path], file_name: str) -> Any:
    path = Path(bundle_dir) / file_name
    if not path.is_file():
        raise BundleVersionError(f"manifest file '{file_name}' is missing from bundle '{bundle_dir}'")
    try:
        return _load_document(path)
    except (yaml.YAMLError, json.JSONDecodeError) as e:
        raise BundleVersionError(f"manifest file '{path}' could not be parsed: {e}")


def read_bundle_version(bundle_dir: Union[str, Path], spec: Optional[Dict[str, Any]]) -> Tuple[BundleVersion, str]:
    """Read the version a bundle records, per the entity's ``manifest:`` spec.

    Returns ``(version, source)`` where ``source`` is ``<file>:<key path>`` for
    diagnostics.  Missing file, missing key or an unparseable value raise.
    """
    spec = spec or {}
    file_name = spec.get("file", DEFAULT_VERSION_FILE)
    keys: Sequence[str] = _key_path(spec.get("version_key", DEFAULT_VERSION_KEY))
    source = f"{file_name}:{'.'.join(keys)}"

    node = _load_manifest(bundle_dir, file_name)
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            raise BundleVersionError(f"key '{'.'.join(keys)}' not found in manifest file '{file_name}'")
        node = node[key]

    try:
        return parse_bundle_version(node), source
    except BundleVersionError as e:
        raise BundleVersionError(f"{e} (read from '{source}' in bundle '{bundle_dir}')")


def read_manifest_paths(
    bundle_dir: Union[str, Path], spec: Optional[Dict[str, Any]], path_keys: Any
) -> List[Tuple[str, str]]:
    """Resolve manifest keys naming required files to paths inside the bundle.

    ``path_keys`` is a consumer's ``requires_paths``; each key sits beside the
    version key and holds a bundle-relative file name.  Every named file must
    exist.  Returns ``(key, absolute path)`` pairs.
    """
    spec = spec or {}
    if not path_keys:
        return []
    if not isinstance(path_keys, list) or not all(isinstance(k, str) for k in path_keys):
        raise BundleVersionError(f"requires_paths must be a list of strings (got {path_keys!r})")

    file_name = spec.get("file", DEFAULT_VERSION_FILE)
    parent: Sequence[str] = _key_path(spec.get("version_key", DEFAULT_VERSION_KEY))[:-1]

    node = _load_manifest(bundle_dir, file_name)
    for key in parent:
        if not isinstance(node, dict) or key not in node:
            raise BundleVersionError(f"key '{'.'.join(parent)}' not found in manifest file '{file_name}'")
        node = node[key]

    resolved: List[Tuple[str, str]] = []
    for key in path_keys:
        if not isinstance(node, dict) or key not in node:
            raise BundleVersionError(f"path key '{key}' not found in manifest file '{file_name}'")
        value = node[key]
        if not isinstance(value, str) or not value:
            raise BundleVersionError(
                f"path key '{key}' in manifest file '{file_name}' must be a file name (got {value!r})"
            )
        target = Path(bundle_dir) / value
        if not target.is_file():
            raise BundleVersionError(f"manifest entry '{key}: {value}' names a file missing from bundle '{bundle_dir}'")
        resolved.append((key, str(target)))
    return resolved
