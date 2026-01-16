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

from typing import Dict, Any, List, Tuple
from abc import ABC, abstractmethod

from ..models.config import ConfigType
from ..exceptions import ValidationError


def entity_name_decode(entity_name: str) -> Tuple[str, str]:
    """Decode entity name into name and type components."""
    # example: 'my_node.module' -> ('my_node', 'module')

    if not entity_name or not isinstance(entity_name, str):
        raise ValidationError(f"Config name must be a non-empty string, got: {entity_name}")
    
    if "." not in entity_name:
        raise ValidationError(f"Invalid entity name format: '{entity_name}'. Expected format: 'name.type'")

    parts = entity_name.split(".")
    if len(parts) != 2:
        raise ValidationError(f"Invalid entity name format: '{entity_name}'. Expected exactly one dot separator")

    name, entity_type = parts
    
    if not name.strip():
        raise ValidationError(f"Config name cannot be empty in: '{entity_name}'")
    
    if not entity_type.strip():
        raise ValidationError(f"Config type cannot be empty in: '{entity_name}'")

    if entity_type not in ConfigType.get_all_types():
        raise ValidationError(f"Invalid entity type: '{entity_type}'. Valid types: {ConfigType.get_all_types()}")

    return name.strip(), entity_type.strip()

class BaseValidator(ABC):
    """Abstract base validator."""
    
    @abstractmethod
    def get_required_fields(self) -> List[str]:
        """Get required fields for this entity type."""
        pass
    
    @abstractmethod
    def get_override_fields(self) -> List[str]:
        """Get fields that must be in override block for inheritance."""
        return []

    @abstractmethod
    def get_schema_properties(self) -> Dict[str, Dict[str, str]]:
        """Get schema properties for validation."""
        pass
    
    def validate_basic_structure(self, config: Dict[str, Any], file_path: str) -> None:
        """Validate basic structure requirements."""
        if not config:
            raise ValidationError(f"Empty configuration file: {file_path}")
        
        if "name" not in config:
            raise ValidationError(f"Field 'name' is required in entity configuration. File: {file_path}")

    def validate_entity_type(self, entity_type: str, expected_type: str, file_path: str) -> None:
        """Validate that the entity type matches expected type."""
        if entity_type != expected_type:
            raise ValidationError(
                f"Invalid entity type '{entity_type}'. Expected '{expected_type}'. File: {file_path}"
            )

    def validate_required_fields(self, config: Dict[str, Any], file_path: str) -> None:
        """Validate that all required fields are present."""
        required_fields = self.get_required_fields()
        missing_fields = [field for field in required_fields if field not in config]
        
        if missing_fields:
            raise ValidationError(
                f"Missing required fields {missing_fields} in configuration. File: {file_path}"
            )
    
    def validate_schema(self, config: Dict[str, Any], file_path: str) -> None:
        """Validate configuration against schema."""
        errors = []
        schema_properties = self.get_schema_properties()
        
        for field, field_schema in schema_properties.items():
            if field in config:
                expected_type = field_schema.get('type')
                if expected_type and not self._validate_type(config[field], expected_type):
                    errors.append(f"Field '{field}' has invalid type. Expected: {expected_type}")
        
        if errors:
            error_msg = f"Schema validation failed for {file_path}:\n" + "\n".join(f"  - {error}" for error in errors)
            raise ValidationError(error_msg)
    
    def _validate_type(self, value: Any, expected_type: str) -> bool:
        """Validate that a value matches the expected type."""
        type_map = {
            'string': str,
            'integer': int,
            'number': (int, float),
            'boolean': bool,
            'array': list,
            'object': dict,
            'object_or_array': (dict, list),
            'nullable_object': (dict, type(None)),
            'nullable_array': (list, type(None)),
        }
        
        if expected_type in type_map:
            expected_types = type_map[expected_type]
            return isinstance(value, expected_types if isinstance(expected_types, tuple) else (expected_types,))
        return True

    def validate_all(self, config: Dict[str, Any], entity_type: str, expected_type: str, file_path: str) -> None:
        """Perform complete validation."""
        self.validate_basic_structure(config, file_path)
        self.validate_entity_type(entity_type, expected_type, file_path)
        self.validate_required_fields(config, file_path)
        self.validate_inheritance_structure(config, file_path)
      
        self.validate_schema(config, file_path)

    def validate_inheritance_structure(self, config: Dict[str, Any], file_path: str) -> None:
        """Validate inheritance structure."""
        if "inheritance" in config:
            # Check for forbidden root fields
            forbidden = self.get_override_fields()
            found = [f for f in forbidden if f in config]
            if found:
                raise ValidationError(f"Fields {found} must be under 'override' block in inheritance config. File: {file_path}")
            
            # Validate override block if present
            if "override" in config:
                override = config["override"]
                if not isinstance(override, dict):
                    raise ValidationError(f"'override' must be a dictionary. File: {file_path}")
                
                # Recursively validate schema for override fields
                # We reuse validate_schema but apply it to override dict
                # Note: This checks that fields inside 'override' match the schema properties
                # It doesn't complain about unknown fields unless we enforce strict schema, which validate_schema doesn't do yet.
                self.validate_schema(override, f"{file_path} (override)")

