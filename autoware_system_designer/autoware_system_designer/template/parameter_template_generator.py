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

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from ..building.runtime.parameters import ParameterType
from ..file_io.source_location import SourceLocation, format_source

logger = logging.getLogger(__name__)


class ParameterTemplateGenerator:
    """Generates parameter set templates for deployment instances.

    This class handles:
    1. Collecting node parameter data from serialized deployment data
    2. Creating namespace-based directory structures
    3. Copying and organizing parameter configuration files
    4. Generating parameter set template files from templates
    """

    @classmethod
    def generate_parameter_set_template_from_data(
        cls,
        root_data: Dict[str, Any],
        deployment_name: str,
        template_renderer,
        output_dir: str,
    ) -> List[str]:
        """Generate parameter set templates from serialized system structure data."""
        return cls._generate_parameter_set_template_from_data(root_data, deployment_name, template_renderer, output_dir)

    @classmethod
    def _generate_parameter_set_template_from_data(
        cls,
        root_data: Dict[str, Any],
        deployment_name: str,
        template_renderer,
        output_dir: str,
    ) -> List[str]:
        component_nodes: Dict[str, List[Dict[str, Any]]] = {}
        for comp in root_data.get("children", []):
            comp_name = comp.get("name", "unknown_component")
            nodes: List[Dict[str, Any]] = []
            cls._collect_node_parameter_files_recursive_data(comp, nodes)
            component_nodes[comp_name] = nodes

        system_root = os.path.join(output_dir, f"{deployment_name}.parameter_set")
        os.makedirs(system_root, exist_ok=True)

        for nodes in component_nodes.values():
            for node in nodes:
                cls._create_namespace_structure_and_copy_configs(node, system_root)

        generated: List[str] = []
        for comp_name, nodes in component_nodes.items():
            output_file_name = f"{deployment_name}__{comp_name}.parameter_set"
            output_path = os.path.join(output_dir, f"{output_file_name}.yaml")
            template_renderer.render_template_to_file(
                "parameter_set.yaml.jinja2",
                output_path,
                name=output_file_name,
                parameters=nodes,
            )
            logger.info(f"Generated component parameter set template: {output_path} (shared root: {system_root})")
            generated.append(output_path)
        return generated

    @classmethod
    def _collect_node_parameter_files_recursive_data(
        cls, instance_data: Dict[str, Any], node_data: List[Dict[str, Any]]
    ) -> None:
        if instance_data.get("entity_type") == "node":
            node_path = instance_data.get("path")

            parameter_files_list, parameters = cls._extract_parameters_from_data(instance_data, node_path)
            parameter_files = {pf["name"]: pf["path"] for pf in parameter_files_list}

            if parameter_files or parameters:
                node_info = {
                    "node": node_path,
                    "parameter_files": parameter_files,
                    "parameters": parameters,
                    "package": instance_data.get("launcher", {}).get("package", "unknown_package"),
                }
                node_data.append(node_info)

        for child in instance_data.get("children", []):
            cls._collect_node_parameter_files_recursive_data(child, node_data)

    @staticmethod
    def _extract_parameters_from_data(
        instance_data: Dict[str, Any], node_path: str
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        parameter_files: List[Dict[str, Any]] = []
        parameters: List[Dict[str, Any]] = []

        base_path = node_path
        for param_file in instance_data.get("parameter_files_all", []):
            param_name = param_file.get("name")
            if not param_name:
                continue
            template_path = f"{base_path}/{param_name}.param.yaml"
            priority = 2 if param_file.get("is_override") else 1
            parameter_files.append({"name": param_name, "path": template_path, "priority": priority})

        skip_types = {
            ParameterType.DEFAULT_FILE.name,
            ParameterType.OVERRIDE_FILE.name,
            ParameterType.GLOBAL.name,
        }
        type_priority = {ptype.name: ptype.value for ptype in ParameterType}
        for param in instance_data.get("parameters", []):
            param_type = param.get("parameter_type")
            if param_type in skip_types:
                continue
            configuration = {
                "name": param.get("name"),
                "type": param.get("type"),
                "value": param.get("value"),
                "priority": type_priority.get(param_type, 0),
            }
            parameters.append(configuration)

        parameter_files.sort(key=lambda pf: pf["priority"])
        parameters.sort(key=lambda p: p["priority"])

        return parameter_files, parameters

    @classmethod
    def _create_namespace_structure_and_copy_configs(cls, node_data: Dict[str, Any], parameter_set_root: str) -> None:
        node_path = node_data["node"]
        parameter_files = node_data["parameter_files"]

        namespace_dir = os.path.join(parameter_set_root, node_path.lstrip("/"))
        os.makedirs(namespace_dir, exist_ok=True)

        updated_parameter_files: Dict[str, str] = {}
        for param_name, original_path in parameter_files.items():
            dest_filename = f"{param_name}.param.yaml"
            dest_path = os.path.join(namespace_dir, dest_filename)

            cls._create_empty_config_file(dest_path, param_name)

            relative_path = os.path.relpath(dest_path, parameter_set_root)
            variable_path = "$(var config_path)" + "/" + relative_path.replace("\\", "/")
            updated_parameter_files[param_name] = variable_path

        node_data["parameter_files"] = updated_parameter_files

    @staticmethod
    def _create_empty_config_file(dest_path: str, param_name: str) -> None:
        try:
            empty_config_content = "/**:\n" "  ros__parameters:\n" "    []\n" f"    # Add parameters for {param_name}\n"

            with open(dest_path, "w") as f:
                f.write(empty_config_content)

            logger.info(f"Created empty config file: {dest_path}")

        except Exception as e:
            src = SourceLocation(file_path=Path(dest_path))
            logger.error(f"Failed to create empty config file {dest_path}: {e}{format_source(src)}")
