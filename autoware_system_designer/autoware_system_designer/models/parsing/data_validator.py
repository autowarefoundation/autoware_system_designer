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

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from abc import ABC, abstractmethod

from ..config import ConfigType
from ..yaml_schema import get_entity_schema, validate_against_schema
from ...exceptions import ValidationError


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

    ENTITY_TYPE: str

    @classmethod
    def get_entity_type(cls) -> str:
        """Return the entity type this validator validates."""
        entity_type = getattr(cls, "ENTITY_TYPE", None)
        if not isinstance(entity_type, str) or not entity_type:
            raise NotImplementedError("Validator must define ENTITY_TYPE")
        return entity_type
    
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

    def validate_validator_type(self, entity_type: str, file_path: str) -> None:
        """Ensure the validator used matches the entity_type being validated."""
        validator_type = self.get_entity_type()
        if entity_type != validator_type:
            raise ValidationError(
                f"Internal error: validator '{validator_type}' used for entity type '{entity_type}'. File: {file_path}"
            )

    @staticmethod
    def _format_schema_issues(issues) -> str:
        return "\n".join(
            f"  - {i.message}" + (f" (yaml_path={i.yaml_path})" if getattr(i, "yaml_path", None) else "")
            for i in issues
        )

    def validate_all(self, config: Dict[str, Any], entity_type: str, expected_type: str, file_path: str) -> None:
        """Perform complete validation."""
        self.validate_basic_structure(config, file_path)
        self.validate_validator_type(entity_type, file_path)
        self.validate_entity_type(entity_type, expected_type, file_path)

        # Schema-driven structural + semantic validation
        schema = get_entity_schema(entity_type)
        issues = validate_against_schema(config, schema=schema)
        if issues:
            details = self._format_schema_issues(issues)
            raise ValidationError(f"Schema validation failed for {file_path}:\n{details}")

class NodeValidator(BaseValidator):
    """Validator for node entities."""

    ENTITY_TYPE = ConfigType.NODE

class ModuleValidator(BaseValidator):
    """Validator for module entities."""

    ENTITY_TYPE = ConfigType.MODULE

class ParameterSetValidator(BaseValidator):
    """Validator for parameter set entities."""

    ENTITY_TYPE = ConfigType.PARAMETER_SET

class SystemValidator(BaseValidator):
    """Validator for system entities."""

    ENTITY_TYPE = ConfigType.SYSTEM

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