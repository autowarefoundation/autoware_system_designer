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
from typing import Dict, Tuple, List
from .config import SystemConfig
from .models.config import Config
from .models.parameters import ParameterType
from .builder.config_registry import ConfigRegistry
from .builder.instances import DeploymentInstance
from .builder.launcher_generator import generate_module_launch_file
from .builder.parameter_template_generator import ParameterTemplateGenerator
from .builder.parameter_resolver import ParameterResolver
from .parsers.data_validator import entity_name_decode
from .parsers.yaml_parser import yaml_parser
from .exceptions import ValidationError
from .utils.template_utils import TemplateRenderer
from .utils import generate_build_scripts
from .visualization.visualize_deployment import visualize_deployment

logger = logging.getLogger(__name__)
debug_mode = True

class Deployment:
    def __init__(self, system_config: SystemConfig ):
        # entity collection
        system_yaml_list, package_paths, file_package_map = self._get_system_list(system_config)
        self.config_registry = ConfigRegistry(system_yaml_list, package_paths, file_package_map)

        # detect mode of input file (deployment vs system only)
        # if deployment_file ends with .system, it's a system-only file
        logger.info("deployment init Deployment file: %s", system_config.deployment_file)
        if system_config.deployment_file.endswith(".system"):
            logger.info("Detected system-only deployment file.")
            # need to parse the absolute path of the file from the config_registry
            # generate deployment config in-memory
            self.config_yaml_dir = system_config.deployment_file
            self.config_yaml = {}
            self.config_yaml['system'] = system_config.deployment_file
            self.config_yaml['name'] = system_config.deployment_file
            self.config_yaml.setdefault('global_parameters', [])
            self.config_yaml.setdefault('environment_parameters', [])
            self.name = self.config_yaml.get("name")

        else:
            # input is a deployment file
            self.config_yaml_dir = system_config.deployment_file
            self.config_yaml = yaml_parser.load_config(self.config_yaml_dir)
            self.name = self.config_yaml.get("name")

        # create parameter resolver for ROS-independent operation
        self.parameter_resolver = ParameterResolver(
            global_params=self.config_yaml.get('global_parameters', []),
            env_params=self.config_yaml.get('environment_parameters', []),
            package_paths=package_paths
        )

        # Check the configuration
        self._check_config()

        # member variables - now supports multiple instances (one per mode)
        self.deploy_instances: Dict[str, DeploymentInstance] = {}  # mode_name -> DeploymentInstance
        self.global_parameters_yaml = None
        self.sensor_calibration_yaml = None
        self.map_yaml = None

        # output paths
        self.output_root_dir = system_config.output_root_dir
        self.launcher_dir = os.path.join(self.output_root_dir, "exports", self.name, "launcher/")
        self.system_monitor_dir = os.path.join(self.output_root_dir, "exports", self.name, "system_monitor/")
        self.visualization_dir = os.path.join(self.output_root_dir, "exports", self.name,"visualization/")
        self.parameter_set_dir = os.path.join(self.output_root_dir, "exports", self.name,"parameter_set/")

        # build the deployment
        self.build()

        # resolve parameter variables


    def _get_system_list(self, system_config: SystemConfig) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
        system_list: list[str] = []
        package_paths: Dict[str, str] = {}
        file_package_map: Dict[str, str] = {}
        manifest_dir = system_config.manifest_dir
        if not os.path.isdir(manifest_dir):
            raise ValidationError(f"Architecture manifest directory not found or not a directory: {manifest_dir}")

        for entry in sorted(os.listdir(manifest_dir)):
            if not entry.endswith('.yaml'):
                continue
            manifest_file = os.path.join(manifest_dir, entry)
            try:
                manifest_yaml = yaml_parser.load_config(manifest_file)
                
                # Get package path if available
                if 'package_path' in manifest_yaml:
                    package_name = manifest_yaml.get('package_name', os.path.splitext(entry)[0])
                    package_paths[package_name] = manifest_yaml['package_path']

                files = manifest_yaml.get('system_config_files')
                # Allow the field to be empty or null without raising an error
                if files in (None, []):
                    logger.debug(
                        f"Manifest '{entry}' has empty system_config_files; skipping."
                    )
                    continue
                if not isinstance(files, list):
                    logger.warning(
                        f"Manifest '{entry}' has unexpected type for system_config_files: {type(files)}; skipping."
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
            raise ValidationError(f"No architecture configuration files collected.")
        return system_list, package_paths, file_package_map

    def _check_config(self) -> bool:
        """Validate & normalize deployment configuration.

        Two supported input forms:
        1. Deployment YAML (fields: name, system, global_parameters, environment_parameters)
        2. Raw System YAML (only 'name' ending with '.system'). We synthesize a minimal
           deployment in-memory (no vehicles / environment parameters) so downstream logic works.
        """
        # Validate required fields now present
        for field in ['name', 'system']:
            if field not in self.config_yaml:
                raise ValidationError(
                    f"Field '{field}' is required in deployment configuration file {self.config_yaml_dir}"
                )

        # Optional lists: default to empty if omitted
        if 'global_parameters' not in self.config_yaml:
            self.config_yaml['global_parameters'] = []
        if 'environment_parameters' not in self.config_yaml:
            self.config_yaml['environment_parameters'] = []

        return True

    def build(self):
        # 1. Get system configuration
        system_name, _ = entity_name_decode(self.config_yaml.get("system"))
        system = self.config_registry.get_system(system_name)

        if not system:
            raise ValidationError(f"System not found: {system_name}")

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
                
                # Apply global parameters to all nodes in the deployment instance
                global_params = self.config_yaml.get('global_parameters', [])
                deploy_instance.apply_global_parameters(global_params)

                # Store instance
                mode_key = mode_name if mode_name else "default"
                self.deploy_instances[mode_key] = deploy_instance
                logger.info(f"Successfully built deployment instance for mode: {mode_key}")
                
            except Exception as e:
                # try to visualize the system to show error status
                self.visualize()
                raise ValidationError(f"Error in setting deploy for mode '{mode_name}': {e}")

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
        template_dir = os.path.join(os.path.dirname(__file__), "../template")
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
            raise ValidationError("Deployment instances are not initialized")
        
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

