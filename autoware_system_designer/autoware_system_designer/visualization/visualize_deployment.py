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
from typing import Dict
from pathlib import Path
from ..utils.template_utils import TemplateRenderer
from .visualization_index import get_install_root

logger = logging.getLogger(__name__)

# Get template directories from installed location
def _get_template_directories():
    """Get template directories from installed share location."""
    from ament_index_python.packages import get_package_share_directory
    share_dir = get_package_share_directory('autoware_system_designer')
    share_template_dir = os.path.join(share_dir, 'template')
    return [
        share_template_dir,
        os.path.join(share_template_dir, "launcher"),
        os.path.join(share_template_dir, "visualization"),
    ]

TEMPLATE_DIRS = _get_template_directories()

def _get_static_file_path(filename: str):
    """Get static file path from installed share location or local source."""
    # Try installed location
    try:
        from ament_index_python.packages import get_package_share_directory
        share_dir = get_package_share_directory('autoware_system_designer')
        static_file = os.path.join(share_dir, 'static', filename)
        if os.path.exists(static_file):
            return static_file
    except (ImportError, Exception):
        pass

    # Fallback to source location (relative to this file)
    # this file is in autoware_system_designer/visualization/visualize_deployment.py
    # static is in autoware_system_designer/static
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_file = os.path.join(base_dir, 'static', filename)
    if os.path.exists(static_file):
        return static_file
    
    logger.warning(f"Static file not found: {filename}")
    return None


def visualize_deployment(deploy_data: Dict[str, Dict], name: str, visualization_dir: str):
    """Generate visualization files for deployment data.

    Args:
        deploy_data: Dictionary mapping mode names to deployment data dictionaries
        name: Base name for the deployment
        visualization_dir: Directory to output visualization files
    """
    # Initialize template renderer with template directories
    renderer = TemplateRenderer(template_dir=TEMPLATE_DIRS)

    # Generate visualization for each mode
    for mode_key, data in deploy_data.items():

        # Create mode-specific output directory
        mode_visualization_dir = os.path.join(visualization_dir, mode_key)

        # Generate diagrams with mode suffix in filename
        filename_base = f"{name}_{mode_key}" if mode_key != "default" else name
        output_path = os.path.join(mode_visualization_dir, filename_base + "_node_graph.dot")
        renderer.render_template_to_file("node_diagram.dot.jinja2", output_path, **data)
        output_path = os.path.join(mode_visualization_dir, filename_base + "_logic_graph.dot")
        renderer.render_template_to_file("logic_diagram.dot.jinja2", output_path, **data)

        # Generate JS data for web visualization
        web_data_dir = os.path.join(visualization_dir, "web", "data")
        node_data_with_mode = {**data, "mode": mode_key}
        output_path = os.path.join(web_data_dir, f"{mode_key}_node_diagram.js")
        renderer.render_template_to_file("visualization/data/node_diagram_data.js.jinja2", output_path, **node_data_with_mode)

        # Generate sequence diagram Mermaid syntax and data
        mermaid_syntax = renderer.render_template("visualization/data/sequence_diagram_mermaid.jinja2", **data)
        sequence_data = {
            "mode": mode_key,
            "mermaid_syntax": mermaid_syntax
        }
        output_path = os.path.join(web_data_dir, f"{mode_key}_sequence_diagram.js")
        renderer.render_template_to_file("visualization/data/sequence_diagram_data.js.jinja2", output_path, **sequence_data)

        # Generate logic diagram data
        logic_data = {**data, "mode": mode_key}
        output_path = os.path.join(web_data_dir, f"{mode_key}_logic_diagram.js")
        renderer.render_template_to_file("visualization/data/logic_diagram_data.js.jinja2", output_path, **logic_data)

        logger.info(f"Generated visualization for mode: {mode_key}")

    # Generate web visualization files
    if deploy_data:
        web_dir = os.path.join(visualization_dir, "web")
        modes = list(deploy_data.keys())
        default_mode = "default" if "default" in modes else modes[0]

        # Generate module JS files for overview page
        module_data = {
            "modes": modes,
            "default_mode": default_mode
        }
        
        # Copy static node_diagram.js
        node_diagram_src = _get_static_file_path("visualization/js/node_diagram.js")
        if node_diagram_src:
            output_path = os.path.join(web_dir, "node_diagram.js")
            shutil.copy2(node_diagram_src, output_path)
            logger.info("Copied node diagram module: node_diagram.js")
        else:
            logger.error("Failed to find node_diagram.js static file")

        # Copy static sequence_diagram.js
        sequence_diagram_src = _get_static_file_path("visualization/js/sequence_diagram.js")
        if sequence_diagram_src:
            output_path = os.path.join(web_dir, "sequence_diagram.js")
            shutil.copy2(sequence_diagram_src, output_path)
            logger.info("Copied sequence diagram module: sequence_diagram.js")
        else:
            logger.error("Failed to find sequence_diagram.js static file")

        # Copy static logic_diagram.js
        logic_diagram_src = _get_static_file_path("visualization/js/logic_diagram.js")
        if logic_diagram_src:
            output_path = os.path.join(web_dir, "logic_diagram.js")
            shutil.copy2(logic_diagram_src, output_path)
            logger.info("Copied logic diagram module: logic_diagram.js")
        else:
            logger.error("Failed to find logic_diagram.js static file")

        # Generate overview HTML file
        # Calculate relative path to systems index
        install_root = get_install_root(Path(web_dir))
        systems_index_rel_path = "../../../../../../../systems.html" # fallback default
        if install_root:
            try:
                # Calculate path from web directory (where html file is) to install root
                rel_to_root = os.path.relpath(install_root, web_dir)
                systems_index_rel_path = os.path.join(rel_to_root, "systems.html")
            except ValueError:
                logger.warning(f"Could not calculate relative path from {web_dir} to {install_root}")

        overview_data = {
            "deployment_name": name,
            "package_name": name,  # Using name as package name for now
            "available_modes": modes,
            "available_diagram_types": ["node_diagram", "sequence_diagram", "logic_diagram"],
            "default_mode": default_mode,
            "default_diagram_type": "node_diagram",
            "systems_index_path": systems_index_rel_path
        }
        # Render config.js
        config_output_path = os.path.join(web_dir, "config.js")
        renderer.render_template_to_file("visualization/data/deployment_config.js.jinja2", config_output_path, **overview_data)
        logger.info(f"Generated deployment config: config.js")

        # Copy static overview HTML
        overview_html_src = _get_static_file_path("visualization/deployment_overview.html")
        if overview_html_src:
            output_path = os.path.join(web_dir, f"{name}_overview.html")
            shutil.copy2(overview_html_src, output_path)
            logger.info(f"Generated deployment overview: {name}_overview.html")
        else:
            logger.error("Failed to find deployment_overview.html static file")
