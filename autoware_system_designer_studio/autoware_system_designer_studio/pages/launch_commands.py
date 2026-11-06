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


"""Launch commands of an exported system per mode × compute unit [× deploy variant], for the Launch activity."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ..exports import KIND_ONBOARD, SystemExport

logger = logging.getLogger(__name__)

LAUNCH_FILE_SUFFIX = ".launch.xml"
STRUCTURE_FILE_SUFFIX = ".json"

LAUNCHER_SUBDIR = "launcher"
STRUCTURE_SUBDIR = "system_structure"
DEPLOYMENTS_SUBDIR = "deployments"

# The console script installs under lib/<pkg>/, which only `ros2 run` resolves.
RUNTIME_PACKAGE = "autoware_system_designer_runtime"
RUNTIME_EXECUTABLE = "autoware-system-designer-launch"


class Command(NamedTuple):
    """One table entry: the two launch methods for a mode × ECU × deploy variant."""

    mode: str
    ecu: str
    deploy: str
    xml: str
    native: str


def _discover_compute_units_in_dir(mode_dir: Path) -> list[str]:
    """Discover compute units by listing subdirs of mode_dir that contain a .launch.xml file."""
    if not mode_dir.is_dir():
        return []
    return [
        entry.name
        for entry in sorted(mode_dir.iterdir())
        if entry.is_dir() and list(entry.glob(f"*{LAUNCH_FILE_SUFFIX}"))
    ]


def _compute_units_in_dir(mode_dir: Path, declared: list[str] | None) -> list[str]:
    """Units listed for one mode directory: the declared ones that have a launcher, else the discovered ones."""
    if declared is None:
        return _discover_compute_units_in_dir(mode_dir)
    units = []
    for compute_unit in declared:
        if (mode_dir / compute_unit / f"{compute_unit.lower()}{LAUNCH_FILE_SUFFIX}").is_file():
            units.append(compute_unit)
        else:
            logger.warning("No launcher for declared compute unit '%s' under %s; row omitted", compute_unit, mode_dir)
    return units


def _export_path_arg(package_name: str, system_name: str, subdir: str, relative_path: str) -> str:
    """Workspace-relative path into exports/<system>/<subdir>/ of an isolated install."""
    return f"install/{package_name}/share/{package_name}/exports/{system_name}/{subdir}/{relative_path}"


def _build_commands(
    system_name: str,
    package_name: str,
    launcher_dir: Path,
    system_structure_dir: Path,
    mode_keys: list[str],
    deploy_variants: list[dict[str, Any]],
    declared_units: Optional[list[str]],
) -> list[Command]:
    """Launch commands ordered by mode > ECU > deploy.

    Without deploy variants the launchers sit directly under launcher/<mode>/<ecu>/; with them
    each variant has its own tree under launcher/deployments/<deploy>/<mode>/<ecu>/. The runtime
    method reads system_structure/<mode>.json and binds the variant's arguments via --deploy.
    ECUs come from the declared onboard compute units when there are any, otherwise from the
    launcher directories present.
    """
    launcher_root = launcher_dir.resolve()
    structure_root = system_structure_dir.resolve()
    deploy_names = [item["name"] for item in deploy_variants if item.get("name")] if deploy_variants else [""]
    if deploy_variants and not (launcher_root / DEPLOYMENTS_SUBDIR).is_dir():
        logger.warning("No deployment launchers under %s; no launch commands listed", launcher_root)
        return []

    commands: list[Command] = []
    for mode_key in mode_keys:
        mode_entries: list[Command] = []
        structure_file = f"{mode_key}{STRUCTURE_FILE_SUFFIX}"
        has_structure = (structure_root / structure_file).is_file()
        for deploy_name in deploy_names:
            prefix = f"{DEPLOYMENTS_SUBDIR}/{deploy_name}/" if deploy_name else ""
            mode_dir = launcher_root / f"{prefix}{mode_key}"
            for compute_unit in _compute_units_in_dir(mode_dir, declared_units):
                launch_filename = f"{compute_unit.lower()}{LAUNCH_FILE_SUFFIX}"
                xml_path = _export_path_arg(
                    package_name, system_name, LAUNCHER_SUBDIR, f"{prefix}{mode_key}/{compute_unit}/{launch_filename}"
                )
                native_cmd = ""
                if has_structure:
                    structure_path = _export_path_arg(package_name, system_name, STRUCTURE_SUBDIR, structure_file)
                    deploy_arg = f" --deploy {deploy_name}" if deploy_name else ""
                    native_cmd = (
                        f"ros2 run {RUNTIME_PACKAGE} {RUNTIME_EXECUTABLE} {structure_path}"
                        f" --compute-unit {compute_unit}{deploy_arg}"
                    )
                mode_entries.append(Command(mode_key, compute_unit, deploy_name, f"ros2 launch {xml_path}", native_cmd))
        mode_entries.sort(key=lambda entry: (entry.ecu, entry.deploy))
        commands.extend(mode_entries)
    return commands


def _export_commands(export: SystemExport) -> list[Command]:
    declared = export.manifest.get("compute_units")
    declared_units = (
        [u["name"] for u in export.compute_units() if u["kind"] == KIND_ONBOARD]
        if isinstance(declared, list) and declared
        else None
    )
    return _build_commands(
        export.name,
        export.package,
        export.export_dir / LAUNCHER_SUBDIR,
        export.export_dir / STRUCTURE_SUBDIR,
        export.mode_keys(),
        export.deploy_variants(),
        declared_units,
    )


def launch_command_records(export: SystemExport) -> list[dict[str, str]]:
    """Launch commands as flat records (mode, unit, deploy, xml, native) for the workbench Launch activity.

    Both launch methods are listed: the exported XML launcher run by ``ros2 launch`` and the
    system_structure JSON run by the designer runtime (``native``, empty without a structure).
    """
    return [
        {"mode": cmd.mode, "unit": cmd.ecu, "deploy": cmd.deploy, "xml": cmd.xml, "native": cmd.native}
        for cmd in _export_commands(export)
    ]
