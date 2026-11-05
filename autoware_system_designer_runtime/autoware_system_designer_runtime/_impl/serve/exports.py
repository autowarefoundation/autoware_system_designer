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

"""Exported systems under an install tree, and the subset of that tree the daemon may serve.

Layout written by the designer (isolated colcon install; merged installs drop the leading package):
``<install>/<pkg>/share/<pkg>/exports/<system>/{deployment.json, system_structure/, launcher/, visualization/web/}``
plus ``systems.html`` with its stylesheet and theme script at the install root.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

INSTALL_DIR_NAME = "install"
MANIFEST_FILENAME = "deployment.json"
STRUCTURE_SUBDIR = "system_structure"
LAUNCHER_SUBDIR = "launcher"
DEPLOYMENTS_SUBDIR = "deployments"
WEB_SUBPATH = ("visualization", "web")
LAUNCH_FILE_SUFFIX = ".launch.xml"

SYSTEMS_INDEX_PATH = "systems.html"
# The systems index and the assets it links, relative to the install root.
INDEX_FILES = frozenset({SYSTEMS_INDEX_PATH, "theme.js", "css/styles.css"})

KIND_ONBOARD = "onboard"
KIND_OFFBOARD = "offboard"

# Compute unit status as seen by this daemon; clients treat unknown values as opaque.
UNIT_AVAILABLE = "available"
UNIT_REMOTE_UNSUPPORTED = "remote_unsupported"
UNIT_OFFBOARD = "offboard"
UNIT_UNSUPPORTED = "unsupported"


def resolve_install_root(explicit: Optional[str]) -> Optional[Path]:
    """Install tree to serve: the explicit one, else the first colcon prefix, else the one holding this package."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    for entry in os.environ.get("COLCON_PREFIX_PATH", "").split(os.pathsep):
        if entry and Path(entry).is_dir():
            return Path(entry).resolve()
    # The unresolved module path keeps a symlink install inside its install tree.
    parts = Path(os.path.abspath(__file__)).parts
    if INSTALL_DIR_NAME in parts:
        idx = len(parts) - 1 - parts[::-1].index(INSTALL_DIR_NAME)
        return Path(*parts[: idx + 1]).resolve()
    return None


