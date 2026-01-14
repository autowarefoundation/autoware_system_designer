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
from .utils import generate_build_scripts
from .visualization.visualize_deployment import visualize_deployment
from .models.config import SystemConfig

logger = logging.getLogger(__name__)
debug_mode = True

class Deployment:
    def __init__(self, deploy_config: DeploymentConfig ):
        # entity collection
        system_yaml_list, package_paths, file_package_map = self._get_system_list(deploy_config)
        self.config_registry = ConfigRegistry(system_yaml_list, package_paths, file_package_map)

        # detect mode of input file (deployment vs system only)
        logger.info("deployment init Deployment file: %s", deploy_config.deployment_file)
        
        input_path = deploy_config.deployment_file
        config_yaml = {}
        system_name = None
        system_config = None
        
        if not os.path.exists(input_path):
            # System
            system_name = os.path.basename(input_path)
            system_name, _ = entity_name_decode(system_name)
            
            system_config = self.config_registry.get_system(system_name)
            if not system_config:
                raise ValidationError(f"System not found: {system_name}")
            
            self.config_yaml_dir = str(system_config.file_path)
            logger.info(f"Resolved system file path from registry: {self.config_yaml_dir}")
            
            config_yaml = yaml_parser.load_config(self.config_yaml_dir)
            logger.info("Detected system-only deployment file.")

        else:
            # Deployment(system inheritance)
            self.config_yaml_dir = input_path
            
            config_yaml = yaml_parser.load_config(self.config_yaml_dir)
            
            system_name = config_yaml['system']
            system_name, _ = entity_name_decode(system_name)

            system_config = self.config_registry.get_system(system_name)
            if not system_config:
                raise ValidationError(f"System not found: {system_name}")

            # Generalize inheritance: Merge lists using _merge_list helper
            
            # 1. Variables (key='name')
            system_config.variables = self._merge_list(
                system_config.variables or [], 
                config_yaml.get('variables', []), 
                key_field='name'
            )
            
            # 2. Variable Files (append only)
            system_config.variable_files = self._merge_list(
                system_config.variable_files or [], 
                config_yaml.get('variable_files', []), 
                key_field=None
            )

            # 3. Modes (key='name')
            system_config.modes = self._merge_list(
                system_config.modes or [],
                config_yaml.get('modes', []),
                key_field='name'
            )

            # 4. Components (key='component')
            system_config.components = self._merge_list(
                system_config.components or [],
                config_yaml.get('components', []),
                key_field='component'
            )

            # 5. Connections (append only, or key='name' if connections have names and need overriding)
            # Plan specified append only for connections.
            system_config.connections = self._merge_list(
                system_config.connections or [],
                config_yaml.get('connections', []),
                key_field=None
            )

            # Apply removals if 'remove' section exists
            remove_config = config_yaml.get('remove', {})
            if remove_config:
                # 1. Remove Modes (key='name')
                if 'modes' in remove_config:
                    # Capture removed mode names for component cleanup
                    removed_mode_names = [m.get('name') for m in remove_config['modes'] if 'name' in m]
                    
                    system_config.modes = self._remove_list(
                        system_config.modes,
                        remove_config['modes'],
                        key_field='name'
                    )
                    
                    # Cleanup components referencing removed modes
                    self._cleanup_components_modes(system_config, removed_mode_names)
                
                # 2. Remove Components (key='component')
                if 'components' in remove_config:
                    system_config.components = self._remove_list(
                        system_config.components,
                        remove_config['components'],
                        key_field='component'
                    )

                # 3. Remove Variables (key='name')
                if 'variables' in remove_config:
                    system_config.variables = self._remove_list(
                        system_config.variables,
                        remove_config['variables'],
                        key_field='name'
                    )

                # 4. Remove Connections (subset match)
                if 'connections' in remove_config:
                    system_config.connections = self._remove_list(
                        system_config.connections,
                        remove_config['connections'],
                        key_field=None
                    )

        self.name = config_yaml.get("name")

        # member variables - now supports multiple instances (one per mode)
        self.deploy_instances: Dict[str, DeploymentInstance] = {}  # mode_name -> DeploymentInstance

        # 4. set output paths
        self.output_root_dir = deploy_config.output_root_dir
        self.launcher_dir = os.path.join(self.output_root_dir, "exports", self.name, "launcher/")
        self.system_monitor_dir = os.path.join(self.output_root_dir, "exports", self.name, "system_monitor/")
        self.visualization_dir = os.path.join(self.output_root_dir, "exports", self.name,"visualization/")
        self.parameter_set_dir = os.path.join(self.output_root_dir, "exports", self.name,"parameter_set/")

        # 5. build the deployment
        self._build(system_config, package_paths)

    def _merge_list(self, base_list: List[Dict], override_list: List[Dict], key_field: str = None) -> List[Dict]:
        """
        Merge override_list into base_list.
        If key_field is provided, items with matching key_field in override_list replace those in base_list.
        Otherwise, items are appended.
        """
        if not override_list:
            return base_list

        merged_list = [item.copy() for item in base_list]

        if key_field:
            # Create a map for quick lookup and replacement
            base_map = {item[key_field]: i for i, item in enumerate(merged_list) if key_field in item}
            
            for item in override_list:
                key = item.get(key_field)
                if key and key in base_map:
                    # Replace existing item
                    merged_list[base_map[key]] = item
                else:
                    # Append new item
                    merged_list.append(item)
        else:
            # Simple append if no key_field is provided
            merged_list.extend(override_list)

        return merged_list

    def _remove_list(self, target_list: List[Dict], remove_specs: List[Dict], key_field: str = None) -> List[Dict]:
        """
        Remove items from target_list based on remove_specs.
        If key_field is provided, remove items where item[key_field] matches spec[key_field].
        Otherwise, remove items that match all properties in spec.
        """
        if not remove_specs or not target_list:
            return target_list

        result_list = []
        
        # Prepare lookup for key-based removal
        remove_keys = set()
        if key_field:
            for spec in remove_specs:
                if key_field in spec:
                    remove_keys.add(spec[key_field])

        for item in target_list:
            should_remove = False
            if key_field:
                if item.get(key_field) in remove_keys:
                    should_remove = True
            else:
                # Subset match: checks if any spec matches the item
                for spec in remove_specs:
                    # Check if spec is a subset of item
                    if all(item.get(k) == v for k, v in spec.items()):
                        should_remove = True
                        break
            
            if not should_remove:
                result_list.append(item)

        return result_list

    def _cleanup_components_modes(self, system_config: SystemConfig, removed_modes: List[str]):
        """Remove removed modes from components' mode lists.
           If a component was specific to a removed mode and has no modes left, remove the component.
        """
        if not system_config.components:
            return

        components_to_keep = []
        removed_set = set(removed_modes)

        for comp in system_config.components:
            mode_field = comp.get('mode')
            
            # If mode is not specified (None or empty), it applies to all modes.
            # We don't need to change anything, as it will apply to whatever modes remain.
            if not mode_field:
                components_to_keep.append(comp)
                continue
                
            # Normalize to list
            current_modes = mode_field if isinstance(mode_field, list) else [mode_field]
            
            # Check intersection
            if not any(m in removed_set for m in current_modes):
                components_to_keep.append(comp)
                continue
                
            # Filter out removed modes
            new_modes = [m for m in current_modes if m not in removed_set]
            
            if new_modes:
                # Update component with new modes
                comp['mode'] = new_modes
                components_to_keep.append(comp)
            else:
                # Component has no modes left (and it was not "all modes" initially)
                # Drop the component
                logger.info(f"Dropping component '{comp.get('component')}' as all its modes {current_modes} were removed.")
                
        system_config.components = components_to_keep

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
                    logger.warning(
                        f"Manifest '{entry}' has unexpected type for deploy_config_files: {type(files)}; skipping."
                    )
                    continue
                for f in files:
                    file_path = f.get('path') if isinstance(f, dict) else None
                    if file_path and file_path not in system_list:
                        system_list.append(file_path)
                    
                    if file_path and 'package_name' in manifest_yaml:
                        file_package_map[file_path] = manifest_yaml['package_name']

            except Exception as e:
                logger.warning(f"Failed to load manifest {manifest_file}: {e}")
        if not system_list:
            raise ValidationError(f"No system design configuration files collected.")
        return system_list, package_paths, file_package_map

    def _build(self, system_config, package_paths):
        # 2. Determine modes to build
        modes_config = system_config.modes or []
        if modes_config:
            # Build one instance per mode
            mode_names = [m.get('name') for m in modes_config]
            logger.info(f"Building deployment for {len(mode_names)} modes: {mode_names}")
        else:
            # No modes defined - build single instance without mode filtering
            mode_names = [None]
            logger.info(f"Building deployment without mode filtering")

        # 3. Create deployment instance for each mode
        for mode_name in mode_names:
            try:
                mode_suffix = f"_{mode_name}" if mode_name else ""
                instance_name = f"{self.name}{mode_suffix}"
                deploy_instance = DeploymentInstance(instance_name, mode=mode_name)
                
                # Set system with mode filtering
                deploy_instance.set_system(
                    system_config, self.config_registry, mode=mode_name, package_paths=package_paths
                )

                # Store instance
                mode_key = mode_name if mode_name else "default"
                self.deploy_instances[mode_key] = deploy_instance
                logger.info(f"Successfully built deployment instance for mode: {mode_key}")
                
            except Exception as e:
                # try to visualize the system to show error status
                self.visualize()
                raise DeploymentError(f"Error in setting deploy for mode '{mode_name}': {e}")

    def visualize(self):
        # Collect data from all deployment instances
        deploy_data = {}
        for mode_key, deploy_instance in self.deploy_instances.items():
            deploy_data[mode_key] = deploy_instance.collect_instance_data()

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
        for mode_key, deploy_instance in self.deploy_instances.items():
            # Collect data from the system instance
            data = deploy_instance.collect_instance_data()
            
            # Create mode-specific output directory
            mode_monitor_dir = os.path.join(self.system_monitor_dir, mode_key, "component_state_monitor")
            self.generate_by_template(data, topics_template_path, mode_monitor_dir, "topics.yaml")
            
            logger.info(f"Generated system monitor for mode: {mode_key}")


    def generate_build_scripts(self):
        """Generate shell scripts to build necessary packages for each ECU."""
        generate_build_scripts(
            self.deploy_instances,
            self.output_root_dir,
            self.name,
            self.config_yaml_dir,
            self.config_registry.file_package_map
        )


    def generate_launcher(self):
        # Generate launcher files for each mode
        for mode_key, deploy_instance in self.deploy_instances.items():
            # Create mode-specific launcher directory
            mode_launcher_dir = os.path.join(self.launcher_dir, mode_key)
            
            # Generate module launch files
            generate_module_launch_file(deploy_instance, mode_launcher_dir)
            
            logger.info(f"Generated launcher for mode: {mode_key}")

    def generate_parameter_set_template(self):
        """Generate parameter set template using ParameterTemplateGenerator."""
        if not self.deploy_instances:
            raise DeploymentError("Deployment instances are not initialized")
        
        # Generate parameter set template for each mode
        output_paths = {}
        for mode_key, deploy_instance in self.deploy_instances.items():
            # Create mode-specific output directory
            mode_parameter_dir = os.path.join(self.parameter_set_dir, mode_key)
            os.makedirs(mode_parameter_dir, exist_ok=True)
            
            # Initialize template renderer
            renderer = TemplateRenderer()
            
            # Create parameter template generator and generate the template
            generator = ParameterTemplateGenerator(deploy_instance)
            template_name = f"{self.name}_{mode_key}" if mode_key != "default" else self.name
            output_path_list = generator.generate_parameter_set_template(
                template_name,
                renderer,
                mode_parameter_dir
            )

            output_paths[mode_key] = output_path_list
            logger.info(f"Generated {len(output_path_list)} parameter set templates for mode: {mode_key}")
        
        return output_paths
