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

from typing import Dict, Any
from pathlib import Path
import logging

from ..parsers.yaml_parser import yaml_parser
from .data_validator import ValidatorFactory, entity_name_decode
from ..models.config import Config, NodeConfig, ModuleConfig, ParameterSetConfig, SystemConfig, ConfigType, ConfigSubType
from ..exceptions import ValidationError

logger = logging.getLogger(__name__)

class ConfigParser:
    """Parser for entity configuration files."""
    
    def __init__(self, strict_mode: bool = True):
        self.validator_factory = ValidatorFactory()
        self.strict_mode = strict_mode
    
    def parse_entity_file(self, config_yaml_path: str) -> Config:
        """Parse an entity configuration file."""
        file_path = Path(config_yaml_path)
        # get entity type from file name 
        # file/path/to/<entity_name>.<entity_type>.yaml
        file_entity_name, file_entity_type = entity_name_decode(file_path.stem)

        # Load configuration
        config = self._load_config(file_path)
        
        # Parse entity name and type
        full_name = config.get("name")
        entity_name, entity_type = entity_name_decode(full_name)

        if entity_name != file_entity_name:
            msg = f"Config name '{entity_name}' does not match file name '{file_entity_name}'. File: {file_path}"
            if self.strict_mode:
                raise ValidationError(msg)
            else:
                logger.warning(msg)
        
        # Validate configuration
        validator = self.validator_factory.get_validator(entity_type)
        validator.validate_all(config, entity_type, file_entity_type, str(file_path))
        
        # Create appropriate data structure
        return self._create_entity_data(entity_name, full_name, entity_type, config, file_path)
    
    def parse_entity_from_content(self, content: str, config_yaml_path: str) -> Config:
        """Parse an entity configuration from string content."""
        file_path = Path(config_yaml_path)
        # get entity type from file name 
        # file/path/to/<entity_name>.<entity_type>.yaml
        file_entity_name, file_entity_type = entity_name_decode(file_path.stem)

        # Load configuration from string
        try:
            config = yaml_parser.load_config_from_string(content)
        except Exception as e:
            logger.error(f"Failed to parse content: {e}")
            raise ValidationError(f"Error parsing YAML content: {e}")
        
        # Parse entity name and type
        full_name = config.get("name")
        if not full_name:
            # If name is missing, we can't fully validate entity matching, but proceed with file info
            # This allows for partial validation of incomplete files
            entity_name = file_entity_name
            entity_type = file_entity_type
        else:
            entity_name, entity_type = entity_name_decode(full_name)

        if full_name and entity_name != file_entity_name:
            msg = f"Config name '{entity_name}' does not match file name '{file_entity_name}'. File: {file_path}"
            if self.strict_mode:
                raise ValidationError(msg)
            else:
                logger.warning(msg)
        
        # Validate configuration
        # For in-memory validation, we might want to skip deep validation if basic structure is invalid
        if full_name:
            validator = self.validator_factory.get_validator(entity_type)
            # Use the file path for reference even if content is from memory
            validator.validate_all(config, entity_type, file_entity_type, str(file_path))
        
        # Create appropriate data structure
        return self._create_entity_data(entity_name, full_name or f"{entity_name}.{entity_type}", entity_type, config, file_path)
    
    def _load_config(self, file_path: Path) -> Dict[str, Any]:
        """Load YAML configuration file."""
        try:
            return yaml_parser.load_config(str(file_path))
        except Exception as e:
            logger.error(f"Failed to load config from {file_path}: {e}")
            raise ValidationError(f"Error parsing YAML file {file_path}: {e}")

    def _create_entity_data(self, entity_name: str, full_name: str, entity_type: str, 
                           config: Dict[str, Any], file_path: Path) -> Config:
        """Create appropriate data structure based on entity type."""
        base_data = {
            'name': entity_name,
            'full_name': full_name,
            'entity_type': entity_type,
            'config': config,
            'file_path': file_path
        }
        
        if entity_type == ConfigType.NODE:
            # Initialize parameter values from defaults
            parameters = config.get('parameters', [])
            if parameters:
                for param in parameters:
                    if 'default' in param and 'value' not in param:
                        param['value'] = param['default']

            sub_type = ConfigSubType.INHERITANCE if "inheritance" in config else ConfigSubType.BASE
            return NodeConfig(
                **base_data,
                sub_type=sub_type,
                launch=config.get('launch'),
                inputs=config.get('inputs'),
                outputs=config.get('outputs'),
                parameter_files=config.get('parameter_files'),
                parameters=parameters,
                processes=config.get('processes')
            )
        elif entity_type == ConfigType.MODULE:
            return ModuleConfig(
                **base_data,
                instances=config.get('instances'),
                external_interfaces=config.get('external_interfaces'),
                connections=config.get('connections')
            )
        elif entity_type == ConfigType.PARAMETER_SET:
            return ParameterSetConfig(
                **base_data,
                parameters=config.get('parameters'),
                local_variables=config.get('local_variables')
            )
        elif entity_type == ConfigType.SYSTEM:
            sub_type = ConfigSubType.INHERITANCE if "inheritance" in config else ConfigSubType.BASE
            return SystemConfig(
                **base_data,
                sub_type=sub_type,
                modes=config.get('modes'),
                components=config.get('components'),
                connections=config.get('connections'),
                variables=config.get('variables'),
                variable_files=config.get('variable_files')
            )
        else:
            raise ValidationError(f"Unknown entity type: {entity_type}")