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

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_DIR.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


from autoware_system_designer.exceptions import ValidationError  # noqa: E402
from autoware_system_designer.file_io.template_renderer import TemplateRenderer  # noqa: E402
from autoware_system_designer.models.config import ConfigType, NodeConfig  # noqa: E402
from autoware_system_designer.models.parsing.data_parser import ConfigParser  # noqa: E402
from autoware_system_designer.utils import pascal_to_snake  # noqa: E402
from autoware_system_designer.utils.logging_utils import configure_split_stream_logging  # noqa: E402


def _normalize_parameter_files(parameter_files: Any) -> List[Dict[str, Any]]:
    if not parameter_files:
        return []

    if isinstance(parameter_files, list):
        return [dict(item) for item in parameter_files if isinstance(item, dict)]

    if isinstance(parameter_files, dict):
        # Single object form: {name: ..., default/path: ..., ...}
        if "name" in parameter_files:
            return [dict(parameter_files)]

        # Mapping form: {param_file_name: path}
        result: List[Dict[str, Any]] = []
        for name, path in parameter_files.items():
            result.append({"name": str(name), "default": path})
        return result

    return []


def _normalize_parameters(parameters: Any) -> List[Dict[str, Any]]:
    if not parameters:
        return []

    if isinstance(parameters, list):
        return [dict(item) for item in parameters if isinstance(item, dict)]

    if isinstance(parameters, dict):
        # Single object form
        if "name" in parameters:
            return [dict(parameters)]

        # Mapping form: {param_name: value}
        result: List[Dict[str, Any]] = []
        for name, value in parameters.items():
            result.append({"name": str(name), "default": value})
        return result

    return []


def create_node_launcher_xml(node_config: NodeConfig) -> str:
    """
    Generate XML launcher content using Jinja2 template.
    
    Args:
        node_yaml: Dictionary containing node configuration
        
    Returns:
        Generated XML content as string
    """
    template_data: Dict[str, Any] = {}
    template_data["node_name"] = pascal_to_snake(node_config.name)

    launch_config = node_config.launch or {}

    # Launch configuration
    package_name = launch_config.get("package")
    template_data["package_name"] = package_name
    template_data["ros2_launch_file"] = launch_config.get("ros2_launch_file", None) # command execution mode
    is_ros2_file_launch = True if template_data["ros2_launch_file"] is not None else False
    template_data["is_ros2_file_launch"] = is_ros2_file_launch
    template_data["node_output"] = launch_config.get("node_output", "screen")

    if is_ros2_file_launch is False:
        template_data["plugin_name"] = launch_config.get("plugin")
        template_data["executable_name"] = launch_config.get("executable")
        template_data["node_output"] = launch_config.get("node_output", "screen")
        template_data["use_container"] = launch_config.get("use_container", False)

        template_data["container_name"] = launch_config.get("container_name")

    # Extract interface information
    template_data["inputs"] = node_config.inputs or []
    template_data["outputs"] = node_config.outputs or []

    # Extract parameter set information
    param_path_list = _normalize_parameter_files(node_config.parameter_files)
    template_data["parameter_files"] = [
        {
            'name': param_file.get('name'),
            'default': _process_parameter_path(param_file.get('default'), package_name),
            'allow_substs': str(param_file.get('allow_substs', False)).lower()
        }
        for param_file in param_path_list
    ]
    parameter_list = _normalize_parameters(node_config.parameters)
    template_data["parameters"] = [
        {
            'name': param.get('name'),
            'default_value': (
                str(param.get('default')).lower()
                if param.get('type') == 'bool' or isinstance(param.get('default'), bool)
                else param.get('default')
            )
        }
        for param in parameter_list
    ]

    # Initialize template renderer
    renderer = TemplateRenderer()
    
    # Render the template
    launcher_xml = renderer.render_template('node_launcher.xml.jinja2', **template_data)
    
    return launcher_xml


def generate_launcher(node_yaml_path: str, launch_file_dir: str) -> None:
    configure_split_stream_logging(
        level=logging.INFO,
        formatter=logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'),
    )
    logger = logging.getLogger(__name__)

    try:
        parser = ConfigParser(strict_mode=True)
        config = parser.parse_entity_file(node_yaml_path)
    except ValidationError as exc:
        logger.error(f"Invalid node config: {exc}")
        return

    if config.entity_type != ConfigType.NODE or not isinstance(config, NodeConfig):
        logger.error(f"Expected a node config file, got '{config.entity_type}': {node_yaml_path}")
        return

    node_name = pascal_to_snake(config.name)
    logger.info(f"Generating launcher for node: {node_name}")

    # generate xml launcher file
    launcher_xml = create_node_launcher_xml(config)

    # generate the launch file
    launch_file = f"{node_name}.launch.xml"
    launch_file_path = os.path.join(launch_file_dir, launch_file)

    logger.info(f"Saving launcher to: {launch_file_path}")

    os.makedirs(os.path.dirname(launch_file_path), exist_ok=True)

    # save the launch file to the launch file directory
    with open(launch_file_path, "w") as f:
        f.write(launcher_xml)


def _process_parameter_path(path, package_name):
    """
    Process parameter path and add package prefix for relative paths.
    
    Args:
        path: Parameter path from node YAML
        package_name: Package name to prefix relative paths with
    
    Returns:
        Processed path with package prefix if it was relative
    """
    if (isinstance(path, str) and 
        package_name and 
        not path.startswith('/') and 
        not path.startswith('$(') and
        ('/' in path or path.endswith(('.yaml', '.json', '.pcd', '.onnx', '.xml')))):
        return f"$(find-pkg-share {package_name})/{path}"
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a ROS 2 launch XML for a single node config YAML")
    parser.add_argument("node_yaml", help="Path to '<Name>.node.yaml'")
    parser.add_argument("output_dir", help="Directory to write '<name>.launch.xml'")

    args = parser.parse_args(argv)
    generate_launcher(args.node_yaml, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
