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

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..exceptions import ValidationError
from ..file_io.template_renderer import TemplateRenderer
from ..models.config import ConfigType, NodeConfig
from ..models.parsing.data_parser import ConfigParser
from ..utils import pascal_to_snake


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
        if "name" in parameters:
            return [dict(parameters)]

        # Mapping form: {param_name: value}
        result: List[Dict[str, Any]] = []
        for name, value in parameters.items():
            result.append({"name": str(name), "default": value})
        return result

    return []


def _process_parameter_path(path: Any, package_name: Optional[str]) -> Any:
    """Process parameter path and add package prefix for relative paths."""

    if (
        isinstance(path, str)
        and package_name
        and not path.startswith("/")
        and not path.startswith("$(")
        and ("/" in path or path.endswith((".yaml", ".json", ".pcd", ".onnx", ".xml")))
    ):
        return f"$(find-pkg-share {package_name})/{path}"
    return path


def create_node_launcher_xml(node_config: NodeConfig) -> str:
    """Generate a single-node launch XML (as a string) from a NodeConfig."""

    template_data: Dict[str, Any] = {}
    template_data["node_name"] = pascal_to_snake(node_config.name)

    launch_config = node_config.launch or {}

    package_name = node_config.package_name
    template_data["package_name"] = package_name
    template_data["ros2_launch_file"] = launch_config.get("ros2_launch_file", None)
    template_data["is_ros2_file_launch"] = template_data["ros2_launch_file"] is not None
    template_data["node_output"] = launch_config.get("node_output", "screen")

    if not template_data["is_ros2_file_launch"]:
        template_data["plugin_name"] = launch_config.get("plugin")
        template_data["executable_name"] = launch_config.get("executable")
        template_data["use_container"] = launch_config.get("use_container", False)
        template_data["container_name"] = launch_config.get("container_name")

    template_data["inputs"] = node_config.inputs or []
    template_data["outputs"] = node_config.outputs or []

    param_path_list = _normalize_parameter_files(node_config.parameter_files)
    template_data["parameter_files"] = [
        {
            "name": param_file.get("name"),
            "default": _process_parameter_path(param_file.get("default"), package_name),
            "allow_substs": str(param_file.get("allow_substs", False)).lower(),
        }
        for param_file in param_path_list
    ]

    parameter_list = _normalize_parameters(node_config.parameters)
    template_data["parameters"] = [
        {
            "name": param.get("name"),
            "default_value": (
                str(param.get("default")).lower()
                if param.get("type") == "bool" or isinstance(param.get("default"), bool)
                else param.get("default")
            ),
        }
        for param in parameter_list
    ]

    renderer = TemplateRenderer()
    return renderer.render_template("node_launcher.xml.jinja2", **template_data)


def generate_node_launcher(
    node_yaml_path: str,
    output_dir: str,
    *,
    strict_mode: bool = True,
) -> str:
    """Generate a ROS 2 launch XML file for a single node YAML config.

    Returns the generated launch file path.
    """

    parser = ConfigParser(strict_mode=strict_mode)
    config = parser.parse_entity_file(node_yaml_path)

    if config.entity_type != ConfigType.NODE or not isinstance(config, NodeConfig):
        raise ValidationError(f"Expected a node config file, got '{config.entity_type}': {node_yaml_path}")

    node_name = pascal_to_snake(config.name)
    launcher_xml = create_node_launcher_xml(config)

    launch_file_path = os.path.join(output_dir, f"{node_name}.launch.xml")
    os.makedirs(os.path.dirname(launch_file_path), exist_ok=True)

    with open(launch_file_path, "w") as f:
        f.write(launcher_xml)

    return launch_file_path
