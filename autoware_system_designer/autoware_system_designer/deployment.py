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
from .builder.parameter_resolver import ParameterResolver
from .parsers.data_validator import entity_name_decode
from .parsers.yaml_parser import yaml_parser
from .exceptions import ValidationError, DeploymentError
from .utils.template_utils import TemplateRenderer
from .utils import generate_build_scripts
from .visualization.visualize_deployment import visualize_deployment

logger = logging.getLogger(__name__)
debug_mode = True

class Deployment:
    def __init__(self, deploy_config: DeploymentConfig ):
        # entity collection
        system_yaml_list, package_paths, file_package_map = self._get_system_list(deploy_config)
        self.config_registry = ConfigRegistry(system_yaml_list, package_paths, file_package_map)

        # detect mode of input file (deployment vs system only)
        logger.info("deployment init Deployment file: %s", deploy_config.deployment_file)
        self.config_yaml_dir = deploy_config.deployment_file
        
        # 1. check the given deploy_config.deployment_file 
        is_inheritance = False
        system_name = None
        self.config_yaml = {}

        if os.path.exists(self.config_yaml_dir):
            # a1. try load the file
            self.config_yaml = yaml_parser.load_config(self.config_yaml_dir)
            is_inheritance = True
            system_name = self.config_yaml["system"]
        else:
             # b. if it is 'system', it is a system config name. nothing to do
             system_name = os.path.basename(self.config_yaml_dir)
        
        system_name, _ = entity_name_decode(system_name)

        # 2. load the system configuration
        system = self.config_registry.get_system(system_name)
        if not system:
            raise ValidationError(f"System not found: {system_name}")

        # If it is system name, config_yaml is empty, load it from system path
        if not self.config_yaml:
             self.config_yaml_dir = str(system.file_path)
             logger.info(f"Resolved system file path from registry: {self.config_yaml_dir}")
             self.config_yaml = yaml_parser.load_config(self.config_yaml_dir)
             
             logger.info("Detected system-only deployment file.")
             if 'system' not in self.config_yaml:
                  self.config_yaml['system'] = self.config_yaml.get('name', system_name)

        self.name = self.config_yaml.get("name")

        # 3. set parameter resolver
        # create parameter resolver for ROS-independent operation
        self.parameter_resolver = ParameterResolver(
            variables=[],
            package_paths=package_paths
        )

        # 4. if it is inheritance, append/override variables and variable files
        if is_inheritance:
            variables = self.config_yaml.get('variables', [])
            variable_map = self.parameter_resolver._build_variable_map(variables)
            self.parameter_resolver.update_variables(variable_map)

            # Process variable files
            if 'variable_files' in self.config_yaml:
                self._load_variable_files(self.config_yaml['variable_files'])

        # member variables - now supports multiple instances (one per mode)
        self.deploy_instances: Dict[str, DeploymentInstance] = {}  # mode_name -> DeploymentInstance

        # 4. set output paths
        self.output_root_dir = deploy_config.output_root_dir
        self.launcher_dir = os.path.join(self.output_root_dir, "exports", self.name, "launcher/")
        self.system_monitor_dir = os.path.join(self.output_root_dir, "exports", self.name, "system_monitor/")
        self.visualization_dir = os.path.join(self.output_root_dir, "exports", self.name,"visualization/")
        self.parameter_set_dir = os.path.join(self.output_root_dir, "exports", self.name,"parameter_set/")

        # 5. build the deployment
        self._build(system)

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

    def _load_variable_files(self, variable_files: List[Dict[str, str]]):
        """Load parameters from external files and update parameter resolver."""
        for file_entry in variable_files:
            for param_prefix, file_path in file_entry.items():
                # Resolve file path
                resolved_path = self.parameter_resolver.resolve_string(file_path)
                
                # Check if it's a find-pkg-share that couldn't be resolved (starts with $)
                if resolved_path.startswith('$'):
                    logger.warning(f"Could not resolve path for global parameter file: {file_path}")
                    continue
                
                if not os.path.exists(resolved_path):
                    logger.warning(f"Global parameter file not found: {resolved_path}")
                    continue
                    
                try:
                    data = yaml_parser.load_config(resolved_path)
                    if not data:
                        continue
                        
                    # Derive prefix from key (e.g., vehicle_info_file -> vehicle_info)
                    prefix = param_prefix.replace('_file', '')
                    
                    variables = {}

                    # Iterate through nodes in the yaml (standard ROS 2 param file structure)
                    # node_name:
                    #   ros__parameters:
                    #     param_name: value
                    for node_name, node_data in data.items():
                        if isinstance(node_data, dict) and "ros__parameters" in node_data:
                            params = node_data["ros__parameters"]
                            flattened = self._flatten_parameters(params, parent_key=prefix)
                            variables.update(flattened)
                        # Handle case where file might be just key-value pairs without node/ros__parameters wrapper
                        elif param_prefix == 'variables' or param_prefix == 'variable_file':
                             # If explicitly global params file, maybe treat differently? 
                             # For now assume ROS 2 param structure or flat if no ros__parameters
                             pass 
                            
                    # Update resolver
                    if variables:
                        self.parameter_resolver.update_variables(variables)
                        logger.info(f"Loaded {len(variables)} global parameters from {resolved_path}")
                    else:
                        logger.warning(f"No parameters found in {resolved_path} (expected standard ROS 2 parameter file format)")
                    
                except Exception as e:
                    logger.warning(f"Failed to load global parameter file {resolved_path}: {e}")

    def _flatten_parameters(self, params: Dict[str, Any], parent_key: str = "", separator: str = ".") -> Dict[str, str]:
        """Flatten nested dictionary into dot-separated keys."""
        items = {}
        for k, v in params.items():
            new_key = f"{parent_key}{separator}{k}" if parent_key else k
            
            if isinstance(v, dict):
                items.update(self._flatten_parameters(v, new_key, separator))
            else:
                items[new_key] = str(v)
        return items

    def _build(self, system):
        # 2. Determine modes to build
        modes_config = system.modes or []
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
                    system, self.config_registry, mode=mode_name, parameter_resolver=self.parameter_resolver
                )
                
                # Resolve parameters (apply global parameters and resolve substitutions)
                variables = self.config_yaml.get('variables', [])
                deploy_instance.resolve_parameters(variables)

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
