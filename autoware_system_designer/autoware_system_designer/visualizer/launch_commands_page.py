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


"""Generate the launch commands HTML page for a deployment (modes × ECUs [× deploy variants])."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, NamedTuple

from autoware_system_designer.common.template_renderer import TemplateRenderer
from autoware_system_designer.visualizer.paths import get_install_root, systems_index_link

logger = logging.getLogger(__name__)

LAUNCH_FILE_SUFFIX = ".launch.xml"
STRUCTURE_FILE_SUFFIX = ".json"

LAUNCHER_SUBDIR = "launcher"
STRUCTURE_SUBDIR = "system_structure"
DEPLOYMENTS_SUBDIR = "deployments"

# The console script installs under lib/<pkg>/, which only `ros2 run` resolves.
RUNTIME_PACKAGE = "autoware_system_designer_runtime"
RUNTIME_EXECUTABLE = "autoware-system-designer-launch"

# Used when the page lives outside an install tree and no relative index path can be derived.
SYSTEMS_INDEX_FALLBACK = "../../../../../../../systems.html"


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


def _build_export_path_arg(
    package_name: str | None,
    system_name: str,
    subdir: str,
    relative_path: str,
    base_dir: Path,
    web_dir: str,
) -> str:
    """Build a path argument pointing into exports/<system>/<subdir>/ (canonical or workspace-relative)."""
    if package_name:
        return f"install/{package_name}/share/{package_name}/exports/{system_name}/{subdir}/{relative_path}"
    target = (base_dir / relative_path).resolve()
    install_root = get_install_root(Path(web_dir))
    if install_root and install_root.exists():
        try:
            return target.relative_to(install_root.parent).as_posix()
        except ValueError:
            pass
    return target.as_posix()


def _build_commands(
    system_name: str,
    package_name: str | None,
    launcher_dir: str,
    system_structure_dir: str | None,
    mode_keys: list[str],
    deploy_variants: list[dict[str, Any]],
    web_dir: str,
) -> list[Command]:
    """Launch commands ordered by mode > ECU > deploy.

    Without deploy variants the launchers sit directly under launcher/<mode>/<ecu>/; with them
    each variant has its own tree under launcher/deployments/<deploy>/<mode>/<ecu>/. The runtime
    method reads system_structure/<mode>.json, which carries no deploy-variant arguments.
    """
    launcher_root = Path(launcher_dir).resolve()
    structure_root = Path(system_structure_dir).resolve() if system_structure_dir else None
    deploy_names = [item["name"] for item in deploy_variants if item.get("name")] if deploy_variants else [""]
    if deploy_variants and not (launcher_root / DEPLOYMENTS_SUBDIR).is_dir():
        logger.warning("No deployment launchers under %s; launch commands page left empty", launcher_root)
        return []

    commands: list[Command] = []
    for mode_key in mode_keys:
        mode_entries: list[Command] = []
        structure_file = f"{mode_key}{STRUCTURE_FILE_SUFFIX}"
        has_structure = structure_root is not None and (structure_root / structure_file).is_file()
        for deploy_name in deploy_names:
            prefix = f"{DEPLOYMENTS_SUBDIR}/{deploy_name}/" if deploy_name else ""
            mode_dir = launcher_root / f"{prefix}{mode_key}"
            for compute_unit in _discover_compute_units_in_dir(mode_dir):
                launch_filename = f"{compute_unit.lower()}{LAUNCH_FILE_SUFFIX}"
                xml_path = _build_export_path_arg(
                    package_name,
                    system_name,
                    LAUNCHER_SUBDIR,
                    f"{prefix}{mode_key}/{compute_unit}/{launch_filename}",
                    launcher_root,
                    web_dir,
                )
                native_cmd = ""
                if has_structure:
                    structure_path = _build_export_path_arg(
                        package_name,
                        system_name,
                        STRUCTURE_SUBDIR,
                        structure_file,
                        structure_root,
                        web_dir,
                    )
                    native_cmd = (
                        f"ros2 run {RUNTIME_PACKAGE} {RUNTIME_EXECUTABLE} {structure_path} --ecu {compute_unit}"
                    )
                mode_entries.append(Command(mode_key, compute_unit, deploy_name, f"ros2 launch {xml_path}", native_cmd))
        mode_entries.sort(key=lambda entry: (entry.ecu, entry.deploy))
        commands.extend(mode_entries)
    return commands


def _add_row_span_metadata(rows: list[dict[str, Any]]) -> None:
    """Add show_mode_cell, show_ecu_cell, mode_rowspan, ecu_rowspan to each row (in-place)."""
    for idx, row in enumerate(rows):
        row["show_mode_cell"] = idx == 0 or rows[idx - 1]["mode"] != row["mode"]
        row["show_ecu_cell"] = idx == 0 or rows[idx - 1]["mode"] != row["mode"] or rows[idx - 1]["ecu"] != row["ecu"]
    for idx, row in enumerate(rows):
        row["mode_rowspan"] = sum(1 for r in rows[idx:] if r["mode"] == row["mode"]) if row["show_mode_cell"] else 0
        row["ecu_rowspan"] = (
            sum(1 for r in rows[idx:] if r["mode"] == row["mode"] and r["ecu"] == row["ecu"])
            if row["show_ecu_cell"]
            else 0
        )


def _to_rows(commands: list[Command]) -> list[dict[str, Any]]:
    """Table rows for one deploy's commands, carrying the cell-merging metadata."""
    rows = [{"mode": cmd.mode, "ecu": cmd.ecu, "xml": cmd.xml, "native": cmd.native} for cmd in commands]
    _add_row_span_metadata(rows)
    return rows


