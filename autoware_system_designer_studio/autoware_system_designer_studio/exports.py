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

"""Exported systems under an install tree: the build artifacts every studio view is drawn from.

Layout written by the designer (isolated colcon install; merged installs drop the leading package):
``<install>/<pkg>/share/<pkg>/exports/<system>/{deployment.json, system_structure/, launcher/, latency/}``.
Measured latency comes from the runtime's record root (see ``autoware_system_designer_runtime.records``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from autoware_system_designer_runtime import records
from autoware_system_designer_runtime.export_tree import (
    derive_workspace_root,
    expand_workspace_paths,
    resolve_structure_paths,
)

logger = logging.getLogger(__name__)

INSTALL_DIR_NAME = "install"
MANIFEST_FILENAME = "deployment.json"
STRUCTURE_SUBDIR = "system_structure"
LAUNCHER_SUBDIR = "launcher"
BUILD_SCRIPTS_SUBDIR = "build_scripts"
DEPLOYMENTS_SUBDIR = "deployments"
LATENCY_SUBDIR = "latency"
LAUNCH_FILE_SUFFIX = ".launch.xml"

# Reference latency shapes the designer exports, in preference order.
REFERENCE_LATENCY_SUFFIXES = (".json", ".js")

KIND_ONBOARD = "onboard"
KIND_OFFBOARD = "offboard"

# Compute unit status as seen by this daemon; clients treat unknown values as opaque.
UNIT_AVAILABLE = "available"
UNIT_REMOTE_UNSUPPORTED = "remote_unsupported"
UNIT_OFFBOARD = "offboard"
UNIT_UNSUPPORTED = "unsupported"
# An available unit with a launch of it active on this daemon.
UNIT_RUNNING = "running"


def resolve_install_root(explicit: Optional[str]) -> Optional[Path]:
    """Install tree to serve: the explicit one, else the first colcon prefix, else the one holding this package."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    for entry in os.environ.get("COLCON_PREFIX_PATH", "").split(os.pathsep):
        if entry and Path(entry).is_dir():
            return Path(entry).resolve()
    return module_install_root()


def module_install_root() -> Optional[Path]:
    """Install tree holding this package, None when it runs from a source checkout."""
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
    def system_file(self) -> str:
        """Design file the system was exported from; tokenized as exported when no workspace root is found."""
        path = self.manifest.get("system_file")
        if not isinstance(path, str):
            return ""
        root = derive_workspace_root(self.export_dir / STRUCTURE_SUBDIR / MANIFEST_FILENAME)
        return expand_workspace_paths(path, root) if root else path

    def structure_path(self, mode: str) -> Path:
        return self.export_dir / STRUCTURE_SUBDIR / f"{mode}.json"

    def structure(self, mode: str) -> tuple[dict, dict]:
        """(data, metadata) of a mode's system structure, workspace paths expanded.

        Raises OSError or ValueError when the mode has no readable structure.
        """
        if mode not in self.mode_keys():
            raise ValueError(f"{self.id} has no mode {mode!r}")
        path = self.structure_path(mode)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload = resolve_structure_paths(payload, str(path))
        if isinstance(payload, dict) and "schema_version" in payload and "data" in payload:
            return payload.get("data") or {}, payload.get("metadata") or {}
        return payload, {}

    def reference_latency(self, mode: str) -> Optional[Path]:
        """Reference measurement the designer exported for a mode, when there is one."""
        for suffix in REFERENCE_LATENCY_SUFFIXES:
            candidate = self.export_dir / LATENCY_SUBDIR / f"{mode}_latency{suffix}"
            if candidate.is_file():
                return candidate
        return None

    def latency_records(self, mode: str, record_root: Optional[Path] = None) -> list[Path]:
        """Recorded measurements of a mode, newest first."""
        return records.latency_records(self.package, self.name, mode, record_root)

    def latency_file(self, mode: str, record_root: Optional[Path] = None) -> Optional[Path]:
        """Latency a view shows for a mode: the newest record, else the exported reference."""
        recorded = self.latency_records(mode, record_root)
        return recorded[0] if recorded else self.reference_latency(mode)

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

    def build_script_units(self, mode: str) -> list[str]:
        """Compute units with a generated build script in ``mode``; a unit without packages has none."""
        scripts_dir = self.export_dir / BUILD_SCRIPTS_SUBDIR / mode
        if not scripts_dir.is_dir():
            return []
        return sorted(p.name[len("build_") : -len(".sh")] for p in scripts_dir.glob("build_*.sh") if p.is_file())

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

    def to_view(self, served_units: Optional[Iterable[str]], pages: dict[str, str]) -> dict[str, Any]:
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
            "build_scripts": {mode: self.build_script_units(mode) for mode in self.mode_keys()},
            "deploy_variants": self.deploy_variants(),
            "pages": pages,
        }


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