class NodeValidator(BaseValidator):
    """Validator for node entities."""
    
    def get_required_fields(self) -> List[str]:
        return ["name"]

    def get_override_fields(self) -> List[str]:
        return ["launch", "inputs", "outputs", "parameter_files", "parameters", "processes"]

    def validate_required_fields(self, config: Dict[str, Any], file_path: str) -> None:
        """
        Validate that all required fields are present.
        """
        super().validate_required_fields(config, file_path)
        
        if "inheritance" not in config:
             required_full = ["launch", "inputs", "outputs", "parameter_files", "parameters", "processes"]
             missing = [f for f in required_full if f not in config]
             if missing:
                raise ValidationError(
                    f"Missing required fields {missing} in base node configuration (no inheritance). File: {file_path}"
                )
    
    def get_schema_properties(self) -> Dict[str, Dict[str, str]]:
        return {
            'name': {'type': 'string'},
            'inheritance': {'type': 'string'},
            'launch': {'type': 'object'},
            'inputs': {'type': 'array'},
            'outputs': {'type': 'array'},
            'parameter_files': {'type': 'object_or_array'},
            'parameters': {'type': 'object_or_array'},
            'processes': {'type': 'array'},
            'remove': {'type': 'object'},
            'override': {'type': 'object'}
        }

class ModuleValidator(BaseValidator):
    """Validator for module entities."""
    
    def get_required_fields(self) -> List[str]:
        return ["name"]

    def get_override_fields(self) -> List[str]:
        return ["instances", "external_interfaces", "connections"]

    def validate_required_fields(self, config: Dict[str, Any], file_path: str) -> None:
        """
        Validate that all required fields are present.
        """
        super().validate_required_fields(config, file_path)
        
        if "inheritance" not in config:
             required_full = ["instances", "external_interfaces", "connections"]
             missing = [f for f in required_full if f not in config]
             if missing:
                raise ValidationError(
                    f"Missing required fields {missing} in base module configuration (no inheritance). File: {file_path}"
                )
    
    def get_schema_properties(self) -> Dict[str, Dict[str, str]]:
        return {
            'name': {'type': 'string'},
            'inheritance': {'type': 'string'},
            'instances': {'type': 'array'},
            'external_interfaces': {'type': 'object_or_array'},
            'connections': {'type': 'array'},
            'remove': {'type': 'object'},
            'override': {'type': 'object'},
        }

class ParameterSetValidator(BaseValidator):
    """Validator for parameter set entities."""
    
    def get_required_fields(self) -> List[str]:
        return ["name", "parameters"]
    
    def get_override_fields(self) -> List[str]:
        return []

    def get_schema_properties(self) -> Dict[str, Dict[str, str]]:
        return {
            'name': {'type': 'string'},
            'parameters': {'type': 'object_or_array'},
            'local_variables': {'type': 'nullable_array'},
        }

class SystemValidator(BaseValidator):
    """Validator for system entities."""
    
    def get_required_fields(self) -> List[str]:
        # Basic requirement is name
        return ["name"]

    def get_override_fields(self) -> List[str]:
        return ["modes", "parameter_sets", "components", "connections", "variables", "variable_files"]
    
    def validate_required_fields(self, config: Dict[str, Any], file_path: str) -> None:
        """
        Validate that all required fields are present.
        For System entities, requirements depend on whether it's an inheritance (child) or base system.
        """
        super().validate_required_fields(config, file_path)
        
        # If it has 'inheritance', it's a child config -> components/connections are optional (inherited)
        # If it does NOT have 'inheritance', it's a base config -> components/connections are required
        if "inheritance" not in config:
            missing = []
            if "components" not in config:
                missing.append("components")
            if "connections" not in config:
                missing.append("connections")
                
            if missing:
                raise ValidationError(
                    f"Missing required fields {missing} in base system configuration (no inheritance). File: {file_path}"
                )

    def get_schema_properties(self) -> Dict[str, Dict[str, str]]:
        return {
            'name': {'type': 'string'},
            'inheritance': {'type': 'string'},
            'modes': {'type': 'nullable_array'},
            'parameter_sets': {'type': 'nullable_array'},  # System-level parameter sets
            'components': {'type': 'array'},
            'connections': {'type': 'array'},
            'variables': {'type': 'nullable_array'},
            'variable_files': {'type': 'nullable_array'},
            'remove': {'type': 'object'}, # Support for removal in inheritance
            'override': {'type': 'object'},
        }

class ValidatorFactory:
    """Factory for creating validators."""
    
    _validators = {
        ConfigType.NODE: NodeValidator,
        ConfigType.MODULE: ModuleValidator,
        ConfigType.PARAMETER_SET: ParameterSetValidator,
        ConfigType.SYSTEM: SystemValidator,
    }
    
    @classmethod
    def get_validator(cls, entity_type: str) -> BaseValidator:
        """Get validator for entity type."""
        if entity_type not in cls._validators:
            raise ValidationError(f"Unknown entity type: {entity_type}")
        return cls._validators[entity_type]()