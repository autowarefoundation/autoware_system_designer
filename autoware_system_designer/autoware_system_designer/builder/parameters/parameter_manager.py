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

from ..runtime.parameters import ParameterList, ParameterFileList, Parameter, ParameterFile, ParameterType
from ...models.parsing.yaml_parser import yaml_parser
from ...exceptions import ParameterConfigurationError, ValidationError
from ...file_io.source_location import SourceLocation, source_from_config, format_source

if TYPE_CHECKING:
    from ..instances.instances import Instance
    from ..config.config_registry import ConfigRegistry

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
        self.input_topic_pattern = re.compile(r'\$\{input\s+([^}]+)\}')
        self.output_topic_pattern = re.compile(r'\$\{output\s+([^}]+)\}')
        self.parameter_pattern = re.compile(r'\$\{parameter\s+([^}]+)\}')
        self._substitution_source_context: Optional[SourceLocation] = None

    # =========================================================================
    # Public API Methods
    # =========================================================================

    def resolve_substitutions(self, input_string: str, source: Optional[SourceLocation] = None) -> str:
        """Resolve all substitutions in a string.
        
        Handles:
        1. ${input topic_name}
        2. ${output topic_name}
        3. ${parameter param_name}
        4. $(var ...), $(env ...), $(find-pkg-share ...) if resolver is available
        """
        if not input_string or not isinstance(input_string, str):
            return input_string

        prev_ctx = self._substitution_source_context
        self._substitution_source_context = source
        try:
            resolved_value = input_string

            # 1. Resolve ${input ...}
            resolved_value = self._resolve_input_topic_string(resolved_value)

            # 2. Resolve ${output ...}
            resolved_value = self._resolve_output_topic_string(resolved_value)

            # 3. Resolve ${parameter ...}
            resolved_value = self._resolve_parameter_string(resolved_value)

            # 4. Resolve global/env vars if resolver exists
            if self.parameter_resolver:
                resolved_value = self.parameter_resolver.resolve_string(resolved_value, source=source)

            return resolved_value
        finally:
            self._substitution_source_context = prev_ctx

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

        # sort by its priority. larger enum value is lower priority, comes earlier in the launcher
        self.parameters.list.sort(key=lambda x: x.parameter_type.value)

        # Add regular parameters
        for param in self.parameters.list:
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
            # Skip DEFAULT_FILE parameter files as their parameters are expanded individually
            if param_file.parameter_type == ParameterType.DEFAULT_FILE:
                continue

            resolved_path = self._resolve_parameter_file_path(param_file.path, self._get_package_name(), param_file.is_override)
            result.append({
                "type": "param_file",
                "name": param_file.name,
                "path": resolved_path,
                "allow_substs": param_file.allow_substs,
                "parameter_type": param_file.parameter_type
            })
        return result

    def resolve_all_parameters(self):
        """Resolve any remaining substitutions in all parameters and parameter files."""
        # Resolve all parameter values
        for param in self.parameters.list:
            if param.value is not None and isinstance(param.value, str):
                resolved_value = self.resolve_substitutions(param.value, source=getattr(param, "source", None))
                if resolved_value != param.value:
                    param.value = resolved_value

        # Resolve all parameter file paths
        for param_file in self.parameter_files.list:
            if param_file.path and isinstance(param_file.path, str):
                resolved_path = self.resolve_substitutions(param_file.path, source=getattr(param_file, "source", None))
                
                if resolved_path != param_file.path:
                    param_file.path = resolved_path

    def _resolve_input_topic_string(self, input_string: str) -> str:
        """Resolve ${input port_name} substitutions."""
        if not input_string or not isinstance(input_string, str):
            return input_string
        return self.input_topic_pattern.sub(self._resolve_input_topic_match, input_string)

    def _resolve_input_topic_match(self, match) -> str:
        """Resolve a single ${input port_name} match."""
        port_name = match.group(1).strip()
        try:
            in_port = self.instance.link_manager.get_in_port(port_name)
            topic = in_port.get_topic()
            if topic:
                return topic
            else:
                return "none"
        except ValidationError:
            logger.warning(
                f"Input port not found for substitution: {port_name} in {self.instance.name}{format_source(self._substitution_source_context)}"
            )
            return match.group(0)  # Return original if not found

    def _resolve_output_topic_string(self, input_string: str) -> str:
        """Resolve ${output port_name} substitutions."""
        if not input_string or not isinstance(input_string, str):
            return input_string
        return self.output_topic_pattern.sub(self._resolve_output_topic_match, input_string)

    def _resolve_output_topic_match(self, match) -> str:
        """Resolve a single ${output port_name} match."""
        port_name = match.group(1).strip()
        try:
            out_port = self.instance.link_manager.get_out_port(port_name)
            topic = out_port.get_topic()
            if topic:
                return topic
            else:
                return "none"
        except ValidationError:
            logger.warning(
                f"Output port not found for substitution: {port_name} in {self.instance.name}{format_source(self._substitution_source_context)}"
            )
            return match.group(0)  # Return original if not found

    def _resolve_parameter_string(self, input_string: str) -> str:
        """Resolve ${parameter param_name} substitutions."""
        if not input_string or not isinstance(input_string, str):
            return input_string
        return self.parameter_pattern.sub(self._resolve_parameter_match, input_string)

    def _resolve_parameter_match(self, match) -> str:
        """Resolve a single ${parameter param_name} match."""
        param_name = match.group(1).strip()
        # Look up parameter in self.parameters
        # We need to get the effective value of the parameter
        param_value = self.parameters.get_parameter(param_name)
        
        if param_value is not None:
            return str(param_value)
        else:
            logger.warning(
                f"Parameter not found for substitution: {param_name} in {self.instance.name}{format_source(self._substitution_source_context)}"
            )
            return match.group(0)  # Return original if not found

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
            raise ParameterConfigurationError(f"path is None. package_name: {package_name}, node_namespace: {self.instance.namespace_str}, path: {path}")

        # Resolve any substitutions in the path first
        if self.parameter_resolver:
            path = self.parameter_resolver.resolve_string(path)

        # If path is now absolute, return it
        if path.startswith('/'):
            return path

        # if path starts with $(find-pkg-share, it's not resolved package path, so return it as is
        if path.startswith('$(find-pkg-share'):
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
    
    def apply_node_parameters(
        self,
        node_namespace: str,
        parameter_files: list,
        parameters: list,
        config_registry: Optional['ConfigRegistry'] = None,
        file_parameter_type: ParameterType = ParameterType.OVERRIDE_FILE,
        direct_parameter_type: ParameterType = ParameterType.OVERRIDE,
        source: Optional[SourceLocation] = None,
        parameter_file_sources: Optional[List[SourceLocation]] = None,
        parameter_sources: Optional[List[SourceLocation]] = None,
    ):
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
            file_parameter_type: ParameterType for parameters loaded from files
            direct_parameter_type: ParameterType for directly specified parameters
        """
        # Handle global parameters (root node)
        if node_namespace == '/':
            self.apply_parameters_to_all_nodes(parameter_files, parameters, config_registry, file_parameter_type, direct_parameter_type)
            return

        target_instances = self.find_matching_nodes(node_namespace)
        if not target_instances:
            logger.warning(f"Target node not found: {node_namespace}{format_source(source)}")
            return
            
        for target_instance in target_instances:
            logger.info(f"Applying parameters to node: {node_namespace} (instance: {target_instance.name})")
            self._apply_parameters_to_instance(
                target_instance,
                parameter_files,
                parameters,
                config_registry,
                file_parameter_type,
                direct_parameter_type,
                parameter_file_sources=parameter_file_sources,
                parameter_sources=parameter_sources,
            )

    def apply_parameters_to_all_nodes(self, parameter_files: list, parameters: list, config_registry: Optional['ConfigRegistry'] = None,
                                      file_parameter_type: ParameterType = ParameterType.OVERRIDE_FILE,
                                      direct_parameter_type: ParameterType = ParameterType.OVERRIDE):
        """Apply parameters to all nodes in the instance tree.
        
        Args:
            parameter_files: List of parameter file mappings
            parameters: List of direct parameters
            config_registry: Registry for resolving paths
            file_parameter_type: ParameterType for parameters loaded from files
            direct_parameter_type: ParameterType for directly specified parameters
        """
        logger.info(f"Applying parameters to all nodes (global scope)")
        
        # Start from the root deployment instance
        root_instance = self.instance
        while root_instance.parent is not None:
            root_instance = root_instance.parent
            
        self._apply_parameters_recursive(root_instance, parameter_files, parameters, config_registry, file_parameter_type, direct_parameter_type)

    def _apply_parameters_recursive(self, instance, parameter_files, parameters, config_registry, file_parameter_type, direct_parameter_type):
        """Recursively apply parameters to instance tree."""
        if instance.entity_type == "node":
             self._apply_parameters_to_instance(instance, parameter_files, parameters, config_registry, file_parameter_type, direct_parameter_type)
        
        for child in instance.children.values():
            self._apply_parameters_recursive(child, parameter_files, parameters, config_registry, file_parameter_type, direct_parameter_type)

    def _apply_parameters_to_instance(
        self,
        target_instance,
        parameter_files: list,
        parameters: list,
        config_registry: Optional['ConfigRegistry'] = None,
        file_parameter_type: ParameterType = ParameterType.OVERRIDE_FILE,
        direct_parameter_type: ParameterType = ParameterType.OVERRIDE,
        *,
        parameter_file_sources: Optional[List[SourceLocation]] = None,
        parameter_sources: Optional[List[SourceLocation]] = None,
    ):
        """Apply parameters directly to a target instance object."""
            
        # Apply parameter files first (as overrides, not defaults)
        if parameter_files:
            for idx, param_file_mapping in enumerate(parameter_files):
                pf_source = None
                if parameter_file_sources and idx < len(parameter_file_sources):
                    pf_source = parameter_file_sources[idx]
                for param_name, param_path in param_file_mapping.items():
                    # Resolve parameter file path if resolver is available
                    if self.parameter_resolver:
                        param_path = self.parameter_resolver.resolve_parameter_file_path(param_path, source=pf_source)

                    target_instance.parameter_manager.parameter_files.add_parameter_file(
                        param_name,
                        param_path,
                        allow_substs=True,
                        is_override=True,  # Parameter set parameter files are overrides
                        parameter_type=file_parameter_type,
                        source=pf_source,
                    )

                    # Load parameters from this file for visualization
                    target_instance.parameter_manager._load_parameters_from_file(
                        param_path,
                        is_override=True,
                        config_registry=config_registry,
                        parameter_type=file_parameter_type,
                        source=pf_source,
                    )
        
        # Apply parameters (these override parameter files)
        if parameters:
            for idx, param in enumerate(parameters):
                p_source = None
                if parameter_sources and idx < len(parameter_sources):
                    p_source = parameter_sources[idx]
                param_name = param.get("name")
                param_type = param.get("type", "string")
                param_value = param.get("value")

                # Resolve parameter value if resolver is available
                if self.parameter_resolver:
                    param_value = self.parameter_resolver.resolve_parameter_value(param_value, source=p_source)

                target_instance.parameter_manager.parameters.set_parameter(
                    param_name,
                    param_value,
                    data_type=param_type,
                    allow_substs=True,
                    parameter_type=direct_parameter_type,  # Parameter set overrides
                    source=p_source,
                )

    # =========================================================================
    # Node Finding (helper methods for parameter application)
    # =========================================================================
    
    def find_matching_nodes(self, target_namespace: str) -> List['Instance']:
        """Find all nodes matching the absolute namespace path in the current instance's subtree.
        
        Args:
            target_namespace: Absolute namespace path (e.g., "/perception/object_recognition/node_tracker")
            
        Returns:
            List of matching Instance objects (nodes)
        """
        matches = []
        
        # Helper for recursive search
        def _search(inst):
            # Check if current instance matches
            if inst.entity_type == "node" and inst.namespace_str == target_namespace:
                matches.append(inst)
            
            # Optimization: only traverse if target could be deeper
            # i.e., target_namespace starts with current namespace
            # OR current namespace is root "/"
            # OR current namespace is a prefix of target
            
            if inst.namespace_str == "/" or target_namespace.startswith(inst.namespace_str + "/") or inst.namespace_str == target_namespace:
                for child in inst.children.values():
                    _search(child)
                    
        _search(self.instance)
        return matches

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
            for idx, cfg_param in enumerate(self.instance.configuration.parameter_files):
                param_name = cfg_param.get("name")
                param_value = cfg_param.get("value", cfg_param.get("default"))
                # param_schema = cfg_param.get("schema")

                cfg_source = source_from_config(self.instance.configuration, f"/parameter_files/{idx}")

                if param_name is None or param_value is None:
                    raise ParameterConfigurationError(f"param_name or param_value is None. namespace: {self.instance.namespace_str}, parameter_files: {self.instance.configuration.parameter_files}")

                # Resolve parameter file path if resolver is available
                if self.parameter_resolver:
                    param_value = self.parameter_resolver.resolve_parameter_file_path(param_value, source=cfg_source)

                # Add to parameter_files list
                self.parameter_files.add_parameter_file(
                    param_name,
                    param_value,
                    allow_substs=True,
                    is_override=False,
                    parameter_type=ParameterType.DEFAULT_FILE,
                    source=cfg_source,
                )

                # Load individual parameters from this file
                self._load_parameters_from_file(
                    param_value,
                    package_name=package_name,
                    is_override=False,  # Node configuration parameter files are defaults
                    config_registry=config_registry,
                    source=cfg_source,
                )
        
        # 2. Set default parameters from node parameters
        if hasattr(self.instance.configuration, 'parameters') and self.instance.configuration.parameters:
            for idx, cfg_param in enumerate(self.instance.configuration.parameters):
                param_name = cfg_param.get("name")
                param_value = cfg_param.get("value", cfg_param.get("default"))
                param_type = cfg_param.get("type", "string")

                cfg_source = source_from_config(self.instance.configuration, f"/parameters/{idx}")

                if param_name is None or param_value is None:
                    raise ParameterConfigurationError(f"param_name or param_value is None. namespace: {self.instance.namespace_str}, parameter_files: {self.instance.configuration.parameter_files}")

                # Resolve parameter value if resolver is available
                if self.parameter_resolver:
                    param_value = self.parameter_resolver.resolve_parameter_value(param_value, source=cfg_source)

                # Only set if a default value is provided
                if param_value is not None:
                    self.parameters.set_parameter(
                        param_name,
                        param_value,
                        data_type=param_type,
                        allow_substs=True,
                        parameter_type=ParameterType.DEFAULT,  # These are default parameters
                        source=cfg_source,
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

    def _infer_package_from_share_path(self, absolute_path: str) -> Optional[str]:
        if not absolute_path or not os.path.isabs(absolute_path):
            return None
        parts = absolute_path.split(os.sep)
        if "share" not in parts:
            return None
        # Pick the last 'share/<pkg>/' segment (works for merged and isolated installs).
        share_idx = len(parts) - 1 - parts[::-1].index("share")
        if share_idx + 1 >= len(parts):
            return None
        return parts[share_idx + 1]

    def _resolve_existing_parameter_file_path(
        self,
        file_path: str,
        package_name: Optional[str],
        is_override: bool,
        config_registry: 'ConfigRegistry',
        source: Optional[SourceLocation],
    ) -> Optional[str]:
        """Resolve a parameter file path to an existing absolute path (for visualization/template generation).

        This is used for *loading* parameter YAML contents. It intentionally returns None when the
        path can't be resolved to an existing absolute file.
        """

        resolved_path = self._resolve_parameter_file_path(file_path, package_name, is_override, config_registry)

        # Only load when resolved to an absolute filesystem path with no remaining substitutions.
        if not resolved_path or resolved_path.startswith("$") or not os.path.isabs(resolved_path):
            logger.debug(
                f"Skipping parameter file load for {file_path}: Could not resolve to absolute path ({resolved_path})"
            )
            return None

        if os.path.exists(resolved_path):
            return resolved_path

        deployment_pkg = getattr(config_registry, "deployment_package_name", None)
        if not deployment_pkg:
            logger.warning(f"Parameter file not found: {resolved_path}{format_source(source)}")
            return None

        # Build-time fallback (deployment build only): install/share may not be populated yet.
        inferred_pkg = package_name or self._infer_package_from_share_path(resolved_path)
        if inferred_pkg != deployment_pkg:
            logger.warning(f"Parameter file not found: {resolved_path}{format_source(source)}")
            return None

        if inferred_pkg:
            marker = os.path.join(os.sep, "share", inferred_pkg, "")
            idx = resolved_path.find(marker)
            if idx != -1:
                rel = resolved_path[idx + len(marker) :]
                src_pkg = config_registry.get_package_source_path(inferred_pkg)
                if src_pkg:
                    candidate = os.path.join(src_pkg, rel)
                    if os.path.exists(candidate):
                        return candidate

        logger.warning(f"Parameter file not found: {resolved_path}{format_source(source)}")
        return None

    def _infer_ros_param_type(self, value: Any) -> str:
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "double"
        if isinstance(value, list):
            if len(value) == 0:
                return "string_array"
            if isinstance(value[0], bool):
                return "bool_array"
            if isinstance(value[0], int):
                return "int_array"
            if isinstance(value[0], float):
                return "double_array"
            if isinstance(value[0], str):
                return "string_array"
            return "string_array"
        return "string"

    def _load_parameters_from_file(
        self,
        file_path: str,
        package_name: Optional[str] = None,
        is_override: bool = True,
        config_registry: Optional['ConfigRegistry'] = None,
        parameter_type: Optional[ParameterType] = None,
        source: Optional[SourceLocation] = None,
    ):
        """Load parameters from a YAML file and add them to the parameter list."""
        if not config_registry:
            logger.debug(f"Skipping parameter file load for {file_path}: No config_registry provided")
            return

        try:
            existing_path = self._resolve_existing_parameter_file_path(
                file_path,
                package_name,
                is_override,
                config_registry,
                source,
            )
            if not existing_path:
                return

            logger.debug(f"Loading parameters from file: {existing_path}")
            data = yaml_parser.load_config(existing_path)
            if not data:
                return

            node_name = self.instance.name
            for key, value in data.items():
                if key not in ("/**", f"/{node_name}", node_name):
                    continue
                ros_params = value.get("ros__parameters") if isinstance(value, dict) else None
                if not ros_params:
                    continue

                flattened_params = self._flatten_parameters(ros_params)
                effective_type = parameter_type or (
                    ParameterType.OVERRIDE_FILE if is_override else ParameterType.DEFAULT_FILE
                )

                for p_name, p_value in flattened_params.items():
                    if self.parameter_resolver:
                        p_value = self.parameter_resolver.resolve_parameter_value(p_value, source=source)
                    self.parameters.set_parameter(
                        p_name,
                        p_value,
                        data_type=self._infer_ros_param_type(p_value),
                        parameter_type=effective_type,
                        source=source,
                    )
        except Exception as e:
            logger.warning(
                f"Failed to load parameters from file {file_path}: {e}{format_source(source)}"
            )
