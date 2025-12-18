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
import re

from ..models.parameters import ParameterList, ParameterFileList, Parameter, ParameterFile, ParameterType
from ..parsers.yaml_parser import yaml_parser

if TYPE_CHECKING:
    from .instances import Instance
    from .config_registry import ConfigRegistry

logger = logging.getLogger(__name__)


class ParameterManager:
    """Manages parameter operations for Instance objects.
    
    This class handles:
    1. Applying parameters from parameter sets to target instances
    2. Initializing node parameters from configuration
    3. Managing parameter values and parameters
    4. Resolving parameter file paths with package prefixes
    """
    
    def __init__(self, instance: 'Instance', parameter_resolver = None):
        self.instance = instance
        self.parameter_resolver = parameter_resolver
        self.parameters: ParameterList = ParameterList()
        self.parameter_files: ParameterFileList = ParameterFileList()

    # =========================================================================
    # Public API Methods
    # =========================================================================

    def get_all_parameters(self):
        """Get all parameters."""
        return self.parameters.list

    def get_all_parameter_files(self):
        """Get all parameter files."""
        return self.parameter_files.list
    
    def get_parameters_for_launch(self) -> List[Dict[str, Any]]:
        """Get all parameters for launcher generation.

        Returns list of parameter dicts:
        - {"type": "param", "name": "...", "value": "...", "parameter_type": ParameterType}

        Global parameters are returned first (applied first in launcher), followed by other parameters.
        Higher priority parameters will override lower priority ones.
        """
        result = []

        # Add regular parameters
        for param in self.parameters.list:
            # Skip parameters that are loaded from files, as they will be passed via parameter files
            if param.parameter_type in [ParameterType.DEFAULT_FILE, ParameterType.OVERRIDE_FILE]:
                continue

            if param.value is not None:
                result.append({
                    "type": "param",
                    "name": param.name,
                    "value": param.value,
                    "parameter_type": param.parameter_type
                })

        return result

    def get_parameter_files_for_launch(self) -> List[Dict[str, Any]]:
        """Get all parameter files for launcher generation.

        Returns list of parameter file dicts:
        - {"type": "param_file", "path": "..."}
        """
        result = []
        for param_file in self.parameter_files.list:
            resolved_path = self._resolve_parameter_file_path(param_file.path, self._get_package_name(), param_file.is_override)
            result.append({
                "type": "param_file",
                "name": param_file.name,
                "path": resolved_path
            })
        return result

    def resolve_all_parameters(self):
        """Resolve any remaining substitutions in all parameters and parameter files."""
        if not self.parameter_resolver:
            return

        # Resolve all parameter values
        for param in self.parameters.list:
            if param.value is not None and isinstance(param.value, str):
                resolved_value = self.parameter_resolver.resolve_string(param.value)
                if resolved_value != param.value:
                    param.value = resolved_value

        # Resolve all parameter file paths
        for param_file in self.parameter_files.list:
            if param_file.path and isinstance(param_file.path, str):
                resolved_path = self.parameter_resolver.resolve_string(param_file.path)
                if resolved_path != param_file.path:
                    param_file.path = resolved_path

    def _get_package_name(self) -> Optional[str]:
        """Get package name from instance configuration."""
        if self.instance.entity_type == "node" and self.instance.configuration:
            launch_config = self.instance.configuration.launch
            return launch_config.get("package")
        return None

    # =========================================================================
    # Parameter Path Resolution
    # =========================================================================
    
    def _resolve_parameter_file_path(self, path: str, package_name: Optional[str] = None,
                                     is_override: bool = False, config_registry: Optional['ConfigRegistry'] = None) -> str:
        """Resolve parameter file path with package prefix if needed.

        Args:
            path: The parameter file path
            package_name: The ROS package name for default parameters
            is_override: True for override parameter files, False for default
            config_registry: Registry to look up package paths

        Returns:
            Resolved path with package prefix if applicable
        """

        if path is None:
            raise ValueError(f"path is None. package_name: {package_name}, node_namespace: {self.instance.namespace_str}, path: {path}")

        # Resolve any substitutions in the path first
        if self.parameter_resolver:
            path = self.parameter_resolver.resolve_string(path)

        # If path is now absolute, return it
        if path.startswith('/'):
            return path

        # Check if this is a default parameter file (not override)
        is_default_file = not is_override

        # If we have config_registry and it's a parameter file, try to resolve to absolute path
        if is_default_file and package_name and config_registry:
            pkg_path = config_registry.get_package_path(package_name)
            if pkg_path:
                resolved_path = os.path.join(pkg_path, path)
                if os.path.exists(resolved_path):
                    return resolved_path

                # Fallback: Check if it's a generated file in install directory
                # This handles cases where config files are generated during build (e.g. from schema)
                # and are not present in the source directory.
                try:
                    # Iterate up to find a directory containing 'src' and 'install' (workspace root)
                    current_dir = pkg_path
                    workspace_root = None
                    while len(current_dir) > 1:
                        if os.path.exists(os.path.join(current_dir, 'src')) and os.path.exists(os.path.join(current_dir, 'install')):
                            workspace_root = current_dir
                            break
                        current_dir = os.path.dirname(current_dir)
                    
                    if workspace_root:
                        # Construct install path: install/<package>/share/<package>/<path>
                        install_path = os.path.join(workspace_root, 'install', package_name, 'share', package_name, path)
                        if os.path.exists(install_path):
                            logger.debug(f"Resolved generated parameter file in install: {install_path}")
                            return install_path
                except Exception as e:
                    logger.debug(f"Failed to resolve fallback path: {e}")

                return resolved_path

        # For parameter files without registry (or fallback), add package prefix for launch file
        if is_default_file and package_name:
            return f"$(find-pkg-share {package_name})/{path}"

        # For overrides or when no package name, return as-is
        return path

    # =========================================================================
    # Parameter Application (from parameter sets)
    # =========================================================================
    
    def apply_node_parameters(self, node_namespace: str, parameter_files: list, parameters: list, config_registry: Optional['ConfigRegistry'] = None):
        """Apply parameters directly to a target node using new parameter set format.
        
        This method finds a node by its absolute namespace and applies both parameter_files 
        and parameters directly to it. Parameters will override parameter_files.
        
        Args:
            node_namespace: Absolute namespace path to the target node 
                           (e.g., "/perception/object_recognition/node_tracker")
            parameter_files: List of parameter file mappings 
                            (e.g., [{"model_param_path": "path/to/file.yaml"}])
            parameters: List of direct parameters
                           (e.g., [{"name": "build_only", "type": "bool", "value": false}])
            config_registry: Registry for resolving paths
        """
        target_instance = self._find_node_by_namespace(node_namespace)
        if target_instance is None:
            logger.warning(f"Target node not found: {node_namespace}")
            return
            
        if target_instance.entity_type != "node":
            logger.warning(f"Target node is not a node: {node_namespace} (type: {target_instance.entity_type})")
            return
        
        logger.info(f"Applying parameters to node: {node_namespace}")
            
        # Apply parameter files first (as overrides, not defaults)
        if parameter_files:
            for param_file_mapping in parameter_files:
                for param_name, param_path in param_file_mapping.items():
                    # Resolve parameter file path if resolver is available
                    if self.parameter_resolver:
                        param_path = self.parameter_resolver.resolve_parameter_file_path(param_path)

                    target_instance.parameter_manager.parameter_files.add_parameter_file(
                        param_name,
                        param_path,
                        allow_substs=True,
                        is_override=True  # Parameter set parameter files are overrides
                    )

                    # Load parameters from this file for visualization
                    target_instance.parameter_manager._load_parameters_from_file(
                        param_path,
                        is_override=True,
                        config_registry=config_registry
                    )
        
        # Apply parameters (these override parameter files)
        if parameters:
            for param in parameters:
                param_name = param.get("name")
                param_type = param.get("type", "string")
                param_value = param.get("value")

                # Resolve parameter value if resolver is available
                if self.parameter_resolver:
                    param_value = self.parameter_resolver.resolve_parameter_value(param_value)

                target_instance.parameter_manager.parameters.set_parameter(
                    param_name,
                    param_value,
                    data_type=param_type,
                    allow_substs=True,
                    parameter_type=ParameterType.OVERRIDE  # Parameter set overrides
                )

    # =========================================================================
    # Node Finding (helper methods for parameter application)
    # =========================================================================
    
    def _find_node_by_namespace(self, target_namespace: str):
        """Find a node instance by its absolute namespace path.
        
        Args:
            target_namespace: Absolute namespace path (e.g., "/perception/object_recognition/node_tracker")
            
        Returns:
            Instance object if found, None otherwise
        """
        # Start from the root deployment instance
        current_instance = self.instance
        
        # Navigate to the root deployment instance
        while current_instance.parent is not None:
            current_instance = current_instance.parent
            
        # If we're at the deployment root (entity_type == "system"),
        # search in its children directly since parameter_set paths don't include the deployment name
        if current_instance.entity_type == "system":
            for child in current_instance.children.values():
                result = self._traverse_to_namespace(child, target_namespace)
                if result is not None:
                    return result
            return None
        
        # Otherwise, traverse down from current instance
        return self._traverse_to_namespace(current_instance, target_namespace)
    
    def _traverse_to_namespace(self, instance, target_namespace: str):
        """Recursively traverse instance tree to find target namespace.
        
        Args:
            instance: Current instance to check
            target_namespace: Target namespace to find
            
        Returns:
            Instance object if found, None otherwise
        """
        # Check if current instance matches the target namespace
        if instance.namespace_str == target_namespace:
            return instance
            
        # Check if target namespace starts with current instance namespace
        # This means we need to look deeper in this branch
        if target_namespace.startswith(instance.namespace_str + "/") or target_namespace == instance.namespace_str:
            # Search in children
            for child in instance.children.values():
                result = self._traverse_to_namespace(child, target_namespace)
                if result is not None:
                    return result
        
        return None

    # =========================================================================
    # Node Parameter Initialization
    # =========================================================================
    
    def initialize_node_parameters(self, config_registry: Optional['ConfigRegistry'] = None):
        """Initialize parameters for node entity during node configuration.
        This method initializes both default parameter_files and default parameters
        from the node's configuration file.
        """
        if self.instance.entity_type != "node":
            return
            
        package_name = None
        if self.instance.configuration and hasattr(self.instance.configuration, 'launch'):
            package_name = self.instance.configuration.launch.get("package")
        
        # 1. Set default parameter_files from node configuration
        if hasattr(self.instance.configuration, 'parameter_files') and self.instance.configuration.parameter_files:
            for cfg_param in self.instance.configuration.parameter_files:
                param_name = cfg_param.get("name")
                param_value = cfg_param.get("value", cfg_param.get("default"))
                # param_schema = cfg_param.get("schema")

                if param_name is None or param_value is None:
                    raise ValueError(f"param_name or param_value is None. namespace: {self.instance.namespace_str}, parameter_files: {self.instance.configuration.parameter_files}")

                # Resolve parameter file path if resolver is available
                if self.parameter_resolver:
                    param_value = self.parameter_resolver.resolve_parameter_file_path(param_value)

                # Add to parameter_files list
                self.parameter_files.add_parameter_file(
                    param_name,
                    param_value,
                    allow_substs=True,
                    is_override=False
                )

                # Load individual parameters from this file
                self._load_parameters_from_file(
                    param_value,
                    package_name=package_name,
                    is_override=False,  # Node configuration parameter files are defaults
                    config_registry=config_registry
                )
        
        # 2. Set default parameters from node parameters
        if hasattr(self.instance.configuration, 'parameters') and self.instance.configuration.parameters:
            for cfg_param in self.instance.configuration.parameters:
                param_name = cfg_param.get("name")
                param_value = cfg_param.get("value", cfg_param.get("default"))
                param_type = cfg_param.get("type", "string")

                if param_name is None or param_value is None:
                    raise ValueError(f"param_name or param_value is None. namespace: {self.instance.namespace_str}, parameter_files: {self.instance.configuration.parameter_files}")

                # Resolve parameter value if resolver is available
                if self.parameter_resolver:
                    param_value = self.parameter_resolver.resolve_parameter_value(param_value)

                # Only set if a default value is provided
                if param_value is not None:
                    self.parameters.set_parameter(
                        param_name,
                        param_value,
                        data_type=param_type,
                        allow_substs=True,
                        parameter_type=ParameterType.DEFAULT  # These are default parameters
                    )

    def _flatten_parameters(self, params: Dict[str, Any], parent_key: str = "", separator: str = ".") -> Dict[str, Any]:
        """Flatten nested dictionary into dot-separated keys.
        
        Args:
            params: The dictionary to flatten
            parent_key: Key prefix for recursion
            separator: Separator for keys
            
        Returns:
            Dict with flattened keys
        """
        items = {}
        for k, v in params.items():
            new_key = f"{parent_key}{separator}{k}" if parent_key else k
            
            if isinstance(v, dict):
                items.update(self._flatten_parameters(v, new_key, separator))
            else:
                items[new_key] = v
        return items

    def _load_parameters_from_file(self, file_path: str, package_name: Optional[str] = None,
                                  is_override: bool = True, config_registry: Optional['ConfigRegistry'] = None):
        """Load parameters from a YAML file and add them to the parameter list."""
        if not config_registry:
            logger.debug(f"Skipping parameter file load for {file_path}: No config_registry provided")
            return

        try:
            # Resolve the full path
            resolved_path = self._resolve_parameter_file_path(file_path, package_name, is_override, config_registry)
            
            # Skip if we couldn't resolve to an absolute path or it involves substitutions
            if not resolved_path or resolved_path.startswith("$") or not os.path.isabs(resolved_path):
                logger.debug(f"Skipping parameter file load for {file_path}: Could not resolve to absolute path ({resolved_path})")
                return
            
            if not os.path.exists(resolved_path):
                logger.warning(f"Parameter file not found: {resolved_path}")
                return
                
            logger.debug(f"Loading parameters from file: {resolved_path}")
            data = yaml_parser.load_config(resolved_path)
            
            if not data:
                return

            # Parse ROS 2 parameter file structure
            # Format:
            # node_name:
            #   ros__parameters:
            #     param_name: value
            
            node_name = self.instance.name
            
            # Find relevant sections
            for key, value in data.items():
                # Check if key matches node name or wildcard
                # Simple matching: exact match or /**
                # Could look into more complex ROS 2 matching rules if needed
                if key == "/**" or key == f"/{node_name}" or key == node_name:
                    if "ros__parameters" in value:
                        # Flatten the nested structure
                        flattened_params = self._flatten_parameters(value["ros__parameters"])
                        
                        for p_name, p_value in flattened_params.items():
                            # Resolve parameter value if resolver is available
                            if self.parameter_resolver:
                                p_value = self.parameter_resolver.resolve_parameter_value(p_value)

                            # Infer type
                            p_type = "string"
                            if isinstance(p_value, bool):
                                p_type = "bool"
                            elif isinstance(p_value, int):
                                p_type = "int"
                            elif isinstance(p_value, float):
                                p_type = "double"
                            elif isinstance(p_value, list):
                                # arrays
                                if len(p_value) > 0:
                                    if isinstance(p_value[0], int): p_type = "int_array"
                                    elif isinstance(p_value[0], float): p_type = "double_array"
                                    elif isinstance(p_value[0], str): p_type = "string_array"
                                    elif isinstance(p_value[0], bool): p_type = "bool_array"

                            # Use appropriate parameter type based on whether this is from an override file
                            param_type = ParameterType.OVERRIDE_FILE if is_override else ParameterType.DEFAULT_FILE
                            self.parameters.set_parameter(
                                p_name,
                                p_value,
                                data_type=p_type,
                                parameter_type=param_type
                            )
        except Exception as e:
            logger.warning(f"Failed to load parameters from file {file_path}: {e}")