def _rows_by_deploy(commands: list[Command]) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    """Sorted deploy names and each one's table rows; the page switches between them client-side."""
    deploy_names = sorted({cmd.deploy for cmd in commands})
    return deploy_names, {name: _to_rows([cmd for cmd in commands if cmd.deploy == name]) for name in deploy_names}


def generate_launch_commands_page(
    system_name: str,
    package_name: str | None,
    launcher_dir: str,
    mode_keys: list[str],
    web_dir: str,
    deploy_variants: list[dict[str, Any]] | None = None,
    system_structure_dir: str | None = None,
) -> None:
    """Generate the launch commands HTML page for a deployment.

    When deploy_variants is non-empty, lists commands per mode × ECU × deploy (cells split by deploy).
    Otherwise lists per mode × ECU only. Each entry carries both launch methods: the exported XML
    launcher run by ``ros2 launch`` and the system_structure JSON run by the designer runtime, which
    the page selects by default whenever the structure export is present.

    Writes web_dir/<system_name>_launch_commands.html listing, for each mode and ECU
    (and deploy variant when present), the corresponding launch command per method.

    Args:
        system_name: Deployment/system name.
        package_name: ROS package name; when set, path uses canonical install/share form.
        launcher_dir: Path to exports/<name>/launcher/ (used to discover modes/ECUs and fallback path).
        mode_keys: List of mode identifiers.
        web_dir: Directory to write the HTML file (e.g. visualization/web).
        deploy_variants: Optional list of deploy items (name, arguments); when set, uses launcher/deployments/.
        system_structure_dir: Path to exports/<name>/system_structure/; omitting it drops the runtime method.
    """
    deploy_variants = deploy_variants or []
    commands = _build_commands(
        system_name,
        package_name,
        launcher_dir,
        system_structure_dir,
        mode_keys,
        deploy_variants,
        web_dir,
    )

    deploy_names, rows_by_deploy = _rows_by_deploy(commands)
    command_rows = rows_by_deploy[deploy_names[0]] if deploy_names else []
    has_native = any(cmd.native for cmd in commands)
    default_method = "native" if has_native else "xml"

    renderer = TemplateRenderer()
    output_path = os.path.join(web_dir, f"{system_name}_launch_commands.html")
    renderer.render_template_to_file(
        "launch_commands.html.jinja2",
        output_path,
        system_name=system_name,
        package_name=package_name or "",
        command_rows=command_rows,
        deploy_names=deploy_names if deploy_variants else [],
        rows_by_deploy=rows_by_deploy,
        has_native=has_native,
        default_method=default_method,
        has_deploy_variants=bool(deploy_variants),
        systems_index_path=systems_index_link(web_dir, SYSTEMS_INDEX_FALLBACK),
        overview_path=f"{system_name}_overview.html",
    )
    logger.info("Generated launch commands page: %s", output_path)
