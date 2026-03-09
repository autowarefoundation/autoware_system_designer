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
# WITHOUT WARRANTIES OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate the launch commands HTML page for a deployment (modes × ECUs [× deploy variants])."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..file_io.template_renderer import TemplateRenderer
from .visualization_index import get_install_root

logger = logging.getLogger(__name__)

LAUNCH_FILE_SUFFIX = ".launch.xml"


def _discover_compute_units_in_dir(mode_dir: Path) -> list[str]:
    """Discover compute units by listing subdirs of mode_dir that contain a .launch.xml file."""
    if not mode_dir.is_dir():
        return []
    return [
        entry.name
        for entry in sorted(mode_dir.iterdir())
        if entry.is_dir() and list(entry.glob(f"*{LAUNCH_FILE_SUFFIX}"))
    ]


def _build_launch_path_arg(
    package_name: str | None,
    system_name: str,
    path_after_launcher: str,
    launcher_base: Path,
    web_dir: str,
) -> str:
    """Build the path argument for ros2 launch (canonical or workspace-relative)."""
    if package_name:
        return f"install/{package_name}/share/{package_name}/exports/{system_name}/launcher" f"/{path_after_launcher}"
    launch_file = (launcher_base / path_after_launcher).resolve()
    install_root = get_install_root(Path(web_dir))
    if install_root and install_root.exists():
        try:
            return launch_file.relative_to(install_root.parent).as_posix()
        except ValueError:
            pass
    return launch_file.as_posix()


def _build_launch_commands(
    system_name: str,
    package_name: str | None,
    launcher_dir: str,
    mode_keys: list[str],
    web_dir: str,
) -> list[tuple[str, str, str]]:
    """Build list of (mode, compute_unit, launch_command) for all modes and ECUs.

    Command format: ros2 launch <path_relative_to_workspace>
    Path uses canonical form: install/<package_name>/share/.../launcher/<mode>/<ecu>/<ecu>.launch.xml
    """
    launcher_root = Path(launcher_dir).resolve()
    result: list[tuple[str, str, str]] = []
    for mode_key in mode_keys:
        mode_dir = launcher_root / mode_key
        for compute_unit in _discover_compute_units_in_dir(mode_dir):
            launch_filename = f"{compute_unit.lower()}{LAUNCH_FILE_SUFFIX}"
            path_after = f"{mode_key}/{compute_unit}/{launch_filename}"
            path_arg = _build_launch_path_arg(package_name, system_name, path_after, launcher_root, web_dir)
            result.append((mode_key, compute_unit, f"ros2 launch {path_arg}"))
    return result


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


