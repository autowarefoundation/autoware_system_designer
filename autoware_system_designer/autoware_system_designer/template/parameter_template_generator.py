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
from typing import TYPE_CHECKING, Any, Dict, List

from ..builder.runtime.parameters import ParameterType
from ..file_io.source_location import SourceLocation, format_source

if TYPE_CHECKING:
    from ..builder.instances.instances import Instance

logger = logging.getLogger(__name__)


class ParameterTemplateGenerator:
    """Generates parameter set templates for deployment instances.

    This class handles:
    1. Collecting node parameter data from deployment instance tree
    2. Creating namespace-based directory structures
    3. Copying and organizing parameter configuration files
    4. Generating parameter set template files from templates
    """

    def __init__(self, root_instance: "Instance"):
        """Initialize the parameter template generator.

        Args:
            root_instance: The root deployment instance to generate templates for
        """
        self.root_instance = root_instance

    def generate_parameter_set_template(
        self, deployment_name: str, template_renderer, output_dir: str
    ) -> List[str]:
        """Generate per-component parameter set templates.

        Instead of one aggregated template, create a separate parameter_set
        template (and directory structure) for each top-level component under
        the deployment root instance.

        Returns:
            List of generated template file paths (one per component). If the
            deployment has no children, a single template is generated for the
            whole deployment.
        """

        component_nodes: Dict[str, List[Dict[str, Any]]] = {}
        for comp_name, comp_instance in self.root_instance.children.items():
            nodes: List[Dict[str, Any]] = []
            self._collect_node_parameter_files_recursive(comp_instance, nodes, "")
            component_nodes[comp_name] = nodes

        system_root = os.path.join(output_dir, f"{deployment_name}.parameter_set")
        os.makedirs(system_root, exist_ok=True)

        for nodes in component_nodes.values():
            for node in nodes:
                self._create_namespace_structure_and_copy_configs(node, system_root)

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
            logger.info(
                f"Generated component parameter set template: {output_path} (shared root: {system_root})"
            )
            generated.append(output_path)
        return generated

    @classmethod
    def generate_parameter_set_template_from_data(
        cls,
        root_data: Dict[str, Any],
        deployment_name: str,
        template_renderer,
        output_dir: str,
    ) -> List[str]:
        """Generate parameter set templates from serialized system structure data."""
        generator = cls.__new__(cls)
        return generator._generate_parameter_set_template_from_data(
            root_data, deployment_name, template_renderer, output_dir
        )

    def collect_node_parameter_files_for_template(self, base_namespace: str = "") -> List[Dict[str, Any]]:
        """Collect parameter template data for all nodes in the deployment instance."""
        node_data: List[Dict[str, Any]] = []
        self._collect_node_parameter_files_recursive(self.root_instance, node_data, base_namespace)
        return node_data

    def _collect_node_parameter_files_recursive(
        self,
        instance: "Instance",
        node_data: List[Dict[str, Any]],
        current_namespace: str = "",
    ) -> None:
        if instance.entity_type == "node":
            full_namespace = instance.namespace_str

            parameter_files_list, parameters = self._extract_parameters_from_manager(instance)
            parameter_files = {pf["name"]: pf["path"] for pf in parameter_files_list}

            if parameter_files or parameters:
                node_info = {
                    "node": full_namespace,
                    "parameter_files": parameter_files,
                    "parameters": parameters,
                    "package": instance.configuration.launch.get("package", "unknown_package"),
                }
                node_data.append(node_info)

        if hasattr(instance, "children") and instance.children:
            for child in instance.children.values():
                self._collect_node_parameter_files_recursive(child, node_data, current_namespace)

    def _generate_parameter_set_template_from_data(
        self,
        root_data: Dict[str, Any],
        deployment_name: str,
        template_renderer,
        output_dir: str,
    ) -> List[str]:
        component_nodes: Dict[str, List[Dict[str, Any]]] = {}
        for comp in root_data.get("children", []):
            comp_name = comp.get("name", "unknown_component")
            nodes: List[Dict[str, Any]] = []
            self._collect_node_parameter_files_recursive_data(comp, nodes)
            component_nodes[comp_name] = nodes

        system_root = os.path.join(output_dir, f"{deployment_name}.parameter_set")
        os.makedirs(system_root, exist_ok=True)

        for nodes in component_nodes.values():
            for node in nodes:
                self._create_namespace_structure_and_copy_configs(node, system_root)

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
            logger.info(
                f"Generated component parameter set template: {output_path} (shared root: {system_root})"
            )
            generated.append(output_path)
        return generated

    def _collect_node_parameter_files_recursive_data(
        self, instance_data: Dict[str, Any], node_data: List[Dict[str, Any]]
    ) -> None:
        if instance_data.get("entity_type") == "node":
            namespace_str = instance_data.get("namespace_str")
            if not namespace_str:
                namespace = instance_data.get("namespace", [])
                namespace_str = "/" + "/".join(namespace) if namespace else "/"

            parameter_files_list, parameters = self._extract_parameters_from_data(instance_data, namespace_str)
            parameter_files = {pf["name"]: pf["path"] for pf in parameter_files_list}

            if parameter_files or parameters:
                node_info = {
                    "node": namespace_str,
                    "parameter_files": parameter_files,
                    "parameters": parameters,
                    "package": instance_data.get("launcher", {}).get("package", "unknown_package"),
                }
                node_data.append(node_info)

        for child in instance_data.get("children", []):
            self._collect_node_parameter_files_recursive_data(child, node_data)

    def _extract_parameters_from_data(
        self, instance_data: Dict[str, Any], namespace_str: str
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        parameter_files: List[Dict[str, Any]] = []
        parameters: List[Dict[str, Any]] = []

        base_path = namespace_str
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

    def _extract_parameters_from_manager(
        self, node_instance: "Instance"
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        parameter_files: List[Dict[str, Any]] = []
        parameters: List[Dict[str, Any]] = []

        all_parameter_files = node_instance.parameter_manager.get_all_param_files()
        all_parameters = node_instance.parameter_manager.get_all_parameters()

        base_path = node_instance.namespace_str

        for param_file in all_parameter_files:
            param_name = param_file.name
            template_path = f"{base_path}/{param_name}.param.yaml"
            priority = 2 if param_file.is_override else 1
            parameter_files.append({"name": param_name, "path": template_path, "priority": priority})

        for param in all_parameters:
            if param.parameter_type in [ParameterType.DEFAULT_FILE, ParameterType.OVERRIDE_FILE]:
                continue

            if param.parameter_type == ParameterType.GLOBAL:
                continue

            configuration = {
                "name": param.name,
                "type": param.data_type,
                "value": param.value,
                "priority": param.parameter_type.value,
            }
            parameters.append(configuration)

        parameter_files.sort(key=lambda pf: pf["priority"])
        parameters.sort(key=lambda p: p["priority"])

        return parameter_files, parameters

    def _create_namespace_structure_and_copy_configs(
        self, node_data: Dict[str, Any], parameter_set_root: str
    ) -> None:
        node_path = node_data["node"]
        parameter_files = node_data["parameter_files"]

        namespace_dir = os.path.join(parameter_set_root, node_path.lstrip("/"))
        os.makedirs(namespace_dir, exist_ok=True)

        updated_parameter_files: Dict[str, str] = {}
        for param_name, original_path in parameter_files.items():
            dest_filename = f"{param_name}.param.yaml"
            dest_path = os.path.join(namespace_dir, dest_filename)

            self._create_empty_config_file(dest_path, param_name)

            relative_path = os.path.relpath(dest_path, parameter_set_root)
            variable_path = "$(var config_path)" + "/" + relative_path.replace("\\", "/")
            updated_parameter_files[param_name] = variable_path

        node_data["parameter_files"] = updated_parameter_files

    def _create_empty_config_file(self, dest_path: str, param_name: str) -> None:
        try:
            empty_config_content = (
                "/**:\n"
                "  ros__parameters:\n"
                "    []\n"
                f"    # Add parameters for {param_name}\n"
            )

            with open(dest_path, "w") as f:
                f.write(empty_config_content)

            logger.info(f"Created empty config file: {dest_path}")

        except Exception as e:
            src = SourceLocation(file_path=Path(dest_path))
            logger.error(f"Failed to create empty config file {dest_path}: {e}{format_source(src)}")
