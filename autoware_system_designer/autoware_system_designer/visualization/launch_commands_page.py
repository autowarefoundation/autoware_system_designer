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

"""Generate the launch commands HTML page for a deployment (modes × ECUs)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

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


def _build_launch_commands(
    system_name: str,
    package_name: Optional[str],
    launcher_dir: str,
    mode_keys: List[str],
) -> List[Tuple[str, str, str]]:
    """Build list of (mode, compute_unit, launch_command) for all modes and ECUs."""
    # Path relative to package share: exports/<name>/launcher/<mode>/<ecu>/<ecu>.launch.xml
    export_prefix = f"exports/{system_name}/launcher"
    result = []
    for mode_key in mode_keys:
        for compute_unit in _discover_compute_units(launcher_dir, mode_key):
            launch_path = f"{export_prefix}/{mode_key}/{compute_unit}/{compute_unit.lower()}.launch.xml"
            if package_name:
                cmd = f"ros2 launch {package_name} {launch_path}"
            else:
                cmd = f"# ros2 launch <package_name> {launch_path}"
            result.append((mode_key, compute_unit, cmd))
    return result


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
) -> None:
    """Generate the launch commands HTML page for a deployment.

    Writes web_dir/<system_name>_launch_commands.html listing, for each mode and ECU,
    the corresponding ros2 launch command.

    Args:
        system_name: Deployment/system name.
        package_name: ROS package name for ros2 launch (or None to show placeholder).
        launcher_dir: Path to exports/<name>/launcher/.
        mode_keys: List of mode identifiers.
        web_dir: Directory to write the HTML file (e.g. visualization/web).
    """
    commands = _build_launch_commands(
        system_name, package_name, launcher_dir, mode_keys
    )
    systems_index_path = _calculate_systems_index_path(web_dir)
    overview_path = f"{system_name}_overview.html"

    renderer = TemplateRenderer()
    output_path = os.path.join(web_dir, f"{system_name}_launch_commands.html")
    renderer.render_template_to_file(
        "launch_commands.html.jinja2",
        output_path,
        system_name=system_name,
        package_name=package_name or "",
        commands=commands,
        systems_index_path=systems_index_path,
        overview_path=overview_path,
    )
    logger.info("Generated launch commands page: %s", output_path)
