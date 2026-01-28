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

"""Error reporting for the linter."""

from pathlib import Path
from typing import List, Dict, Any, Optional


class LintResult:
    """Container for linting results for a single file."""
    
    def __init__(self, file_path: Path):
        """Initialize lint result.
        
        Args:
            file_path: Path to the file being linted
        """
        self.file_path = file_path
        self.errors: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
    
    def add_error(
        self,
        message: str,
        line: Optional[int] = None,
        column: Optional[int] = None,
        yaml_path: Optional[str] = None,
    ):
        """Add an error message.
        
        Args:
            message: Error message
            line: Optional line number where error occurred
        """
        error = {'message': message}
        if line is not None:
            error['line'] = line
        if column is not None:
            error['column'] = column
        if yaml_path is not None:
            error['yaml_path'] = yaml_path
        self.errors.append(error)
    
    def add_warning(
        self,
        message: str,
        line: Optional[int] = None,
        column: Optional[int] = None,
        yaml_path: Optional[str] = None,
    ):
        """Add a warning message.
        
        Args:
            message: Warning message
            line: Optional line number where warning occurred
        """
        warning = {'message': message}
        if line is not None:
            warning['line'] = line
        if column is not None:
            warning['column'] = column
        if yaml_path is not None:
            warning['yaml_path'] = yaml_path
        self.warnings.append(warning)

