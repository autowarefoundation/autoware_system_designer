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
import copy
from pathlib import Path
from typing import Dict, Tuple, List, Any
from .deployment_config import DeploymentConfig
from .builder.config_registry import ConfigRegistry
from .builder.instances import DeploymentInstance
from .builder.launcher_generator import generate_module_launch_file
from .builder.parameter_template_generator import ParameterTemplateGenerator
from .parsers.data_validator import entity_name_decode
from .parsers.yaml_parser import yaml_parser
from .exceptions import ValidationError, DeploymentError
from .utils.template_utils import TemplateRenderer
from .utils.system_structure_json import (
    save_system_structure,
    save_system_structure_snapshot,
    load_system_structure,
    extract_system_structure_data,
)
from .utils import generate_build_scripts
from .visualization.visualize_deployment import visualize_deployment
from .models.config import SystemConfig
from .utils.source_location import SourceLocation, source_from_config, format_source

logger = logging.getLogger(__name__)
debug_mode = True


def _apply_removals(config: SystemConfig, remove_spec: Dict[str, Any]) -> None:
    """
    Remove components and connections from system configuration.
    
    Args:
        config: SystemConfig to modify in-place
        remove_spec: Dictionary containing 'components' and/or 'connections' to remove
    """
    # Remove components
    if 'components' in remove_spec:
        components_to_remove = remove_spec['components']
        if components_to_remove:
            # Build set of component names to remove
            remove_names = set()
            for item in components_to_remove:
                if isinstance(item, dict) and 'name' in item:
                    remove_names.add(item['name'])

            # Filter out components to remove
            if config.components:
                config.components = [
                    comp for comp in config.components
                    if comp.get('name') not in remove_names
                ]
                logger.debug(f"Removed {len(remove_names)} components: {remove_names}")

            # Also remove connections from/to removed components
            if config.connections:
                def _endpoint_component(endpoint: Any) -> str | None:
                    if not isinstance(endpoint, str):
                        return None
                    return endpoint.split('.', 1)[0] if endpoint else None

                original_count = len(config.connections)
                config.connections = [
                    conn for conn in config.connections
                    if _endpoint_component(conn.get('from')) not in remove_names
                    and _endpoint_component(conn.get('to')) not in remove_names
                ]
                removed_count = original_count - len(config.connections)
                if removed_count:
                    logger.debug(
                        f"Removed {removed_count} connections referencing removed components"
                    )
    
    # Remove connections
    if 'connections' in remove_spec:
        connections_to_remove = remove_spec['connections']
        if connections_to_remove and config.connections:
            # For connections, we need to match all fields in the spec
            original_count = len(config.connections)
            filtered_connections = []
            
            for conn in config.connections:
                should_remove = False
                for remove_conn in connections_to_remove:
                    # Check if all fields in remove_conn match the connection
                    if all(conn.get(k) == v for k, v in remove_conn.items()):
                        should_remove = True
                        break
                
                if not should_remove:
                    filtered_connections.append(conn)
            
            config.connections = filtered_connections
            removed_count = original_count - len(filtered_connections)
            logger.debug(f"Removed {removed_count} connections")


