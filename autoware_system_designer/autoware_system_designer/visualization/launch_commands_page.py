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
from typing import Any, Dict, List, Optional, Tuple

from ..file_io.template_renderer import TemplateRenderer
from .visualization_index import get_install_root

logger = logging.getLogger(__name__)


def _discover_compute_units(launcher_dir: str, mode_key: str) -> List[str]:
    """Discover compute units by listing subdirs of launcher_dir/mode_key that contain a .launch.xml file."""
    mode_dir = Path(launcher_dir) / mode_key
    if not mode_dir.is_dir():
        return []
    compute_units = []
    for entry in sorted(mode_dir.iterdir()):
        if not entry.is_dir():
            continue
        launch_files = list(entry.glob("*.launch.xml"))
        if launch_files:
            compute_units.append(entry.name)
    return compute_units


def _discover_compute_units_under_deploy(
    deployments_dir: Path, deploy_name: str, mode_key: str
) -> List[str]:
    """Discover compute units under launcher/deployments/<deploy_name>/<mode_key>/."""
    mode_dir = deployments_dir / deploy_name / mode_key
    if not mode_dir.is_dir():
        return []
    compute_units = []
    for entry in sorted(mode_dir.iterdir()):
        if not entry.is_dir():
            continue
        launch_files = list(entry.glob("*.launch.xml"))
        if launch_files:
            compute_units.append(entry.name)
    return compute_units


def _build_launch_commands(
    system_name: str,
    package_name: Optional[str],
    launcher_dir: str,
    mode_keys: List[str],
    web_dir: str,
) -> List[Tuple[str, str, str]]:
    """Build list of (mode, compute_unit, launch_command) for all modes and ECUs.

    Command format: ros2 launch <path_relative_to_workspace>
    Path uses canonical form: install/<package_name>/share/<package_name>/exports/<system_name>/launcher/<mode>/<ecu>/<ecu>.launch.xml
    """
    result = []
    for mode_key in mode_keys:
        for compute_unit in _discover_compute_units(launcher_dir, mode_key):
            launch_filename = f"{compute_unit.lower()}.launch.xml"
            if package_name:
                path_arg = (
                    f"install/{package_name}/share/{package_name}/exports/{system_name}/launcher"
                    f"/{mode_key}/{compute_unit}/{launch_filename}"
                )
            else:
                launcher_root = Path(launcher_dir).resolve()
                launch_file = launcher_root / mode_key / compute_unit / launch_filename
                install_root = get_install_root(Path(web_dir))
                if install_root and install_root.exists():
                    workspace_root = install_root.parent
                    try:
                        path_arg = launch_file.relative_to(workspace_root).as_posix()
                    except ValueError:
                        path_arg = launch_file.as_posix()
                else:
                    path_arg = launch_file.as_posix()
            cmd = f"ros2 launch {path_arg}"
            result.append((mode_key, compute_unit, cmd))
    return result


def _flat_commands_to_rows(
    commands: List[Tuple[str, str, str]],
) -> List[Dict[str, Any]]:
    """Convert flat (mode, ecu, cmd) list to rows with mode/ecu grouping and rowspans (no deploy variants)."""
    if not commands:
        return []
    rows: List[Dict[str, Any]] = []
    for mode_key, compute_unit, cmd in commands:
        rows.append({
            "mode": mode_key,
            "ecu": compute_unit,
            "deploy_name": "",
            "cmd": cmd,
        })
    for idx, row in enumerate(rows):
        row["show_mode_cell"] = (
            idx == 0 or rows[idx - 1]["mode"] != row["mode"]
        )
        row["show_ecu_cell"] = (
            idx == 0 or rows[idx - 1]["mode"] != row["mode"] or rows[idx - 1]["ecu"] != row["ecu"]
        )
    for idx, row in enumerate(rows):
        if row["show_mode_cell"]:
            row["mode_rowspan"] = sum(
                1 for r in rows[idx:] if r["mode"] == row["mode"]
            )
        else:
            row["mode_rowspan"] = 0
        if row["show_ecu_cell"]:
            row["ecu_rowspan"] = sum(
                1 for r in rows[idx:] if r["mode"] == row["mode"] and r["ecu"] == row["ecu"]
            )
        else:
            row["ecu_rowspan"] = 0
    return rows


def _path_arg_for_deploy(
    package_name: Optional[str],
    system_name: str,
    launcher_dir: str,
    deploy_name: str,
    mode_key: str,
    compute_unit: str,
    web_dir: str,
) -> str:
    """Build path argument for a deploy-variant launch file."""
    launch_filename = f"{compute_unit.lower()}.launch.xml"
    if package_name:
        return (
            f"install/{package_name}/share/{package_name}/exports/{system_name}/launcher"
            f"/deployments/{deploy_name}/{mode_key}/{compute_unit}/{launch_filename}"
        )
    launcher_root = Path(launcher_dir) / "deployments" / deploy_name / mode_key / compute_unit
    launch_file = launcher_root / launch_filename
    install_root = get_install_root(Path(web_dir))
    if install_root and install_root.exists():
        workspace_root = install_root.parent
        try:
            return launch_file.resolve().relative_to(workspace_root).as_posix()
        except ValueError:
            return launch_file.resolve().as_posix()
    return launch_file.resolve().as_posix()


