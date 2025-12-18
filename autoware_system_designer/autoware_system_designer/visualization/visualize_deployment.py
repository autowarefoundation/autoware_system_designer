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
from typing import Dict
from ..utils.template_utils import TemplateRenderer

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
        output_path = os.path.join(web_dir, "node_diagram.js")
        renderer.render_template_to_file("visualization/page/node_diagram.js.jinja2", output_path, **module_data)
        logger.info("Generated node diagram module: node_diagram.js")

        output_path = os.path.join(web_dir, "sequence_diagram.js")
        renderer.render_template_to_file("visualization/page/sequence_diagram.js.jinja2", output_path, **module_data)
        logger.info("Generated sequence diagram module: sequence_diagram.js")

        output_path = os.path.join(web_dir, "logic_diagram.js")
        renderer.render_template_to_file("visualization/page/logic_diagram.js.jinja2", output_path, **module_data)
        logger.info("Generated logic diagram module: logic_diagram.js")

        # Generate overview HTML file
        overview_data = {
            "deployment_name": name,
            "package_name": name,  # Using name as package name for now
            "available_modes": modes,
            "available_diagram_types": ["node_diagram", "sequence_diagram", "logic_diagram"],
            "default_mode": default_mode,
            "default_diagram_type": "node_diagram"
        }
        output_path = os.path.join(web_dir, f"{name}_overview.html")
        renderer.render_template_to_file("visualization/page/deployment_overview.html.jinja2", output_path, **overview_data)
        logger.info(f"Generated deployment overview: {name}_overview.html")