def _apply_overrides(config: SystemConfig, override_spec: Dict[str, Any]) -> None:
    """
    Apply overrides/additions to system configuration.
    
    Args:
        config: SystemConfig to modify in-place
        override_spec: Dictionary containing 'components', 'connections', and/or 'parameter_sets' to override/add
    """
    def _merge_named_list(base_list: List[Dict[str, Any]] | None, override_list: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
        if not override_list:
            return base_list or []
        merged = [item.copy() for item in (base_list or [])]
        index_by_name = {item.get('name'): i for i, item in enumerate(merged) if isinstance(item, dict)}
        for item in override_list:
            name = item.get('name') if isinstance(item, dict) else None
            if name and name in index_by_name:
                merged[index_by_name[name]] = item
            else:
                merged.append(item)
        return merged

    # Override/merge variables
    if 'variables' in override_spec:
        override_variables = override_spec['variables']
        if override_variables is not None:
            config.variables = _merge_named_list(config.variables, override_variables)

    # Override/merge variable_files
    if 'variable_files' in override_spec:
        override_variable_files = override_spec['variable_files']
        if override_variable_files is not None:
            config.variable_files = _merge_named_list(config.variable_files, override_variable_files)

    # Override parameter_sets (replaces base parameter_sets completely)
    if 'parameter_sets' in override_spec:
        override_parameter_sets = override_spec['parameter_sets']
        if override_parameter_sets is not None:
            config.parameter_sets = override_parameter_sets
            logger.info(f"Overrode parameter_sets with {len(override_parameter_sets) if isinstance(override_parameter_sets, list) else 1} parameter set(s)")
    
    # Override/add components
    if 'components' in override_spec:
        override_components = override_spec['components']
        if override_components:
            if not config.components:
                config.components = []
            
            # Build a map of existing components by name
            component_map = {
                comp.get('name'): idx 
                for idx, comp in enumerate(config.components)
                if 'name' in comp
            }
            
            # Apply overrides or add new components
            for override_comp in override_components:
                comp_name = override_comp.get('name')
                if comp_name in component_map:
                    # Override existing component
                    idx = component_map[comp_name]
                    config.components[idx] = override_comp
                    logger.debug(f"Overrode component '{comp_name}'")
                else:
                    # Add new component
                    config.components.append(override_comp)
                    logger.debug(f"Added new component '{comp_name}'")
    
    # Add/override connections
    if 'connections' in override_spec:
        override_connections = override_spec['connections']
        if override_connections:
            if not config.connections:
                config.connections = []
            
            # For connections, we simply append them (no override logic needed)
            # as connections are uniquely identified by their from/to combination
            config.connections.extend(override_connections)
            logger.debug(f"Added {len(override_connections)} connections")


def apply_mode_configuration(base_system_config: SystemConfig, mode_name: str) -> SystemConfig:
    """
    Create a copy of base system and apply mode-specific overrides/removals.
    
    Args:
        base_system_config: The base system configuration
        mode_name: Name of the mode to apply (or "default" for base)
    
    Returns:
        Modified system configuration with mode applied
    """
    # Create a deep copy to avoid modifying original
    modified_config = copy.deepcopy(base_system_config)
    
    # Filter out components with explicit 'mode' fields from base (deprecated old format)
    # These components should be defined in mode-specific sections instead
    if modified_config.components:
        filtered_components = []
        for comp in modified_config.components:
            if 'mode' in comp:
                logger.debug(f"Filtering out component '{comp.get('name')}' with deprecated 'mode' field from base")
            else:
                filtered_components.append(comp)
        modified_config.components = filtered_components
    
    # If mode is "default" or no mode configs exist, return the filtered base
    if mode_name == "default" or not base_system_config.mode_configs:
        return modified_config
    
    mode_config = base_system_config.mode_configs.get(mode_name)
    if not mode_config:
        # Mode not found, return base configuration
        src = source_from_config(base_system_config, "/modes")
        logger.warning(
            f"Mode '{mode_name}' not found in mode_configs, using base configuration{format_source(src)}"
        )
        return modified_config
    
    logger.info(f"Applying mode configuration for mode '{mode_name}'")
    
    # Apply removals first
    if 'remove' in mode_config:
        _apply_removals(modified_config, mode_config['remove'])
    
    # Apply overrides/additions
    if 'override' in mode_config:
        _apply_overrides(modified_config, mode_config['override'])
    
    return modified_config

class Deployment:
    def __init__(self, deploy_config: DeploymentConfig ):
        # entity collection
        system_yaml_list, package_paths, file_package_map = self._get_system_list(deploy_config)
        self.config_registry = ConfigRegistry(system_yaml_list, package_paths, file_package_map)

        # detect mode of input file (deployment vs system only)
        logger.info("deployment init Deployment file: %s", deploy_config.deployment_file)
        
        input_path = deploy_config.deployment_file
        system_name = None
        
        # System by name
        system_name = os.path.basename(input_path)
        # Remove extension if present, though entity_name_decode handles check
        if system_name.endswith('.yaml'):
            system_name = system_name[:-5]
            
        # If name is full name (name.system), decode it
        if "." in system_name:
                system_name, _ = entity_name_decode(system_name)

        # Get system from registry (this handles base/variant resolution)
        system_config = self.config_registry.get_system(system_name)
        if not system_config:
            raise ValidationError(f"System not found: {system_name}")
        
        self.config_yaml_dir = str(system_config.file_path)
        logger.info(f"Resolved system file path from registry: {self.config_yaml_dir}")
        
        # Load the resolved config (which is what get_system returned)
        # Wait, get_system returns a SystemConfig object which HAS the config dict.
        # We don't need to load yaml again.
        
        self.name = system_config.name

        # member variables - now supports multiple modes
        self.mode_keys: List[str] = []

        # 4. set output paths
        self.output_root_dir = deploy_config.output_root_dir
        self.launcher_dir = os.path.join(self.output_root_dir, "exports", self.name, "launcher/")
        self.system_monitor_dir = os.path.join(self.output_root_dir, "exports", self.name, "system_monitor/")
        self.visualization_dir = os.path.join(self.output_root_dir, "exports", self.name,"visualization/")
        self.parameter_set_dir = os.path.join(self.output_root_dir, "exports", self.name,"parameter_set/")
        self.system_structure_dir = os.path.join(self.output_root_dir, "exports", self.name, "system_structure/")
        self.system_structure_snapshots: Dict[str, Dict[str, Any]] = {}

        # 5. build the deployment
        self._build(system_config, package_paths)

    def _get_system_list(self, deploy_config: DeploymentConfig) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
        system_list: list[str] = []
        package_paths: Dict[str, str] = {}
        file_package_map: Dict[str, str] = {}
        manifest_dir = deploy_config.manifest_dir
        if not os.path.isdir(manifest_dir):
            raise ValidationError(f"System design manifest directory not found or not a directory: {manifest_dir}")

        for entry in sorted(os.listdir(manifest_dir)):
            if not entry.endswith('.yaml'):
                continue
            manifest_file = os.path.join(manifest_dir, entry)
            try:
                manifest_yaml = yaml_parser.load_config(manifest_file)
                
                # Load package map if available
                if 'package_map' in manifest_yaml:
                    package_paths.update(manifest_yaml['package_map'])

                files = manifest_yaml.get('deploy_config_files')
                # Allow the field to be empty or null without raising an error
                if files in (None, []):
                    logger.debug(
                        f"Manifest '{entry}' has empty deploy_config_files; skipping."
                    )
                    continue
                if not isinstance(files, list):
                    manifest_src = SourceLocation(file_path=Path(manifest_file))
                    logger.warning(
                        f"Manifest '{entry}' has unexpected type for deploy_config_files: {type(files)}; skipping.{format_source(manifest_src)}"
                    )
                    continue
                for f in files:
                    file_path = f.get('path') if isinstance(f, dict) else None
                    if file_path and file_path not in system_list:
                        system_list.append(file_path)
                    
                    if file_path and 'package_name' in manifest_yaml:
                        file_package_map[file_path] = manifest_yaml['package_name']

            except Exception as e:
                manifest_src = SourceLocation(file_path=Path(manifest_file))
                logger.warning(f"Failed to load manifest {manifest_file}: {e}{format_source(manifest_src)}")
        if not system_list:
            raise ValidationError(f"No system design configuration files collected.")
        return system_list, package_paths, file_package_map

    def _create_snapshot_callback(
        self,
        mode_key: str,
        deploy_instance: DeploymentInstance,
        snapshot_store: Dict[str, Any],
    ):
        def snapshot_callback(step: str, error: Exception | None = None) -> None:
            snapshot_path = os.path.join(self.system_structure_dir, f"{mode_key}_{step}.json")
            payload = save_system_structure_snapshot(
                snapshot_path, deploy_instance, self.name, mode_key, step, error
            )
            snapshot_store[step] = payload

        return snapshot_callback

    def _build_mode_instance(
        self,
        mode_name: str,
        mode_system_config: SystemConfig,
        package_paths: Dict[str, str],
        default_mode: str,
    ) -> Tuple[str, Dict[str, Any]]:
        mode_suffix = f"_{mode_name}" if mode_name else ""
        instance_name = f"{self.name}{mode_suffix}"
        deploy_instance = DeploymentInstance(instance_name)

        snapshot_store: Dict[str, Any] = {}
        mode_key = mode_name if mode_name else default_mode

        snapshot_callback = self._create_snapshot_callback(
            mode_key, deploy_instance, snapshot_store
        )

        deploy_instance.set_system(
            mode_system_config,
            self.config_registry,
            package_paths=package_paths,
            snapshot_callback=snapshot_callback,
        )

        # Save system structure JSON for downstream consumers
        structure_payload = deploy_instance.collect_system_structure(self.name, mode_key)
        structure_path = os.path.join(self.system_structure_dir, f"{mode_key}.json")
        save_system_structure(structure_path, structure_payload)

        return mode_key, snapshot_store

    def _build(self, system_config, package_paths):
        # 2. Determine modes to build
        modes_config = system_config.modes or []
        
        if modes_config:
            # Use defined modes
            mode_names = [m.get('name') for m in modes_config]
            
            # If a mode has default=true, use that as default, otherwise use first mode
            default_mode = next((m.get('name') for m in modes_config if m.get('default')), mode_names[0])
            logger.info(f"Building deployment for {len(mode_names)} modes: {mode_names}, default: {default_mode}")
        else:
            # No modes defined - use "default" as the mode name
            mode_names = ["default"]
            default_mode = "default"
            logger.info(f"Building deployment with single 'default' mode")

        # 3. Create deployment instance for each mode
        self.mode_keys = []
        for mode_name in mode_names:
            mode_key = mode_name if mode_name else default_mode
            snapshot_store: Dict[str, Any] = {}
            try:
                # Apply mode configuration on top of base system
                mode_system_config = apply_mode_configuration(system_config, mode_name)

                mode_key, snapshot_store = self._build_mode_instance(
                    mode_name, mode_system_config, package_paths, default_mode
                )
                self.mode_keys.append(mode_key)
                logger.info(f"Successfully built deployment instance for mode: {mode_key}")

                self.system_structure_snapshots[mode_key] = snapshot_store
            except Exception as e:
                self.system_structure_snapshots[mode_key] = snapshot_store
                # try to visualize the system to show error status
                self.visualize()
                raise DeploymentError(f"Error in setting deploy for mode '{mode_name}': {e}")

    def visualize(self):
        # Collect data from all deployment instances
        deploy_data = {}
        for mode_key in self.mode_keys:
            structure_path = os.path.join(self.system_structure_dir, f"{mode_key}.json")
            payload = load_system_structure(structure_path)
            data, _ = extract_system_structure_data(payload)
            deploy_data[mode_key] = data

        visualize_deployment(deploy_data, self.name, self.visualization_dir)

    def generate_by_template(self, data, template_path, output_dir, output_filename):
        """Generate file from template using the unified template renderer."""
        # Initialize template renderer
        renderer = TemplateRenderer()
        
        # Get template name from path
        template_name = os.path.basename(template_path)
        
        # Render template and save to file
        output_path = os.path.join(output_dir, output_filename)
        renderer.render_template_to_file(template_name, output_path, **data)

    def generate_system_monitor(self):
        # load the template file
        template_dir = os.path.join(os.path.dirname(__file__), "template")
        topics_template_path = os.path.join(template_dir, "sys_monitor_topics.yaml.jinja2")

        # Generate system monitor for each mode
        for mode_key in self.mode_keys:
            structure_path = os.path.join(self.system_structure_dir, f"{mode_key}.json")
            payload = load_system_structure(structure_path)
            data, _ = extract_system_structure_data(payload)
            
            # Create mode-specific output directory
            mode_monitor_dir = os.path.join(self.system_monitor_dir, mode_key, "component_state_monitor")
            self.generate_by_template(data, topics_template_path, mode_monitor_dir, "topics.yaml")
            
            logger.info(f"Generated system monitor for mode: {mode_key}")


    def generate_build_scripts(self):
        """Generate shell scripts to build necessary packages for each ECU."""
        deploy_data = {}
        for mode_key in self.mode_keys:
            structure_path = os.path.join(self.system_structure_dir, f"{mode_key}.json")
            payload = load_system_structure(structure_path)
            data, _ = extract_system_structure_data(payload)
            deploy_data[mode_key] = data

        generate_build_scripts(
            deploy_data,
            self.output_root_dir,
            self.name,
            self.config_yaml_dir,
            self.config_registry.file_package_map
        )


    def generate_launcher(self):
        # Generate launcher files for each mode
        for mode_key in self.mode_keys:
            # Create mode-specific launcher directory
            mode_launcher_dir = os.path.join(self.launcher_dir, mode_key)
            
            # Generate module launch files from JSON structure
            structure_path = os.path.join(self.system_structure_dir, f"{mode_key}.json")
            payload = load_system_structure(structure_path)
            generate_module_launch_file(payload, mode_launcher_dir)
            
            logger.info(f"Generated launcher for mode: {mode_key}")

    def generate_parameter_set_template(self):
        """Generate parameter set template using ParameterTemplateGenerator."""
        if not self.mode_keys:
            raise DeploymentError("Deployment instances are not initialized")
        
        # Generate parameter set template for each mode
        output_paths = {}
        for mode_key in self.mode_keys:
            # Create mode-specific output directory
            mode_parameter_dir = os.path.join(self.parameter_set_dir, mode_key)
            os.makedirs(mode_parameter_dir, exist_ok=True)
            
            # Initialize template renderer
            renderer = TemplateRenderer()
            
            # Create parameter template generator and generate the template
            structure_path = os.path.join(self.system_structure_dir, f"{mode_key}.json")
            payload = load_system_structure(structure_path)
            data, _ = extract_system_structure_data(payload)
            template_name = f"{self.name}_{mode_key}" if mode_key != "default" else self.name
            output_path_list = ParameterTemplateGenerator.generate_parameter_set_template_from_data(
                data,
                template_name,
                renderer,
                mode_parameter_dir
            )

            output_paths[mode_key] = output_path_list
            logger.info(f"Generated {len(output_path_list)} parameter set templates for mode: {mode_key}")
        
        return output_paths
