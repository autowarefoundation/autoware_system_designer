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

"""Structure and schema linter for autoware_system_design_format files."""

from pathlib import Path
from typing import Dict, Any, List

from ..models.parsing.yaml_parser import yaml_parser
from ..models.parsing.data_validator import ValidatorFactory, entity_name_decode
from ..models.config import ConfigType
from .report import LintResult


class StructureLinter:
    """Linter for structure and schema validation."""
    
    def __init__(self):
        """Initialize the structure linter."""
        self.validator_factory = ValidatorFactory()
    
    def lint(self, file_path: Path, result: LintResult):
        """Lint structure and schema of the YAML file.
        
        Args:
            file_path: Path to the file to lint
            result: LintResult to add errors/warnings to
        """
        try:
            config = yaml_parser.load_config(str(file_path))
        except Exception as e:
            result.add_error(f"Failed to load YAML file: {str(e)}")
            return
        
        # Determine entity type from filename
        file_stem = file_path.stem  # filename without .yaml extension
        try:
            file_entity_name, file_entity_type = entity_name_decode(file_stem)
        except Exception as e:
            result.add_error(f"Invalid file name format: {str(e)}")
            return
        
        # Validate entity name matches filename
        if 'name' not in config:
            result.add_error("Missing required field 'name'")
            return
        
        try:
            entity_name, entity_type = entity_name_decode(config['name'])
            
            # Check entity name matches filename
            if entity_name != file_entity_name:
                result.add_error(
                    f"Entity name '{entity_name}' does not match file name '{file_entity_name}'"
                )
            
            # Check entity type matches file extension
            if entity_type != file_entity_type:
                result.add_error(
                    f"Entity type '{entity_type}' does not match file extension type '{file_entity_type}'"
                )
            
            # Use validator to check structure
            try:
                validator = self.validator_factory.get_validator(entity_type)
                validator.validate_all(config, entity_type, file_entity_type, str(file_path))
            except Exception as e:
                result.add_error(f"Validation error: {str(e)}")
            
            # Additional schema-specific validations
            self._validate_node_schema(config, result)
            self._validate_module_schema(config, result)
            self._validate_system_schema(config, result)
            self._validate_parameter_set_schema(config, result)
            
        except Exception as e:
            result.add_error(f"Error validating entity name: {str(e)}")
    
    def _validate_node_schema(self, config: Dict[str, Any], result: LintResult):
        """Validate node-specific schema."""
        if 'launch' not in config:
            return
        
        launch = config['launch']
        if not isinstance(launch, dict):
            return
        
        # Check that at least one of plugin, executable, or ros2_launch_file is present
        has_plugin = 'plugin' in launch
        has_executable = 'executable' in launch
        has_ros2_launch_file = 'ros2_launch_file' in launch
        
        if not (has_plugin or has_executable or has_ros2_launch_file):
            result.add_error(
                "Launch config must have at least one of: 'plugin', 'executable', or 'ros2_launch_file'"
            )
        
        # Check package is present
        if 'package' not in launch:
            result.add_error("Launch config must have 'package' field")
        
        # Validate container_name if use_container is true
        if launch.get('use_container') is True:
            if 'container_name' not in launch:
                result.add_error(
                    "Launch config must have 'container_name' when 'use_container' is true"
                )
        
        # Validate inputs structure
        if 'inputs' in config and isinstance(config['inputs'], list):
            for idx, input_port in enumerate(config['inputs']):
                if not isinstance(input_port, dict):
                    result.add_error(f"Input port at index {idx} must be a dictionary")
                    continue
                
                if 'name' not in input_port:
                    result.add_error(f"Input port at index {idx} missing 'name' field")
                if 'message_type' not in input_port:
                    result.add_error(f"Input port at index {idx} missing 'message_type' field")
        
        # Validate outputs structure
        if 'outputs' in config and isinstance(config['outputs'], list):
            for idx, output_port in enumerate(config['outputs']):
                if not isinstance(output_port, dict):
                    result.add_error(f"Output port at index {idx} must be a dictionary")
                    continue
                
                if 'name' not in output_port:
                    result.add_error(f"Output port at index {idx} missing 'name' field")
                if 'message_type' not in output_port:
                    result.add_error(f"Output port at index {idx} missing 'message_type' field")
        
        # Validate processes structure
        if 'processes' in config and isinstance(config['processes'], list):
            for idx, process in enumerate(config['processes']):
                if not isinstance(process, dict):
                    result.add_error(f"Process at index {idx} must be a dictionary")
                    continue
                
                if 'name' not in process:
                    result.add_error(f"Process at index {idx} missing 'name' field")
                if 'trigger_conditions' not in process:
                    result.add_error(f"Process at index {idx} missing 'trigger_conditions' field")
                if 'outcomes' not in process:
                    result.add_error(f"Process at index {idx} missing 'outcomes' field")

        # Validate variant override/remove blocks for node configs
        self._validate_node_variant_blocks(config, result)
    
    def _validate_module_schema(self, config: Dict[str, Any], result: LintResult):
        """Validate module-specific schema."""
        # Validate instances structure
        if 'instances' in config and isinstance(config['instances'], list):
            for idx, instance in enumerate(config['instances']):
                if not isinstance(instance, dict):
                    result.add_error(f"Instance at index {idx} must be a dictionary")
                    continue
                
                if 'name' not in instance:
                    result.add_error(f"Instance at index {idx} missing 'name' field")
                if 'entity' not in instance:
                    result.add_error(f"Instance at index {idx} missing 'entity' field")
        
        # Validate external_interfaces structure
        if 'external_interfaces' in config:
            ext_interfaces = config['external_interfaces']
            if isinstance(ext_interfaces, dict):
                # Validate input list
                if 'input' in ext_interfaces:
                    if not isinstance(ext_interfaces['input'], list):
                        result.add_error("External interfaces 'input' must be a list")
                    else:
                        for idx, ext_input in enumerate(ext_interfaces['input']):
                            if isinstance(ext_input, dict) and 'name' not in ext_input:
                                result.add_error(f"External input at index {idx} missing 'name' field")
                
                # Validate output list
                if 'output' in ext_interfaces:
                    if not isinstance(ext_interfaces['output'], list):
                        result.add_error("External interfaces 'output' must be a list")
                    else:
                        for idx, ext_output in enumerate(ext_interfaces['output']):
                            if isinstance(ext_output, dict) and 'name' not in ext_output:
                                result.add_error(f"External output at index {idx} missing 'name' field")
        
        # Validate connections structure
        if 'connections' in config and isinstance(config['connections'], list):
            for idx, connection in enumerate(config['connections']):
                if not isinstance(connection, dict):
                    result.add_error(f"Connection at index {idx} must be a dictionary")
                    continue
                
                if 'from' not in connection:
                    result.add_error(f"Connection at index {idx} missing 'from' field")
                if 'to' not in connection:
                    result.add_error(f"Connection at index {idx} missing 'to' field")

        # Validate variant override/remove blocks for module configs
        self._validate_module_variant_blocks(config, result)
    
    def _validate_system_schema(self, config: Dict[str, Any], result: LintResult):
        """Validate system-specific schema."""
        # Validate components structure
        if 'components' in config and isinstance(config['components'], list):
            for idx, component in enumerate(config['components']):
                if not isinstance(component, dict):
                    result.add_error(f"Component at index {idx} must be a dictionary")
                    continue
                
                if 'name' not in component:
                    result.add_error(f"Component at index {idx} missing 'name' field")
                if 'entity' not in component:
                    result.add_error(f"Component at index {idx} missing 'entity' field")
        
        # Validate connections structure (same as module)
        if 'connections' in config and isinstance(config['connections'], list):
            for idx, connection in enumerate(config['connections']):
                if not isinstance(connection, dict):
                    result.add_error(f"Connection at index {idx} must be a dictionary")
                    continue
                
                if 'from' not in connection:
                    result.add_error(f"Connection at index {idx} missing 'from' field")
                if 'to' not in connection:
                    result.add_error(f"Connection at index {idx} missing 'to' field")

        # Validate variant override/remove blocks for system configs
        self._validate_system_variant_blocks(config, result)
    
    def _validate_parameter_set_schema(self, config: Dict[str, Any], result: LintResult):
        """Validate parameter set-specific schema."""
        # Parameters can be dict or list, both are valid
        # Additional validation can be added here if needed
        pass

    def _validate_node_variant_blocks(self, config: Dict[str, Any], result: LintResult):
        """Validate node variant override/remove blocks."""
        override = config.get('override')
        if override is not None:
            if not isinstance(override, dict):
                result.add_error("'override' must be a dictionary in variant config")
            else:
                if 'launch' in override and not isinstance(override['launch'], dict):
                    result.add_error("Override 'launch' must be a dictionary")
                self._validate_list_block(override, 'inputs', result, required_keys=['name'],
                                          label_prefix="Override input")
                self._validate_list_block(override, 'outputs', result, required_keys=['name'],
                                          label_prefix="Override output")
                self._validate_list_block(override, 'parameter_files', result, required_keys=['name'],
                                          label_prefix="Override parameter file")
                self._validate_list_block(override, 'parameters', result, required_keys=['name'],
                                          label_prefix="Override parameter")
                self._validate_list_block(override, 'processes', result, required_keys=['name'],
                                          label_prefix="Override process")

        remove = config.get('remove')
        if remove is not None:
            if not isinstance(remove, dict):
                result.add_error("'remove' must be a dictionary in variant config")
            else:
                self._validate_list_block(remove, 'inputs', result, required_keys=['name'],
                                          label_prefix="Remove input")
                self._validate_list_block(remove, 'outputs', result, required_keys=['name'],
                                          label_prefix="Remove output")
                self._validate_list_block(remove, 'parameter_files', result, required_keys=['name'],
                                          label_prefix="Remove parameter file")
                self._validate_list_block(remove, 'parameters', result, required_keys=['name'],
                                          label_prefix="Remove parameter")
                self._validate_list_block(remove, 'processes', result, required_keys=['name'],
                                          label_prefix="Remove process")

    def _validate_module_variant_blocks(self, config: Dict[str, Any], result: LintResult):
        """Validate module variant override/remove blocks."""
        override = config.get('override')
        if override is not None:
            if not isinstance(override, dict):
                result.add_error("'override' must be a dictionary in variant config")
            else:
                self._validate_list_block(override, 'instances', result, required_keys=['name'],
                                          label_prefix="Override instance")
                self._validate_list_block(override, 'connections', result, required_keys=None,
                                          label_prefix="Override connection")
                self._validate_external_interfaces_block(override.get('external_interfaces'), result, "Override")

        remove = config.get('remove')
        if remove is not None:
            if not isinstance(remove, dict):
                result.add_error("'remove' must be a dictionary in variant config")
            else:
                self._validate_list_block(remove, 'instances', result, required_keys=['name'],
                                          label_prefix="Remove instance")
                self._validate_list_block(remove, 'connections', result, required_keys=None,
                                          label_prefix="Remove connection")
                self._validate_external_interfaces_block(remove.get('external_interfaces'), result, "Remove")

    def _validate_system_variant_blocks(self, config: Dict[str, Any], result: LintResult):
        """Validate system variant override/remove blocks."""
        override = config.get('override')
        if override is not None:
            if not isinstance(override, dict):
                result.add_error("'override' must be a dictionary in variant config")
            else:
                self._validate_list_block(override, 'variables', result, required_keys=['name'],
                                          label_prefix="Override variable")
                self._validate_list_block(override, 'variable_files', result, required_keys=['name', 'value'],
                                          label_prefix="Override variable file")
                self._validate_list_block(override, 'modes', result, required_keys=['name'],
                                          label_prefix="Override mode")
                self._validate_list_block(override, 'parameter_sets', result, required_keys=None,
                                          label_prefix="Override parameter set")
                self._validate_list_block(override, 'components', result, required_keys=['name'],
                                          label_prefix="Override component")
                self._validate_list_block(override, 'connections', result, required_keys=None,
                                          label_prefix="Override connection")

        remove = config.get('remove')
        if remove is not None:
            if not isinstance(remove, dict):
                result.add_error("'remove' must be a dictionary in variant config")
            else:
                self._validate_list_block(remove, 'modes', result, required_keys=['name'],
                                          label_prefix="Remove mode")
                self._validate_list_block(remove, 'parameter_sets', result, required_keys=None,
                                          label_prefix="Remove parameter set")
                self._validate_list_block(remove, 'components', result, required_keys=['name'],
                                          label_prefix="Remove component")
                self._validate_list_block(remove, 'variables', result, required_keys=['name'],
                                          label_prefix="Remove variable")
                self._validate_list_block(remove, 'connections', result, required_keys=None,
                                          label_prefix="Remove connection")

    def _validate_list_block(
        self,
        container: Dict[str, Any],
        field: str,
        result: LintResult,
        required_keys: List[str] = None,
        label_prefix: str = "Item",
    ):
        """Validate a list field inside a container dict."""
        if field not in container:
            return

        value = container[field]
        if not isinstance(value, list):
            result.add_error(f"{label_prefix} list '{field}' must be a list")
            return

        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                result.add_error(f"{label_prefix} at index {idx} must be a dictionary")
                continue

            if required_keys:
                for key in required_keys:
                    if key not in item:
                        result.add_error(f"{label_prefix} at index {idx} missing '{key}' field")

    def _validate_external_interfaces_block(
        self,
        external_interfaces: Any,
        result: LintResult,
        label_prefix: str,
    ):
        """Validate external_interfaces block when present."""
        if external_interfaces is None:
            return

        if not isinstance(external_interfaces, dict):
            result.add_error(f"{label_prefix} external_interfaces must be a dictionary")
            return

        for interface_type in ['input', 'output']:
            if interface_type in external_interfaces:
                entries = external_interfaces[interface_type]
                if not isinstance(entries, list):
                    result.add_error(
                        f"{label_prefix} external_interfaces '{interface_type}' must be a list"
                    )
                    continue
                for idx, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        result.add_error(
                            f"{label_prefix} external_interfaces '{interface_type}' at index {idx} must be a dictionary"
                        )
                        continue
                    if 'name' not in entry:
                        result.add_error(
                            f"{label_prefix} external_interfaces '{interface_type}' at index {idx} missing 'name' field"
                        )