def _flat_commands_to_rows(
    commands: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """Convert flat (mode, ecu, cmd) list to rows with mode/ecu grouping and rowspans (no deploy)."""
    rows = [
        {"mode": mode_key, "ecu": compute_unit, "deploy_name": "", "cmd": cmd}
        for mode_key, compute_unit, cmd in commands
    ]
    _add_row_span_metadata(rows)
    return rows


def _path_arg_for_deploy(
    package_name: str | None,
    system_name: str,
    launcher_dir: str,
    deploy_name: str,
    mode_key: str,
    compute_unit: str,
    web_dir: str,
) -> str:
    """Build path argument for a deploy-variant launch file."""
    launch_filename = f"{compute_unit.lower()}{LAUNCH_FILE_SUFFIX}"
    path_after = f"deployments/{deploy_name}/{mode_key}/{compute_unit}/{launch_filename}"
    launcher_base = Path(launcher_dir)
    return _build_launch_path_arg(package_name, system_name, path_after, launcher_base, web_dir)


def _build_deploy_commands(
    system_name: str,
    package_name: str | None,
    launcher_dir: str,
    mode_keys: list[str],
    deploy_variants: list[dict[str, Any]],
    web_dir: str,
) -> list[tuple[str, str, str, str]]:
    """Build list of (mode, ecu, deploy_name, launch_command) ordered by mode > ecu > deploy."""
    result: list[tuple[str, str, str, str]] = []
    deployments_dir = Path(launcher_dir) / "deployments"
    if not deployments_dir.is_dir():
        return result
    for mode_key in mode_keys:
        mode_entries: list[tuple[str, str, str]] = []
        for deploy_item in deploy_variants:
            deploy_name = deploy_item.get("name")
            if not deploy_name:
                continue
            mode_dir = deployments_dir / deploy_name / mode_key
            for compute_unit in _discover_compute_units_in_dir(mode_dir):
                path_arg = _path_arg_for_deploy(
                    package_name,
                    system_name,
                    launcher_dir,
                    deploy_name,
                    mode_key,
                    compute_unit,
                    web_dir,
                )
                mode_entries.append((compute_unit, deploy_name, f"ros2 launch {path_arg}"))
        mode_entries.sort(key=lambda x: (x[0], x[1]))
        for compute_unit, deploy_name, cmd in mode_entries:
            result.append((mode_key, compute_unit, deploy_name, cmd))
    return result


def _build_command_groups(
    deploy_commands: list[tuple[str, str, str, str]],
) -> list[dict[str, Any]]:
    """Build hierarchy mode > ecu > deploys for template with rowspans."""
    if not deploy_commands:
        return []
    groups: list[dict[str, Any]] = []
    current_mode: str | None = None
    mode_group: dict[str, Any] | None = None
    current_ecu: str | None = None
    ecu_group: dict[str, Any] | None = None
    for mode_key, compute_unit, deploy_name, cmd in deploy_commands:
        if mode_key != current_mode:
            current_mode = mode_key
            mode_group = {"mode": mode_key, "mode_rowspan": 0, "ecus": []}
            groups.append(mode_group)
            current_ecu = None
        if compute_unit != current_ecu:
            current_ecu = compute_unit
            ecu_group = {"ecu": compute_unit, "ecu_rowspan": 0, "deploys": []}
            if mode_group is not None:
                mode_group["ecus"].append(ecu_group)
        deploy_entry = {"deploy_name": deploy_name, "cmd": cmd}
        if ecu_group is not None:
            ecu_group["deploys"].append(deploy_entry)
            ecu_group["ecu_rowspan"] += 1
        if mode_group is not None:
            mode_group["mode_rowspan"] += 1
    return groups


def _command_groups_to_rows(command_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert command_groups to flat rows with show_mode_cell, mode_rowspan, show_ecu_cell, ecu_rowspan."""
    rows = [
        {
            "mode": mg["mode"],
            "ecu": eg["ecu"],
            "deploy_name": deploy["deploy_name"],
            "cmd": deploy["cmd"],
        }
        for mg in command_groups
        for eg in mg["ecus"]
        for deploy in eg["deploys"]
    ]
    _add_row_span_metadata(rows)
    return rows


def _calculate_systems_index_path(web_dir: str) -> str:
    """Calculate relative path from web_dir to systems.html index."""
    install_root = get_install_root(Path(web_dir))
    if install_root and install_root.exists():
        try:
            rel_to_root = os.path.relpath(install_root, web_dir)
            return os.path.join(rel_to_root, "systems.html")
        except ValueError:
            logger.warning("Could not calculate relative path from %s to %s", web_dir, install_root)
    return "../../../../../../../systems.html"


def generate_launch_commands_page(
    system_name: str,
    package_name: str | None,
    launcher_dir: str,
    mode_keys: list[str],
    web_dir: str,
    deploy_variants: list[dict[str, Any]] | None = None,
) -> None:
    """Generate the launch commands HTML page for a deployment.

    When deploy_variants is non-empty, lists commands per mode × ECU × deploy (cells split by deploy).
    Otherwise lists per mode × ECU only.

    Writes web_dir/<system_name>_launch_commands.html listing, for each mode and ECU
    (and deploy variant when present), the corresponding ros2 launch command.

    Args:
        system_name: Deployment/system name.
        package_name: ROS package name; when set, path uses canonical install/share form.
        launcher_dir: Path to exports/<name>/launcher/ (used to discover modes/ECUs and fallback path).
        mode_keys: List of mode identifiers.
        web_dir: Directory to write the HTML file (e.g. visualization/web).
        deploy_variants: Optional list of deploy items (name, arguments); when set, uses launcher/deployments/.
    """
    deploy_variants = deploy_variants or []
    if deploy_variants:
        deploy_commands = _build_deploy_commands(
            system_name, package_name, launcher_dir, mode_keys, deploy_variants, web_dir
        )
        command_groups = _build_command_groups(deploy_commands)
        command_rows = _command_groups_to_rows(command_groups)
    else:
        commands = _build_launch_commands(system_name, package_name, launcher_dir, mode_keys, web_dir)
        command_rows = _flat_commands_to_rows(commands)

    systems_index_path = _calculate_systems_index_path(web_dir)
    overview_path = f"{system_name}_overview.html"

    renderer = TemplateRenderer()
    output_path = os.path.join(web_dir, f"{system_name}_launch_commands.html")
    renderer.render_template_to_file(
        "launch_commands.html.jinja2",
        output_path,
        system_name=system_name,
        package_name=package_name or "",
        command_rows=command_rows,
        systems_index_path=systems_index_path,
        overview_path=overview_path,
    )
    logger.info("Generated launch commands page: %s", output_path)