def _build_deploy_commands(
    system_name: str,
    package_name: Optional[str],
    launcher_dir: str,
    mode_keys: List[str],
    deploy_variants: List[Dict[str, Any]],
    web_dir: str,
) -> List[Tuple[str, str, str, str]]:
    """Build list of (mode, ecu, deploy_name, launch_command) ordered by mode > ecu > deploy."""
    result: List[Tuple[str, str, str, str]] = []
    deployments_dir = Path(launcher_dir) / "deployments"
    if not deployments_dir.is_dir():
        return result
    for mode_key in mode_keys:
        # Collect (ecu, deploy_name, cmd) for this mode so we can order by ecu then deploy
        mode_entries: List[Tuple[str, str, str]] = []
        for deploy_item in deploy_variants:
            deploy_name = deploy_item.get("name")
            if not deploy_name:
                continue
            for compute_unit in _discover_compute_units_under_deploy(
                deployments_dir, deploy_name, mode_key
            ):
                path_arg = _path_arg_for_deploy(
                    package_name,
                    system_name,
                    launcher_dir,
                    deploy_name,
                    mode_key,
                    compute_unit,
                    web_dir,
                )
                cmd = f"ros2 launch {path_arg}"
                mode_entries.append((compute_unit, deploy_name, cmd))
        mode_entries.sort(key=lambda x: (x[0], x[1]))
        for compute_unit, deploy_name, cmd in mode_entries:
            result.append((mode_key, compute_unit, deploy_name, cmd))
    return result


def _build_command_groups(
    deploy_commands: List[Tuple[str, str, str, str]],
) -> List[Dict[str, Any]]:
    """Build hierarchy mode > ecu > deploys for template with rowspans."""
    if not deploy_commands:
        return []
    groups: List[Dict[str, Any]] = []
    current_mode: Optional[str] = None
    mode_group: Optional[Dict[str, Any]] = None
    current_ecu: Optional[str] = None
    ecu_group: Optional[Dict[str, Any]] = None
    for mode_key, compute_unit, deploy_name, cmd in deploy_commands:
        if mode_key != current_mode:
            current_mode = mode_key
            mode_group = {
                "mode": mode_key,
                "mode_rowspan": 0,
                "ecus": [],
            }
            groups.append(mode_group)
            current_ecu = None
        if compute_unit != current_ecu:
            current_ecu = compute_unit
            ecu_group = {
                "ecu": compute_unit,
                "ecu_rowspan": 0,
                "deploys": [],
            }
            if mode_group:
                mode_group["ecus"].append(ecu_group)
        deploy_entry = {"deploy_name": deploy_name, "cmd": cmd}
        if ecu_group:
            ecu_group["deploys"].append(deploy_entry)
            ecu_group["ecu_rowspan"] += 1
        if mode_group:
            mode_group["mode_rowspan"] += 1
    return groups


def _command_groups_to_rows(
    command_groups: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert command_groups to flat rows with show_mode_cell, mode_rowspan, show_ecu_cell, ecu_rowspan."""
    rows: List[Dict[str, Any]] = []
    for mode_group in command_groups:
        for ecu_group in mode_group["ecus"]:
            for deploy in ecu_group["deploys"]:
                rows.append({
                    "mode": mode_group["mode"],
                    "ecu": ecu_group["ecu"],
                    "deploy_name": deploy["deploy_name"],
                    "cmd": deploy["cmd"],
                })
    for idx, row in enumerate(rows):
        row["show_mode_cell"] = (
            idx == 0 or rows[idx - 1]["mode"] != row["mode"]
        )
        row["show_ecu_cell"] = (
            idx == 0 or rows[idx - 1]["mode"] != row["mode"] or rows[idx - 1]["ecu"] != row["ecu"]
        )
    for idx, row in enumerate(rows):
        if row["show_mode_cell"]:
            row["mode_rowspan"] = sum(
                1 for r in rows[idx:] if r["mode"] == row["mode"]
            )
        else:
            row["mode_rowspan"] = 0
        if row["show_ecu_cell"]:
            row["ecu_rowspan"] = sum(
                1 for r in rows[idx:] if r["mode"] == row["mode"] and r["ecu"] == row["ecu"]
            )
        else:
            row["ecu_rowspan"] = 0
    return rows


def _calculate_systems_index_path(web_dir: str) -> str:
    """Calculate relative path from web_dir to systems.html index."""
    install_root = get_install_root(Path(web_dir))
    if install_root and install_root.exists():
        try:
            rel_to_root = os.path.relpath(install_root, web_dir)
            return os.path.join(rel_to_root, "systems.html")
        except ValueError:
            logger.warning(
                "Could not calculate relative path from %s to %s", web_dir, install_root
            )
    return "../../../../../../../systems.html"


def generate_launch_commands_page(
    system_name: str,
    package_name: Optional[str],
    launcher_dir: str,
    mode_keys: List[str],
    web_dir: str,
    deploy_variants: Optional[List[Dict[str, Any]]] = None,
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
        commands = _build_launch_commands(
            system_name, package_name, launcher_dir, mode_keys, web_dir
        )
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
