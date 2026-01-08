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

