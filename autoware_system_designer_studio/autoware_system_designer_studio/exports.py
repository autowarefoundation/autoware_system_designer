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
BUILD_SCRIPTS_SUBDIR = "build_scripts"
LATENCY_SUBDIR = "latency"
# A system's icon sits beside its design file: ``<System>.icon.svg``.
ICON_FILE_SUFFIX = ".icon.svg"
ICON_MAX_BYTES = 256 * 1024

# Reference latency shapes the designer exports, in preference order.
REFERENCE_LATENCY_SUFFIXES = (".json", ".js")

# Kind of an exported target; a manifest without one is a system.
TARGET_SYSTEM = "system"
TARGET_SUBSYSTEM = "subsystem"

KIND_ONBOARD = "onboard"
KIND_OFFBOARD = "offboard"

# Compute unit status as seen by this daemon; clients treat unknown values as opaque.
UNIT_AVAILABLE = "available"
UNIT_REMOTE_UNSUPPORTED = "remote_unsupported"
# A unit bound to a peer daemon that does not answer.
UNIT_UNREACHABLE = "unreachable"
UNIT_OFFBOARD = "offboard"
UNIT_UNSUPPORTED = "unsupported"
# An available unit with a launch of it active.
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

    @property
    def kind(self) -> str:
        return TARGET_SUBSYSTEM if self.manifest.get("kind") == TARGET_SUBSYSTEM else TARGET_SYSTEM

    def subsystem(self) -> Optional[dict[str, Any]]:
        """Launch-file interface of a subsystem and the command launching it; None for a system."""
        if self.kind != TARGET_SUBSYSTEM:
            return None
        record = self.manifest.get("subsystem")
        record = record if isinstance(record, dict) else {}
        launch_file = record.get("launch_file")
        launch_file = launch_file if isinstance(launch_file, str) else ""
        package = self.manifest.get("deployment_package_name")
        package = package if isinstance(package, str) and package else self.package
        return {
            "launch_file": launch_file,
            "launch_file_path": str(self.export_dir.parents[1] / launch_file) if launch_file else "",
            "launch_command": f"ros2 launch {package} {Path(launch_file).name}" if launch_file else "",
            "arguments": [
                {
                    "name": item["name"],
                    "default": None if item.get("default") is None else str(item["default"]),
                    "description": str(item.get("description") or ""),
                }
                for item in _named_records(record.get("arguments"))
            ],
            "subscribers": _interface_ports(record.get("subscribers")),
            "publishers": _interface_ports(record.get("publishers")),
            "servers": _interface_ports(record.get("servers")),
            "clients": _interface_ports(record.get("clients")),
            "system_launches": [
                {
                    "package": str(item.get("package") or ""),
                    "file": str(item.get("file") or ""),
                    "scoped": item.get("scoped") is not False,
                    "arguments": [
                        {"name": argument["name"], "value": str(argument.get("value", ""))}
                        for argument in _named_records(item.get("arguments"))
                    ],
                }
                for item in (record.get("system_launches") or [])
                if isinstance(item, dict)
            ],
        }

    def icon_file(self) -> Optional[Path]:
        """The system's own icon; None without one beside the design file, or when it is oversized."""
        if not self.system_file:
            return None
        candidate = Path(self.system_file).with_name(f"{self.name}{ICON_FILE_SUFFIX}")
        try:
            if not candidate.is_file():
                return None
            size = candidate.stat().st_size
        except OSError as exc:
            logger.warning("system icon ignored: %s (%s)", candidate, exc)
            return None
        if size > ICON_MAX_BYTES:
            logger.warning("system icon ignored: %s is %d bytes, over %d", candidate, size, ICON_MAX_BYTES)
            return None
        return candidate

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
        """Declared units (name, kind, description only); without a declaration, the units any mode launches."""
        declared = self._declared_units()
        if declared:
            return declared
        names = dict.fromkeys(name for mode in self.mode_keys() for name in self.launch_units(mode))
        return [{"name": name, "kind": KIND_ONBOARD, "description": ""} for name in names]

    def launch_units(self, mode: str) -> list[str]:
        """Units the manifest records as launched in ``mode``; without the record, the declared onboard units.

        The launcher tree is not listed: files left by an earlier build would pass for units.
        """
        recorded = self.manifest.get("launch_units")
        if isinstance(recorded, dict):
            units = recorded.get(mode)
            return [name for name in units if isinstance(name, str) and name] if isinstance(units, list) else []
        return [unit["name"] for unit in self._declared_units() if unit["kind"] == KIND_ONBOARD]

    def _declared_units(self) -> list[dict[str, str]]:
        declared = self.manifest.get("compute_units")
        if not isinstance(declared, list):
            return []
        return [
            {
                "name": unit["name"],
                "kind": str(unit.get("kind") or KIND_ONBOARD),
                "description": str(unit.get("description") or ""),
            }
            for unit in declared
            if isinstance(unit, dict) and isinstance(unit.get("name"), str)
        ]

    def build_script_units(self, mode: str) -> list[str]:
        """Launched units of ``mode`` with a generated build script; a unit without packages has none."""
        scripts_dir = self.export_dir / BUILD_SCRIPTS_SUBDIR / mode
        return sorted(unit for unit in self.launch_units(mode) if (scripts_dir / f"build_{unit}.sh").is_file())

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
            "kind": self.kind,
            "default_mode": default_mode if isinstance(default_mode, str) else None,
            "modes": [
                {"name": mode, "has_structure": (structure_dir / f"{mode}.json").is_file()} for mode in self.mode_keys()
            ],
            "compute_units": units,
            "build_scripts": {mode: self.build_script_units(mode) for mode in self.mode_keys()},
            "deploy_variants": self.deploy_variants(),
            "pages": pages,
        }


def _named_records(value: Any) -> list[dict]:
    """Records of a manifest list that carry a name."""
    items = value if isinstance(value, list) else []
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("name"), str)]


def _interface_ports(value: Any) -> list[dict[str, str]]:
    return [{"name": item["name"], "topic": str(item.get("topic") or "")} for item in _named_records(value)]


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


def find_system(install_root: Path, package: str, name: str) -> Optional[SystemExport]:
    """The export of one system under the install root, merged or isolated layout; None without a readable one."""
    if not package or not name or "/" in package or "/" in name or package.startswith(".") or name.startswith("."):
        return None
    relative = Path("share", package, "exports", name, MANIFEST_FILENAME)
    for manifest_path in (install_root / relative, install_root / package / relative):
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict):
            return SystemExport(package, name, manifest_path.parent, manifest)
    return None


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