@dataclass(frozen=True)
class SystemExport:
    """One exported system: its export directory and the manifest the designer wrote there."""

    package: str
    name: str
    export_dir: Path
    manifest: dict

    @property
    def id(self) -> str:
        return f"{self.package}:{self.name}"

    @property
    def web_dir(self) -> Path:
        return self.export_dir.joinpath(*WEB_SUBPATH)

    def mode_keys(self) -> list[str]:
        modes = self.manifest.get("mode_keys")
        return [m for m in modes if isinstance(m, str)] if isinstance(modes, list) else []

    def compute_units(self) -> list[dict[str, str]]:
        """Declared units (name, kind, description only); without a declaration, the units that have launchers."""
        declared = self.manifest.get("compute_units")
        if isinstance(declared, list) and declared:
            return [
                {
                    "name": unit["name"],
                    "kind": str(unit.get("kind") or KIND_ONBOARD),
                    "description": str(unit.get("description") or ""),
                }
                for unit in declared
                if isinstance(unit, dict) and isinstance(unit.get("name"), str)
            ]
        return [{"name": name, "kind": KIND_ONBOARD, "description": ""} for name in self._launcher_units()]

    def _launcher_units(self) -> list[str]:
        launcher_root = self.export_dir / LAUNCHER_SUBDIR
        mode_dirs: list[Path] = [launcher_root / mode for mode in self.mode_keys()]
        deployments = launcher_root / DEPLOYMENTS_SUBDIR
        if deployments.is_dir():
            mode_dirs += [d / mode for d in deployments.iterdir() for mode in self.mode_keys()]
        found = {
            unit_dir.name
            for mode_dir in mode_dirs
            if mode_dir.is_dir()
            for unit_dir in mode_dir.iterdir()
            if unit_dir.is_dir() and any(unit_dir.glob(f"*{LAUNCH_FILE_SUFFIX}"))
        }
        return sorted(found)

    def deploy_variants(self) -> list[dict[str, Any]]:
        variants = self.manifest.get("deploy_variants")
        if not isinstance(variants, list):
            return []
        views = []
        for variant in variants:
            if not isinstance(variant, dict) or not isinstance(variant.get("name"), str):
                continue
            items = variant.get("arguments", variant.get("variables", []))
            arguments = {
                item["name"]: str(item.get("value", ""))
                for item in (items if isinstance(items, list) else [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            views.append({"name": variant["name"], "arguments": arguments})
        return views

    def to_view(self, install_root: Path, served_units: Optional[Iterable[str]]) -> dict[str, Any]:
        """API record of the system; ``served_units`` None means every onboard unit is local."""
        served = None if served_units is None else set(served_units)
        units = [{**unit, "status": _unit_status(unit, served)} for unit in self.compute_units()]
        structure_dir = self.export_dir / STRUCTURE_SUBDIR
        default_mode = self.manifest.get("default_mode")
        return {
            "id": self.id,
            "package": self.package,
            "name": self.name,
            "default_mode": default_mode if isinstance(default_mode, str) else None,
            "modes": [
                {"name": mode, "has_structure": (structure_dir / f"{mode}.json").is_file()} for mode in self.mode_keys()
            ],
            "compute_units": units,
            "deploy_variants": self.deploy_variants(),
            "pages": {
                "overview": self._page_url(install_root, f"{self.name}_overview.html"),
                "launch_commands": self._page_url(install_root, f"{self.name}_launch_commands.html"),
            },
        }

    def _page_url(self, install_root: Path, filename: str) -> Optional[str]:
        page = self.web_dir / filename
        if not page.is_file():
            return None
        try:
            return "/" + page.relative_to(install_root).as_posix()
        except ValueError:
            return None


def _unit_status(unit: dict[str, str], served: Optional[set[str]]) -> str:
    if unit["kind"] == KIND_OFFBOARD:
        return UNIT_OFFBOARD
    if unit["kind"] != KIND_ONBOARD:
        return UNIT_UNSUPPORTED
    if served is None or unit["name"] in served:
        return UNIT_AVAILABLE
    return UNIT_REMOTE_UNSUPPORTED


@dataclass(frozen=True)
class DiscoveryError:
    path: str
    message: str


def discover_systems(install_root: Path) -> tuple[list[SystemExport], list[DiscoveryError]]:
    """Every export manifest under the install root, sorted by package then system name."""
    systems: dict[str, SystemExport] = {}
    errors: list[DiscoveryError] = []
    candidates = [(p, p.parts[-4]) for p in install_root.glob(f"share/*/exports/*/{MANIFEST_FILENAME}")]
    candidates += [
        (p, p.parts[-4])
        for p in install_root.glob(f"*/share/*/exports/*/{MANIFEST_FILENAME}")
        if p.parts[-6] == p.parts[-4]
    ]
    for manifest_path, package in candidates:
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError) as exc:
            errors.append(DiscoveryError(str(manifest_path), f"unreadable manifest: {exc}"))
            continue
        if not isinstance(manifest, dict):
            errors.append(DiscoveryError(str(manifest_path), "manifest is not a JSON object"))
            continue
        export = SystemExport(package, manifest_path.parent.name, manifest_path.parent, manifest)
        systems.setdefault(export.id, export)
    return sorted(systems.values(), key=lambda s: (s.package, s.name)), errors


def static_file(install_root: Path, url_path: str) -> Optional[Path]:
    """File behind a request path when it lies in the servable subset of the install tree, else None.

    Servable: the systems index files at the root, and anything under an export's visualization/web.
    Dot segments, hidden names and symlinks leaving the permitted directory are refused.
    """
    parts = (url_path[1:] if url_path.startswith("/") else url_path).split("/")
    if any(p in ("", ".", "..") or p.startswith(".") or "\\" in p or "\0" in p for p in parts):
        return None
    if "/".join(parts) in INDEX_FILES:
        base = install_root
    else:
        base_len = _web_dir_prefix_len(parts)
        if base_len is None or len(parts) <= base_len:
            return None
        base = install_root.joinpath(*parts[:base_len])
    candidate = install_root.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _web_dir_prefix_len(parts: list[str]) -> Optional[int]:
    """Segment count of a leading <pkg>/share/<pkg>/exports/<system>/visualization/web (or merged) prefix."""
    for offset in (1, 0):
        head = parts[offset:]
        if len(head) < 6 or head[0] != "share" or head[2] != "exports" or tuple(head[4:6]) != WEB_SUBPATH:
            continue
        if offset and parts[0] != head[1]:
            continue
        return offset + 6
    return None
