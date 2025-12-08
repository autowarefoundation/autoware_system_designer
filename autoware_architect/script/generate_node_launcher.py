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
import re
import sys

import yaml
import logging

from autoware_architect.utils import pascal_to_snake
from autoware_architect.utils.template_utils import TemplateRenderer


def create_node_launcher_xml(node_yaml) -> str:
    """
    Generate XML launcher content using Jinja2 template.
    
    Args:
        node_yaml: Dictionary containing node configuration
        
    Returns:
        Generated XML content as string
    """
    # Extract necessary information from the node YAML
    launch_config = node_yaml.get("launch")
    package_name = launch_config.get("package")
    plugin_name = launch_config.get("plugin")
    executable_name = launch_config.get("executable")
    node_output = launch_config.get("node_output", "screen")
    use_container = launch_config.get("use_container", False)
    
    if use_container and not launch_config.get("container_name"):
        raise ValueError("Container name is required when use_container is True")
    container_name = launch_config.get("container_name")

    # Extract interface information
    input_list = node_yaml.get("inputs", [])
    output_list = node_yaml.get("outputs", [])

    # Extract parameter set information
    param_path_list = node_yaml.get("parameter_files", [])

    # Extract parameter information
    parameter_list = node_yaml.get("parameters", [])

    # node name is snake case of the node name which the original is in pascal case
    # e.g. ObjectDetector.node -> object_detector
    node_name = node_yaml.get("name").split(".")[0]
    node_name = pascal_to_snake(node_name)

    # Prepare template data
    template_data = {
        'node_name': node_name,
        'package_name': package_name,
        'plugin_name': plugin_name,
        'executable_name': executable_name,
        'node_output': node_output,
        'use_container': use_container,
        'container_name': container_name,
        'inputs': input_list,
        'outputs': output_list,
        'parameter_files': [
            {
                'name': param_file.get('name'),
                'default': _process_parameter_path(param_file.get('default'), package_name),
                'allow_substs': str(param_file.get('allow_substs', False)).lower()
            }
            for param_file in param_path_list
        ],
        'parameters': [
            {
                'name': param.get('name'),
                'default_value': (
                    str(param.get('default')).lower()
                    if param.get('type') == 'bool'
                    else param.get('default')
                )
            }
            for param in parameter_list
        ]
    }

    # Initialize template renderer
    renderer = TemplateRenderer()
    
    # Render the template
    launcher_xml = renderer.render_template('node_launcher.xml.jinja2', **template_data)
    
    return launcher_xml


def generate_launcher(node_yaml_dir, launch_file_dir) -> None:

    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # parse the architecture yaml configuration
    with open(node_yaml_dir, "r") as stream:
        try:
            node_yaml = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            logger.error(f"Error parsing YAML file {node_yaml_dir}: {exc}")
            return
    if "name" not in node_yaml:
        logger.error(f"Field 'name' is required in node configuration., {node_yaml_dir}")
        return
    
    # Check if launch configuration exists and has required fields
    if "launch" not in node_yaml:
        logger.error(f"Field 'launch' is required in node configuration., {node_yaml_dir}")
        return
    
    launch_config = node_yaml.get("launch")
    if "executable" not in launch_config:
        logger.error(f"Field 'executable' is required in launch configuration., {node_yaml_dir}")
        return
    
    node_name = node_yaml.get("name")
    node_name = node_name.split(".")[0]
    node_name = pascal_to_snake(node_name)

    logger.info(f"Generating launcher for node: {node_name}")

    # generate xml launcher file
    launcher_xml = create_node_launcher_xml(node_yaml)

    # generate the launch file
    launch_file = f"{node_name}.launch.xml"
    launch_file_path = os.path.join(launch_file_dir, launch_file)

    logger.info(f"Saving launcher to: {launch_file_path}")

    # if the directory does not exist, create the directory
    os.makedirs(os.path.dirname(launch_file_path), exist_ok=True)

    # if file exists, remove the file
    if os.path.exists(launch_file_path):
        os.remove(launch_file_path)

    # save the launch file to the launch file directory
    with open(launch_file_path, "w") as f:
        # save empty file at this moment
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


if __name__ == "__main__":
    generate_launcher(sys.argv[1], sys.argv[2])
