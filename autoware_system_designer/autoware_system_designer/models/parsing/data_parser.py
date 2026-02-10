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

from .yaml_parser import yaml_parser
from .data_validator import ValidatorFactory, entity_name_decode
from ..config import Config, NodeConfig, ModuleConfig, ParameterSetConfig, SystemConfig, ConfigType, ConfigSubType
from ...exceptions import ValidationError
from ...file_io.source_location import SourceLocation, lookup_source, format_source
from ...utils.parameter_types import normalize_type_name, coerce_numeric_value

logger = logging.getLogger(__name__)

def _build_source_location(
    file_path: Path,
    source_map: Dict[str, Dict[str, int]] | None,
    yaml_path: str,
) -> SourceLocation:
    loc = lookup_source(source_map, yaml_path)
    return SourceLocation(
        file_path=file_path,
        yaml_path=loc.yaml_path,
        line=loc.line,
        column=loc.column,
    )


def _coerce_param_value(
    value: Any,
    *,
    param_type: Any,
    file_path: Path,
    source_map: Dict[str, Dict[str, int]] | None,
    yaml_path: str,
) -> Any:
    type_name = normalize_type_name(param_type)
    if not type_name:
        return value
    try:
        return coerce_numeric_value(value, type_name)
    except ValueError as exc:
        src = _build_source_location(file_path, source_map, yaml_path)
        raise ValidationError(f"{exc}{format_source(src)}") from exc


