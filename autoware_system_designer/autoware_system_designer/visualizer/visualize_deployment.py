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


"""Generate the per-deployment web visualization bundle under <visualization>/web/."""

from __future__ import annotations

import logging
import os
from typing import Dict

from autoware_system_designer.common.template_renderer import TemplateRenderer
from autoware_system_designer.model.export_schema import DeploymentDataByMode
from autoware_system_designer.visualizer import assets
from autoware_system_designer.visualizer.paths import systems_index_link
from autoware_system_designer.visualizer.visualization_guide import inject_vis_guides

logger = logging.getLogger(__name__)

EDITOR_SCHEME = "vscode"


def _generate_js_data(renderer: TemplateRenderer, mode_key: str, data: Dict, web_data_dir: str) -> None:
    """Write one data file per diagram type; each assigns the mode's tree to its window variable."""
    for diagram_type, window_variable in assets.DIAGRAM_TYPES.items():
        output_path = os.path.join(web_data_dir, f"{mode_key}_{diagram_type}.js")
        renderer.render_template_to_file(
            "data/common_design_data.js.jinja2",
            output_path,
            **{**data, "mode": mode_key, "window_variable": window_variable},
        )


def _copy_web_assets(web_dir: str) -> None:
    """Place the scripts and stylesheet the overview page loads beside it."""
    for script in assets.OVERVIEW_SCRIPTS:
        assets.copy_into(script, web_dir)
    assets.copy_into(assets.STYLESHEET, os.path.join(web_dir, "css"))


def _generate_config(
    renderer: TemplateRenderer,
    web_dir: str,
    name: str,
    modes: list[str],
    system_definition_file: str,
) -> None:
    """Write config.js, the page's entry point for modes, diagram types and editor links."""
    renderer.render_template_to_file(
        "data/deployment_config.js.jinja2",
        os.path.join(web_dir, "config.js"),
        deployment_name=name,
        package_name=name,
        available_modes=modes,
        available_diagram_types=list(assets.DIAGRAM_TYPES),
        default_mode="default" if "default" in modes else modes[0],
        default_diagram_type=assets.DEFAULT_DIAGRAM_TYPE,
        systems_index_path=systems_index_link(web_dir),
        editor_scheme=EDITOR_SCHEME,
        system_definition_file=system_definition_file,
    )
    logger.info("Generated deployment config: config.js")


def visualize_deployment(
    deploy_data: DeploymentDataByMode,
    name: str,
    visualization_dir: str,
    system_definition_file: str | None = None,
):
    """Generate visualization files for deployment data.

    Args:
        deploy_data: Dictionary mapping mode names to deployment data dictionaries
        name: Base name for the deployment
        visualization_dir: Directory to output visualization files
        system_definition_file: Source system file, linked from the page for editor jumps
    """
    renderer = TemplateRenderer()
    web_dir = os.path.join(visualization_dir, "web")
    web_data_dir = os.path.join(web_dir, "data")

    for mode_key, data in deploy_data.items():
        inject_vis_guides(data)
        _generate_js_data(renderer, mode_key, data, web_data_dir)
        logger.info(f"Generated visualization for mode: {mode_key}")

    if not deploy_data:
        return

    _copy_web_assets(web_dir)
    _generate_config(renderer, web_dir, name, list(deploy_data), system_definition_file or "")

    overview_path = os.path.join(web_dir, f"{name}_overview.html")
    if assets.copy_asset(assets.OVERVIEW_PAGE, overview_path):
        logger.info(f"Generated deployment overview: {os.path.basename(overview_path)}")
