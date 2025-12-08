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

import logging
from typing import TYPE_CHECKING, List, Dict, Any, Optional
import os
import shutil

from ..models.parameters import ParameterType

if TYPE_CHECKING:
    from .instances import Instance

logger = logging.getLogger(__name__)


class ParameterTemplateGenerator:
    """Generates parameter set templates for deployment instances.
    
    This class handles:
    1. Collecting node parameter data from deployment instance tree
    2. Creating namespace-based directory structures
    3. Copying and organizing parameter configuration files
    4. Generating parameter set template files from templates
    """
    
    def __init__(self, root_instance: 'Instance'):
        """Initialize the parameter template generator.
        
        Args:
            root_instance: The root deployment instance to generate templates for
        """
        self.root_instance = root_instance
    
    # =========================================================================
    # Public API Methods
    # =========================================================================
    
    def generate_parameter_set_template(self, deployment_name: str,
                                        template_renderer, output_dir: str) -> List[str]:
        """Generate per-component parameter set templates.

        Instead of one aggregated template, create a separate parameter_set
        template (and directory structure) for each top-level component under
        the deployment root instance.

        Returns:
            List of generated template file paths (one per component). If the
            deployment has no children, a single template is generated for the
            whole deployment.
        """

        # Collect all component node data first (no file creation yet)
        component_nodes: Dict[str, List[Dict[str, Any]]] = {}
        for comp_name, comp_instance in self.root_instance.children.items():
            nodes: List[Dict[str, Any]] = []
            self._collect_node_parameter_files_recursive(comp_instance, nodes, "")
            component_nodes[comp_name] = nodes

        # Single root directory for the whole system
        system_root = os.path.join(output_dir, f"{deployment_name}.parameter_set")
        os.makedirs(system_root, exist_ok=True)

        # Create namespace structure and empty config files once (system-wide)
        for nodes in component_nodes.values():
            for node in nodes:
                self._create_namespace_structure_and_copy_configs(node, system_root)

        # Generate per-component templates referencing shared system_root paths
        generated: List[str] = []
        for comp_name, nodes in component_nodes.items():
            output_path = os.path.join(output_dir, f"{deployment_name}.{comp_name}.parameter_set.yaml")
            template_renderer.render_template_to_file(
                "parameter_set.yaml.jinja2",
                output_path,
                name=f"{deployment_name}.{comp_name}.parameter_set",
                parameters=nodes,
            )
            logger.info(f"Generated component parameter set template: {output_path} (shared root: {system_root})")
            generated.append(output_path)
        return generated
    
    def collect_node_parameter_files_for_template(self, base_namespace: str = "") -> List[Dict[str, Any]]:
        """Collect parameter template data for all nodes in the deployment instance.
        
        Args:
            base_namespace: Base namespace to prepend to node namespaces
            
        Returns:
            List of dictionaries containing node parameter template data
        """
        node_data = []
        self._collect_node_parameter_files_recursive(self.root_instance, node_data, base_namespace)
        return node_data
    
    # =========================================================================
    # Helper Methods for Template Generation
    # =========================================================================
    
    def _collect_node_parameter_files_recursive(self, instance: 'Instance', 
                                           node_data: List[Dict[str, Any]], 
                                           current_namespace: str = "") -> None:
        """Recursively collect node parameter_files with their full namespaces.
        
        Args:
            instance: Current instance to process
            node_data: List to append node parameter data to
            current_namespace: Current namespace path
        """
        if instance.entity_type == "node":
            # Use the instance's namespace_str directly
            full_namespace = instance.namespace_str
            
            # Get parameter information from parameter_manager
            parameter_files_list, parameters = self._extract_parameters_from_manager(instance)

            # Convert parameter files list back to dict for template compatibility
            parameter_files = {pf["name"]: pf["path"] for pf in parameter_files_list}

            if parameter_files or parameters:
                node_info = {
                    "node": full_namespace,
                    "parameter_files": parameter_files,
                    "parameters": parameters,
                    "package": instance.configuration.launch.get("package", "unknown_package")
                }
                node_data.append(node_info)
        
        # Recursively process children
        if hasattr(instance, 'children') and instance.children:
            for child in instance.children.values():
                self._collect_node_parameter_files_recursive(child, node_data, current_namespace)
    
    def _extract_parameters_from_manager(self, node_instance: 'Instance') -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract parameter files and parameters from parameter manager.

        Args:
            node_instance: Node instance to extract parameters from

        Returns:
            Tuple of (parameter_files_list, parameters_list) - both sorted by priority
        """
        parameter_files = []
        parameters = []

        # Get parameter files from the parameter manager
        all_parameter_files = node_instance.parameter_manager.get_all_parameter_files()
        # Get parameters from the parameter manager
        all_parameters = node_instance.parameter_manager.get_all_parameters()

        # Use the instance's namespace_str directly for parameter file paths
        base_path = node_instance.namespace_str

        for param_file in all_parameter_files:
            param_name = param_file.name
            # Generate template path based on namespace and parameter name
            template_path = f"{base_path}/{param_name}.param.yaml"
            # Override parameter files have higher priority than default ones
            priority = 2 if param_file.is_override else 1
            parameter_files.append({
                "name": param_name,
                "path": template_path,
                "priority": priority
            })

        for param in all_parameters:
            # Skip parameters that are loaded from parameter files
            # (they are already included in the parameter_files section)
            if param.parameter_type in [ParameterType.DEFAULT_FILE, ParameterType.OVERRIDE_FILE]:
                continue

            configuration = {
                "name": param.name,
                "type": param.data_type,
                "value": param.value,
                "priority": param.parameter_type.value
            }
            parameters.append(configuration)

        # Sort parameter files and parameters by priority (higher priority comes later)
        parameter_files.sort(key=lambda pf: pf["priority"])
        parameters.sort(key=lambda p: p["priority"])

        return parameter_files, parameters
    
    def _create_namespace_structure_and_copy_configs(self, node_data: Dict[str, Any], 
                                                   parameter_set_root: str) -> None:
        """Create namespace folder structure and copy config files.
        
        Args:
            node_data: Node data containing node path and parameter_files
            parameter_set_root: Root directory for parameter set
        """
        node_path = node_data["node"]
        parameter_files = node_data["parameter_files"]
        
        # Create namespace directory structure
        # node_path is like "/perception/object_recognition/detection/centerpoint"
        namespace_dir = os.path.join(parameter_set_root, node_path.lstrip('/'))
        os.makedirs(namespace_dir, exist_ok=True)
        
        # Copy parameter files and update paths in node_data
        updated_parameter_files = {}
        for param_name, original_path in parameter_files.items():
            # Copy config file to namespace directory
            dest_filename = f"{param_name}.param.yaml"
            dest_path = os.path.join(namespace_dir, dest_filename)

            # Create an empty config file at the destination
            self._create_empty_config_file(dest_path, param_name)

            # Update path to be relative to parameter set root
            relative_path = os.path.relpath(dest_path, parameter_set_root)
            variable_path = "$(var config_path)" + "/" + relative_path.replace("\\", "/")
            updated_parameter_files[param_name] = variable_path
        
        # Update node data with new paths
        node_data["parameter_files"] = updated_parameter_files
    
    def _create_empty_config_file(self, dest_path: str, param_name: str) -> None:
        """Create an empty config file with basic ROS parameter structure.
        
        Args:
            dest_path: Destination path for the config file
            param_name: Parameter name for logging
        """
        try:
            # Create basic ROS parameter file structure
            empty_config_content = """/**:
  ros__parameters:
    []
    # Add parameters for {}
""".format(param_name)
            
            with open(dest_path, 'w') as f:
                f.write(empty_config_content)
            
            logger.info(f"Created empty config file: {dest_path}")
            
        except Exception as e:
            logger.error(f"Failed to create empty config file {dest_path}: {e}")
