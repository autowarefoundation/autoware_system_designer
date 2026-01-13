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
from typing import Dict, Any

from ..parsers.yaml_parser import yaml_parser
from ..parsers.data_validator import ValidatorFactory, entity_name_decode
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
    
    def _validate_module_schema(self, config: Dict[str, Any], result: LintResult):
        """Validate module-specific schema."""
        # Validate instances structure
        if 'instances' in config and isinstance(config['instances'], list):
            for idx, instance in enumerate(config['instances']):
                if not isinstance(instance, dict):
                    result.add_error(f"Instance at index {idx} must be a dictionary")
                    continue
                
                if 'instance' not in instance:
                    result.add_error(f"Instance at index {idx} missing 'instance' field")
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
    
    def _validate_system_schema(self, config: Dict[str, Any], result: LintResult):
        """Validate system-specific schema."""
        # Validate components structure
        if 'components' in config and isinstance(config['components'], list):
            for idx, component in enumerate(config['components']):
                if not isinstance(component, dict):
                    result.add_error(f"Component at index {idx} must be a dictionary")
                    continue
                
                if 'component' not in component:
                    result.add_error(f"Component at index {idx} missing 'component' field")
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
    
    def _validate_parameter_set_schema(self, config: Dict[str, Any], result: LintResult):
        """Validate parameter set-specific schema."""
        # Parameters can be dict or list, both are valid
        # Additional validation can be added here if needed
        pass

