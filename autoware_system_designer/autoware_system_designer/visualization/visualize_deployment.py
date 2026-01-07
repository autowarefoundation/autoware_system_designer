# Copyright 2025 TIER IV, inc.
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


import os
import logging
import shutil
from typing import Dict, List, Optional
from pathlib import Path
from ..utils.template_utils import TemplateRenderer
from .visualization_index import get_install_root

logger = logging.getLogger(__name__)

# Get template directories from installed location
def _get_template_directories() -> List[str]:
    """Get template directories from local source."""
    # Check relative to this file (works for source and site-packages)
    # autoware_system_designer/visualization/visualize_deployment.py
    # -> autoware_system_designer/template
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(base_dir, 'template')
    
    if os.path.exists(template_dir):
        return [
            template_dir,
            os.path.join(template_dir, "launcher"),
            os.path.join(template_dir, "visualization"),
        ]
    return []

TEMPLATE_DIRS = _get_template_directories()

def _get_static_file_path(filename: str) -> Optional[str]:
    """Get static file path from local source."""
    # Check relative to this file (works for source and site-packages)
    current_dir = os.path.dirname(os.path.abspath(__file__))

    local_file = os.path.join(current_dir, filename)
    if os.path.exists(local_file):
        return local_file
    
    logger.warning(f"Static file not found: {filename}")
    return None


def _copy_static_asset(filename: str, destination_dir: str) -> None:
    """Copy a static asset to the destination directory.
    
    Args:
        filename: Relative path of the static asset (e.g. 'visualization/js/node.js')
        destination_dir: Directory where the file should be copied
    """
    src = _get_static_file_path(filename)
    if src:
        dest_filename = os.path.basename(filename)
        output_path = os.path.join(destination_dir, dest_filename)
        shutil.copy2(src, output_path)
        logger.info(f"Copied static asset: {dest_filename}")
    else:
        logger.error(f"Failed to find static file: {filename}")


def _generate_js_data(renderer: TemplateRenderer, mode_key: str, data: Dict, web_data_dir: str) -> None:
    """Generate JavaScript data files for web visualization."""
    # Node diagram data
    node_data = {**data, "mode": mode_key, "window_variable": "systemDesignData"}
    output_path = os.path.join(web_data_dir, f"{mode_key}_node_diagram.js")
    renderer.render_template_to_file("visualization/data/common_design_data.js.jinja2", output_path, **node_data)

    # Sequence diagram data
    sequence_data = {**data, "mode": mode_key, "window_variable": "sequenceDiagramData"}
    output_path = os.path.join(web_data_dir, f"{mode_key}_sequence_diagram.js")
    renderer.render_template_to_file("visualization/data/common_design_data.js.jinja2", output_path, **sequence_data)

    # Logic diagram data
    logic_data = {**data, "mode": mode_key, "window_variable": "logicDiagramData"}
    output_path = os.path.join(web_data_dir, f"{mode_key}_logic_diagram.js")
    renderer.render_template_to_file("visualization/data/common_design_data.js.jinja2", output_path, **logic_data)


def _calculate_systems_index_path(web_dir: str) -> str:
    """Calculate relative path to systems.html index."""
    install_root = get_install_root(Path(web_dir))
    if install_root:
        try:
            rel_to_root = os.path.relpath(install_root, web_dir)
            return os.path.join(rel_to_root, "systems.html")
        except ValueError:
            logger.warning(f"Could not calculate relative path from {web_dir} to {install_root}")
    return "../../../../../../../systems.html"


def visualize_deployment(deploy_data: Dict[str, Dict], name: str, visualization_dir: str):
    """Generate visualization files for deployment data.

    Args:
        deploy_data: Dictionary mapping mode names to deployment data dictionaries
        name: Base name for the deployment
        visualization_dir: Directory to output visualization files
    """
    # Initialize template renderer with template directories
    renderer = TemplateRenderer(template_dir=TEMPLATE_DIRS)
    web_dir = os.path.join(visualization_dir, "web")
    web_data_dir = os.path.join(web_dir, "data")
    
    # Generate visualization for each mode
    for mode_key, data in deploy_data.items():
        _generate_js_data(renderer, mode_key, data, web_data_dir)
        logger.info(f"Generated visualization for mode: {mode_key}")

    # Generate web visualization files
    if deploy_data:
        modes = list(deploy_data.keys())
        default_mode = "default" if "default" in modes else modes[0]

        # Copy static JS modules
        js_modules = [
            "js/node_diagram.js",
            "js/sequence_diagram.js",
            "js/logic_diagram.js"
        ]
        for module in js_modules:
            _copy_static_asset(module, web_dir)

        # Generate config.js
        systems_index_rel_path = _calculate_systems_index_path(web_dir)
        
        overview_data = {
            "deployment_name": name,
            "package_name": name,
            "available_modes": modes,
            "available_diagram_types": ["node_diagram", "sequence_diagram", "logic_diagram"],
            "default_mode": default_mode,
            "default_diagram_type": "node_diagram",
            "systems_index_path": systems_index_rel_path
        }
        
        config_output_path = os.path.join(web_dir, "config.js")
        renderer.render_template_to_file("visualization/data/deployment_config.js.jinja2", config_output_path, **overview_data)
        logger.info(f"Generated deployment config: config.js")

        # Copy static overview HTML
        overview_html_src = _get_static_file_path("deployment_overview.html")
        if overview_html_src:
            output_path = os.path.join(web_dir, f"{name}_overview.html")
            shutil.copy2(overview_html_src, output_path)
            logger.info(f"Generated deployment overview: {name}_overview.html")
        else:
            logger.error("Failed to find deployment_overview.html static file")
