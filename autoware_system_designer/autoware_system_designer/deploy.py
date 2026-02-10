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
from pathlib import Path
from typing import Dict, Tuple, List, Any, Optional
from .deployment.deployment_config import DeploymentConfig
from .deployment._deploy_variant_launchers import _generate_deploy_variant_launchers
from .deployment._input_resolution import _resolve_input_target
from .deployment._mode_selection import _select_modes
from .deployment._modes import _apply_mode_configuration
from .deployment._system_structure import _iter_mode_payload_and_data
from .builder.config.config_registry import ConfigRegistry
from .builder.deployment_instance import DeploymentInstance
from .ros2_launcher.generate_module_launcher import generate_module_launch_file
from .template.parameter_template_generator import ParameterTemplateGenerator
from .models.parsing.yaml_parser import yaml_parser
from .exceptions import ValidationError, DeploymentError
from .file_io.template_renderer import TemplateRenderer
from .file_io.system_structure_json import (
    save_system_structure,
    save_system_structure_snapshot,
)
from .utils import generate_build_scripts
from .visualization.visualize_deployment import visualize_deployment
from .models.config import SystemConfig, NodeConfig
from .file_io.source_location import SourceLocation, format_source

logger = logging.getLogger(__name__)


class Deployment:
    def __init__(self, deploy_config: DeploymentConfig ):
        # entity collection
        system_yaml_list, package_paths, file_package_map = self._get_system_list(deploy_config)
        self.config_registry = ConfigRegistry(
            system_yaml_list,
            package_paths,
            file_package_map,
            workspace_config=deploy_config.workspace_config,
        )
        deployment_file_abs = str(Path(deploy_config.deployment_file).resolve())
        self.config_registry.deployment_package_name = file_package_map.get(deployment_file_abs)

        # detect mode of input file (deployment vs system only)
        logger.info("deployment init Deployment file: %s", deploy_config.deployment_file)
        
        input_path = deploy_config.deployment_file
        self.deploy_variants: List[Dict[str, Any]] = []
        self.deployment_table_path: Optional[str] = None
        self.system_argument_variables: List[str] = []

        system_config, self.deploy_variants, self.deployment_table_path = _resolve_input_target(
            input_path, self.config_registry
        )
        if not system_config:
            raise ValidationError(f"System not found from input: {input_path}")

        # Fallback for deployments-table mode where deployment_file itself is not an entity file.
        if self.config_registry.deployment_package_name is None:
            system_file_abs = str(Path(system_config.file_path).resolve())
            self.config_registry.deployment_package_name = file_package_map.get(system_file_abs)
        
        self.config_yaml_dir = str(system_config.file_path)
        logger.info(f"Resolved system file path from registry: {self.config_yaml_dir}")
                
        self.name = system_config.name
        self.system_argument_variables = self._collect_system_argument_names(system_config)
        self.deployment_package_path = str(Path(deploy_config.output_root_dir).resolve())

        # mode identifiers
        self.mode_keys: List[str] = []

        # set output paths
        self.output_root_dir = deploy_config.output_root_dir
        self.launcher_dir = os.path.join(self.output_root_dir, "exports", self.name, "launcher/")
        self.system_monitor_dir = os.path.join(self.output_root_dir, "exports", self.name, "system_monitor/")
        self.visualization_dir = os.path.join(self.output_root_dir, "exports", self.name,"visualization/")
        self.parameter_set_dir = os.path.join(self.output_root_dir, "exports", self.name,"parameter_set/")
        self.system_structure_dir = os.path.join(self.output_root_dir, "exports", self.name, "system_structure/")
        self.system_structure_snapshots: Dict[str, Dict[str, Any]] = {}

        # build the deployment
        self._build(system_config, package_paths)

    def _collect_deploy_variable_names(self) -> List[str]:
        variable_names: List[str] = []
        seen = set()

        # 1) System arguments are treated as required launch arguments.
        for name in self.system_argument_variables:
            if name not in seen:
                seen.add(name)
                variable_names.append(name)

        # 2) Deploy-list variables are also forwarded.
        for deploy_item in self.deploy_variants:
            for argument in deploy_item.get("arguments", deploy_item.get("variables", [])):
                if not isinstance(argument, dict):
                    continue
                name = argument.get("name")
                if not isinstance(name, str) or not name or name in seen:
                    continue
                seen.add(name)
                variable_names.append(name)
        return variable_names

    def _collect_system_argument_names(self, system_config: SystemConfig) -> List[str]:
        result: List[str] = []
        seen = set()
        for argument in system_config.arguments or []:
            if not isinstance(argument, dict):
                continue
            name = argument.get("name")
            if not isinstance(name, str) or not name:
                continue
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

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
        mode_names, default_mode = _select_modes(system_config)
        if system_config.modes:
            logger.info(
                f"Building deployment for {len(mode_names)} modes: {mode_names}, default: {default_mode}"
            )
        else:
            logger.info("Building deployment with single 'default' mode")

        # Create deployment instance for each mode
        self.mode_keys = []
        for mode_name in mode_names:
            mode_key = mode_name if mode_name else default_mode
            snapshot_store: Dict[str, Any] = {}
            try:
                # Apply mode configuration on top of base system
                mode_system_config = _apply_mode_configuration(system_config, mode_name)

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
                details = []
                if mode_key == default_mode:
                    details.append("default")
                system_path = getattr(system_config, "file_path", None)
                if system_path:
                    details.append(f"system= {system_path} ")
                details_str = f" ({', '.join(details)})" if details else ""

                hint = (
                    "Hint: top-level 'connections' apply to all modes; "
                    "use '<Mode>.override.connections' or '<Mode>.remove.connections' for mode-specific wiring."
                )

                # If any design files had a newer minor format version,
                # surface this so the user knows it may be related.
                mismatch_files = getattr(self.config_registry, "minor_version_mismatch_files", [])
                if mismatch_files:
                    from .builder.config.config_registry import _format_mismatch_hint
                    hint += "\n" + _format_mismatch_hint(mismatch_files)

                raise DeploymentError(
                    f"Error while building deploy for mode '{mode_key}'{details_str}: {e}\n{hint}"
                ) from e

    def visualize(self):
        # Collect data from all deployment instances
        deploy_data = {
            mode_key: data
            for mode_key, _payload, data in _iter_mode_payload_and_data(
                self.mode_keys, self.system_structure_dir
            )
        }

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
        for mode_key, _payload, data in _iter_mode_payload_and_data(
            self.mode_keys, self.system_structure_dir
        ):
            # Create mode-specific output directory
            mode_monitor_dir = os.path.join(self.system_monitor_dir, mode_key, "component_state_monitor")
            self.generate_by_template(data, topics_template_path, mode_monitor_dir, "topics.yaml")
            
            logger.info(f"Generated system monitor for mode: {mode_key}")


    def generate_build_scripts(self):
        """Generate shell scripts to build necessary packages for each ECU."""
        deploy_data = {
            mode_key: data
            for mode_key, _payload, data in _iter_mode_payload_and_data(
                self.mode_keys, self.system_structure_dir
            )
        }

        package_resolution_by_name: Dict[str, str | None] = {}
        packages_without_provider: set[str] = set()
        for entity in self.config_registry.entities.values():
            if not isinstance(entity, NodeConfig):
                continue
            pkg_name = entity.package_name
            if not pkg_name:
                continue
            if not entity.package_provider:
                packages_without_provider.add(pkg_name)
                continue
            resolution = entity.package_resolution
            if resolution is None:
                package_resolution_by_name.setdefault(pkg_name, None)
                continue
            existing = package_resolution_by_name.get(pkg_name)
            if existing != "source":
                package_resolution_by_name[pkg_name] = resolution

        generate_build_scripts(
            deploy_data,
            self.output_root_dir,
            self.name,
            self.config_yaml_dir,
            self.config_registry.file_package_map,
            package_resolution_by_name=package_resolution_by_name,
            packages_without_provider=packages_without_provider,
        )


    def generate_launcher(self):
        deploy_variable_names = self._collect_deploy_variable_names()
        # Generate launcher files for each mode
        for mode_key, payload, _data in _iter_mode_payload_and_data(
            self.mode_keys, self.system_structure_dir
        ):
            # Create mode-specific launcher directory
            mode_launcher_dir = os.path.join(self.launcher_dir, mode_key)
            
            # Generate module launch files from JSON structure
            generate_module_launch_file(
                payload,
                mode_launcher_dir,
                forward_args=deploy_variable_names,
            )
            
            logger.info(f"Generated launcher for mode: {mode_key}")

        if self.deploy_variants:
            _generate_deploy_variant_launchers(
                mode_keys=self.mode_keys,
                system_structure_dir=self.system_structure_dir,
                launcher_dir=self.launcher_dir,
                deployment_package_path=self.deployment_package_path,
                system_name=self.name,
                deploy_variants=self.deploy_variants,
            )

    def generate_parameter_set_template(self):
        """Generate parameter set template using ParameterTemplateGenerator."""
        if not self.mode_keys:
            raise DeploymentError("Deployment instances are not initialized")
        
        # Generate parameter set template for each mode
        output_paths = {}
        for mode_key, _payload, data in _iter_mode_payload_and_data(
            self.mode_keys, self.system_structure_dir
        ):
            # Create mode-specific output directory
            mode_parameter_dir = os.path.join(self.parameter_set_dir, mode_key)
            os.makedirs(mode_parameter_dir, exist_ok=True)
            
            # Initialize template renderer
            renderer = TemplateRenderer()
            
            # Create parameter template generator and generate the template
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
