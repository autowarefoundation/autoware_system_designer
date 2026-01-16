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

"""File naming linter for autoware_system_design_format files."""

import re
from pathlib import Path

from ..parsers.yaml_parser import yaml_parser
from ..parsers.data_validator import entity_name_decode
from ..models.config import ConfigType
from .report import LintResult


class FileLinter:
    """Linter for file naming conventions."""
    
    # Valid entity file extensions
    VALID_EXTENSIONS = {
        '.node.yaml': ConfigType.NODE,
        '.module.yaml': ConfigType.MODULE,
        '.system.yaml': ConfigType.SYSTEM,
        '.parameter_set.yaml': ConfigType.PARAMETER_SET,
    }
    
    def lint(self, file_path: Path, result: LintResult):
        """Lint file naming conventions.
        
        Args:
            file_path: Path to the file to lint
            result: LintResult to add errors/warnings to
        """
        file_name = file_path.name
        
        # Check if file has valid extension
        valid_extension = None
        for ext, entity_type in self.VALID_EXTENSIONS.items():
            if file_name.endswith(ext):
                valid_extension = ext
                expected_type = entity_type
                break
        
        if not valid_extension:
            result.add_error(
                f"File does not have a valid entity extension. "
                f"Expected one of: {', '.join(self.VALID_EXTENSIONS.keys())}"
            )
            return
        
        # Extract base name (without extension)
        base_name = file_name[:-len(valid_extension)]

        if expected_type == ConfigType.PARAMETER_SET:
            return

        # Load config to detect inheritance-specific naming rules
        inheritance_value = None
        try:
            config = yaml_parser.load_config(str(file_path))
            if isinstance(config, dict):
                inheritance_value = config.get('inheritance')
        except Exception:
            inheritance_value = None
        
        # Check that file name matches expected pattern: Name.type.yaml
        if '.' in base_name:
            parts = base_name.split('.')
            if len(parts) == 2:
                name_part, type_part = parts
                if type_part != expected_type:
                    result.add_error(
                        f"File name type part '{type_part}' does not match "
                        f"file extension type '{expected_type}'"
                    )
                if expected_type != ConfigType.PARAMETER_SET:
                    if inheritance_value:
                        if not self._is_allowed_inheritance_name(name_part, inheritance_value):
                            base_hint = self._get_inheritance_base_name(inheritance_value) or "OriginalName"
                            result.add_error(
                                f"Entity name '{name_part}' should be snake_case or "
                                f"'{base_hint}_snake_variant' for inheritance config"
                            )
                    else:
                        if not self._is_pascal_case(name_part):
                            result.add_error(
                                f"Entity name '{name_part}' should be in PascalCase format "
                                f"(e.g., 'DetectorA', 'MyModule')"
                            )
            else:
                result.add_error(
                    f"File name '{base_name}' should be in format 'Name.type' "
                    f"(e.g., 'DetectorA.node', 'MyModule.module')"
                )
        else:
            # If no dot, the entire base name should be PascalCase
            # This handles the case where file is just Name.yaml (though not standard)
            if expected_type != ConfigType.PARAMETER_SET:
                if inheritance_value:
                    if not self._is_allowed_inheritance_name(base_name, inheritance_value):
                        base_hint = self._get_inheritance_base_name(inheritance_value) or "OriginalName"
                        result.add_error(
                            f"File name '{base_name}' should be snake_case or "
                            f"'{base_hint}_snake_variant' for inheritance config"
                        )
                else:
                    if not self._is_pascal_case(base_name):
                        result.add_error(
                            f"File name '{base_name}' should be in PascalCase format "
                            f"(e.g., 'DetectorA', 'MyModule')"
                        )
    
    @staticmethod
    def _is_pascal_case(name: str) -> bool:
        """Check if a string is in PascalCase format.
        
        PascalCase: Starts with uppercase letter, followed by alphanumeric characters,
        with no underscores or spaces. Examples: DetectorA, MyModule, Node123
        
        Args:
            name: String to check
            
        Returns:
            True if string is in PascalCase format
        """
        if not name:
            return False
        
        # Must start with uppercase letter
        if not name[0].isupper():
            return False
        
        # Rest should be alphanumeric (no underscores, spaces, or special chars)
        # Allow lowercase letters and numbers after the first character
        pattern = r'^[A-Z][a-zA-Z0-9]*$'
        return bool(re.match(pattern, name))

    @staticmethod
    def _is_snake_case(name: str) -> bool:
        """Check if a string is in snake_case format."""
        if not name:
            return False
        if not name[0].islower():
            return False
        pattern = r'^[a-z][a-z0-9_]*$'
        return bool(re.match(pattern, name))

    def _is_allowed_inheritance_name(self, name: str, inheritance_value: str) -> bool:
        """Check inheritance name rules for temporary/variant configs."""
        if self._is_snake_case(name):
            return True

        base_name = self._get_inheritance_base_name(inheritance_value)
        if not base_name:
            return False

        if not name.startswith(f"{base_name}_"):
            return False

        suffix = name[len(base_name) + 1:]
        return bool(suffix) and self._is_snake_suffix(suffix)

    @staticmethod
    def _is_snake_suffix(name: str) -> bool:
        """Allow lowercase/digits/underscores, leading digit OK."""
        return bool(re.match(r'^[a-z0-9][a-z0-9_]*$', name))

    @staticmethod
    def _get_inheritance_base_name(inheritance_value: str) -> str:
        """Get base name from inheritance field."""
        if not isinstance(inheritance_value, str):
            return ""
        try:
            base_name, _ = entity_name_decode(inheritance_value)
            return base_name
        except Exception:
            return ""

