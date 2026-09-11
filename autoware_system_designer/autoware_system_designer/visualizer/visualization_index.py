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


"""Maintain systems.html, the install-root index over every exported deployment."""

from __future__ import annotations

import fcntl
import logging
from dataclasses import dataclass
from pathlib import Path

from autoware_system_designer.common.source_location import SourceLocation, format_source
from autoware_system_designer.common.template_renderer import TemplateRenderer
from autoware_system_designer.visualizer import assets
from autoware_system_designer.visualizer.paths import SYSTEMS_INDEX_FILENAME, get_install_root

logger = logging.getLogger(__name__)

INDEX_LOCK_FILENAME = ".systems_index.lock"

# Exported deployments live at <install>/share/<package>/exports/<name>/visualization.
_EXPORTS_MARKER_DEPTH = -3
_DEPLOYMENT_NAME_DEPTH = -2
_PACKAGE_NAME_DEPTH = -4


@dataclass(frozen=True)
class _Deployment:
    """One exported deployment discovered under the install root."""

    name: str
    package: str
    web_dir: Path
    diagram_types: list[str]

    @property
    def key(self) -> str:
        return f"{self.package}:{self.name}"


def update_index(output_root_dir: str):
    """Rewrite systems.html in the install root; the lock serializes parallel package builds."""
    install_root = get_install_root(Path(output_root_dir).resolve())

    if not install_root or not install_root.exists():
        src = SourceLocation(file_path=Path(output_root_dir))
        logger.warning(
            f"Could not determine install root from {output_root_dir}. Skipping index update.{format_source(src)}"
        )
        return

    try:
        with open(install_root / INDEX_LOCK_FILENAME, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                _generate_index_file(install_root, install_root / SYSTEMS_INDEX_FILENAME)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except Exception as e:
        src = SourceLocation(file_path=Path(output_root_dir))
        logger.error(f"Failed to update visualization index: {e}{format_source(src)}")


def _diagram_types_in(data_dir: Path) -> list[str]:
    """Diagram types present in a deployment's data directory, read off <mode>_<type>.js names."""
    found = {
        diagram_type
        for data_file in data_dir.glob("*.js")
        for diagram_type in assets.DIAGRAM_TYPES
        if data_file.stem.endswith(f"_{diagram_type}")
    }
    return sorted(found)


def _discover_deployments(install_root: Path) -> list[_Deployment]:
    """Every exported deployment under the install root that has generated diagram data."""
    found: dict[str, _Deployment] = {}

    for visualization_dir in install_root.rglob("visualization"):
        parts = visualization_dir.parts
        if len(parts) < 5 or parts[_EXPORTS_MARKER_DEPTH] != "exports":
            continue

        web_dir = visualization_dir / "web"
        data_dir = web_dir / "data"
        if not web_dir.exists() or not data_dir.exists():
            continue

        diagram_types = _diagram_types_in(data_dir)
        if not diagram_types:
            continue

        deployment = _Deployment(
            name=parts[_DEPLOYMENT_NAME_DEPTH],
            package=parts[_PACKAGE_NAME_DEPTH],
            web_dir=web_dir,
            diagram_types=diagram_types,
        )
        found.setdefault(deployment.key, deployment)

    return sorted(found.values(), key=lambda d: (d.package, d.name))


def _to_view(deployment: _Deployment, install_root: Path) -> dict:
    """Template row for one deployment: install-root-relative links to its pages."""
    web_path = deployment.web_dir.relative_to(install_root)

    default_diagram = (
        assets.DEFAULT_DIAGRAM_TYPE
        if assets.DEFAULT_DIAGRAM_TYPE in deployment.diagram_types
        else deployment.diagram_types[0]
    )
    launch_commands_filename = f"{deployment.name}_launch_commands.html"
    has_launch_page = (deployment.web_dir / launch_commands_filename).exists()

    return {
        "name": deployment.name,
        "package": deployment.package,
        "diagram_link": f"{web_path / f'{deployment.name}_overview.html'}?diagram={default_diagram}",
        "launch_commands_link": (web_path / launch_commands_filename).as_posix() if has_launch_page else None,
    }


def _copy_index_assets(install_root: Path) -> None:
    """Place the stylesheet and theme script systems.html links, beside it."""
    for source, destination in assets.INDEX_ASSETS.items():
        assets.copy_asset(source, str(install_root / destination))


def _generate_index_file(install_root: Path, output_file: Path) -> None:
    _copy_index_assets(install_root)
    deployments = [_to_view(dep, install_root) for dep in _discover_deployments(install_root)]

    try:
        renderer = TemplateRenderer()
        renderer.render_template_to_file("systems_index.html.jinja2", str(output_file), deployments=deployments)
    except Exception as e:
        logger.error(f"Failed to render visualization index template: {e}")