def _normalize_param_list(
    parameters: Any,
    *,
    file_path: Path,
    source_map: Dict[str, Dict[str, int]] | None,
    base_path: str,
) -> None:
    if not isinstance(parameters, list):
        return

    for idx, param in enumerate(parameters):
        if not isinstance(param, dict):
            continue
        param_type = param.get("type")
        if param_type is None:
            continue
        for key in ("default", "value"):
            if key in param:
                param[key] = _coerce_param_value(
                    param[key],
                    param_type=param_type,
                    file_path=file_path,
                    source_map=source_map,
                    yaml_path=f"{base_path}/{idx}/{key}",
                )

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

        # Load configuration (+ source map for better diagnostics)
        config, source_map = self._load_config_with_source(file_path)
        
        # Parse entity name and type
        full_name = config.get("name")
        entity_name, entity_type = entity_name_decode(full_name)

        if entity_name != file_entity_name:
            name_loc = lookup_source(source_map, "/name")
            name_src = SourceLocation(
                file_path=file_path,
                yaml_path=name_loc.yaml_path,
                line=name_loc.line,
                column=name_loc.column,
            )
            msg = (
                f"Config name '{entity_name}' does not match file name '{file_entity_name}'."
                f"{format_source(name_src)}"
            )
            if self.strict_mode:
                raise ValidationError(msg)
            else:
                logger.warning(msg)
        
        # Validate configuration
        validator = self.validator_factory.get_validator(entity_type)
        validator.validate_all(config, entity_type, file_entity_type, str(file_path))
        
        # Create appropriate data structure
        return self._create_entity_data(entity_name, full_name, entity_type, config, file_path, source_map)
    
    def parse_entity_from_content(self, content: str, config_yaml_path: str) -> Config:
        """Parse an entity configuration from string content."""
        file_path = Path(config_yaml_path)
        # get entity type from file name 
        # file/path/to/<entity_name>.<entity_type>.yaml
        file_entity_name, file_entity_type = entity_name_decode(file_path.stem)

        # Load configuration from string (+ source map for better diagnostics)
        try:
            config, source_map = yaml_parser.load_config_from_string_with_source(content)
        except Exception as e:
            logger.error(f"Failed to parse content for {file_path}: {e}")
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
            name_loc = lookup_source(source_map, "/name")
            name_src = SourceLocation(
                file_path=file_path,
                yaml_path=name_loc.yaml_path,
                line=name_loc.line,
                column=name_loc.column,
            )
            msg = (
                f"Config name '{entity_name}' does not match file name '{file_entity_name}'."
                f"{format_source(name_src)}"
            )
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
        return self._create_entity_data(
            entity_name,
            full_name or f"{entity_name}.{entity_type}",
            entity_type,
            config,
            file_path,
            source_map,
        )
    
    def _load_config_with_source(self, file_path: Path) -> tuple[Dict[str, Any], Dict[str, Dict[str, int]]]:
        """Load YAML configuration file and return (config, source_map)."""
        try:
            return yaml_parser.load_config_with_source(str(file_path))
        except Exception as e:
            logger.error(f"Failed to load config from {file_path}: {e}")
            raise ValidationError(f"Error parsing YAML file {file_path}: {e}")

    def _create_entity_data(self, entity_name: str, full_name: str, entity_type: str, 
                           config: Dict[str, Any], file_path: Path,
                           source_map: Dict[str, Dict[str, int]] | None = None) -> Config:
        """Create appropriate data structure based on entity type."""
        base_data = {
            'name': entity_name,
            'full_name': full_name,
            'entity_type': entity_type,
            'config': config,
            'file_path': file_path,
            'source_map': source_map,
        }
        
        if entity_type == ConfigType.NODE:
            # Initialize parameter values from defaults
            parameters = config.get('parameters', [])
            if parameters:
                for param in parameters:
                    if 'default' in param and 'value' not in param:
                        param['value'] = param['default']

            _normalize_param_list(
                parameters,
                file_path=file_path,
                source_map=source_map,
                base_path="/parameters",
            )

            # Extract top-level package info (name and provider)
            pkg_info = config.get('package')
            pkg_name = None
            pkg_provider = None
            if isinstance(pkg_info, dict):
                pkg_name = pkg_info.get('name')
                pkg_provider = pkg_info.get('provider')

            sub_type = ConfigSubType.VARIANT if "base" in config else ConfigSubType.BASE
            return NodeConfig(
                **base_data,
                sub_type=sub_type,
                package_name=pkg_name,
                package_provider=pkg_provider,
                launch=config.get('launch'),
                inputs=config.get('inputs'),
                outputs=config.get('outputs'),
                parameter_files=config.get('parameter_files'),
                parameters=parameters,
                processes=config.get('processes')
            )
        elif entity_type == ConfigType.MODULE:
            sub_type = ConfigSubType.VARIANT if "base" in config else ConfigSubType.BASE
            return ModuleConfig(
                **base_data,
                sub_type=sub_type,
                instances=config.get('instances'),
                external_interfaces=config.get('external_interfaces'),
                connections=config.get('connections')
            )
        elif entity_type == ConfigType.PARAMETER_SET:
            parameters = config.get('parameters')
            if isinstance(parameters, list):
                for idx, node_entry in enumerate(parameters):
                    if not isinstance(node_entry, dict):
                        continue
                    _normalize_param_list(
                        node_entry.get("parameters"),
                        file_path=file_path,
                        source_map=source_map,
                        base_path=f"/parameters/{idx}/parameters",
                    )
            return ParameterSetConfig(
                **base_data,
                parameters=parameters,
                local_variables=config.get('local_variables')
            )
        elif entity_type == ConfigType.SYSTEM:
            sub_type = ConfigSubType.VARIANT if "base" in config else ConfigSubType.BASE
            
            # Parse mode-specific configurations
            mode_configs = {}
            modes = config.get('modes')
            if modes:
                # Extract mode names from modes list
                mode_names = [m.get('name') for m in modes if isinstance(m, dict) and 'name' in m]
                
                # Look for top-level keys matching mode names
                for mode_name in mode_names:
                    if mode_name in config:
                        mode_configs[mode_name] = config[mode_name]
                        logger.debug(f"Found mode-specific configuration for mode '{mode_name}'")
            
            return SystemConfig(
                **base_data,
                sub_type=sub_type,
                arguments=config.get('arguments'),
                modes=config.get('modes'),
                mode_configs=mode_configs if mode_configs else None,
                parameter_sets=config.get('parameter_sets'),
                components=config.get('components'),
                connections=config.get('connections'),
                variables=config.get('variables'),
                variable_files=config.get('variable_files')
            )
        else:
            raise ValidationError(f"Unknown entity type: {entity_type}")