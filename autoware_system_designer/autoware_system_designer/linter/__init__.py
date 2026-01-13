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

"""Linter package for autoware_system_design_format validation."""

from pathlib import Path
from typing import List

from .report import LintResult
from .structure_linter import StructureLinter
from .naming_linter import NamingLinter
from .file_linter import FileLinter

__all__ = ['lint_files', 'LintResult']


def lint_files(file_paths: List[Path]) -> List[LintResult]:
    """Lint a list of YAML files.
    
    Args:
        file_paths: List of file paths to lint
        
    Returns:
        List of LintResult objects, one per file
    """
    results = []
    
    structure_linter = StructureLinter()
    naming_linter = NamingLinter()
    file_linter = FileLinter()
    
    for file_path in file_paths:
        result = LintResult(file_path)
        
        # Run all linters
        try:
            file_linter.lint(file_path, result)
            structure_linter.lint(file_path, result)
            naming_linter.lint(file_path, result)
        except Exception as e:
            result.add_error(f"Unexpected error during linting: {str(e)}")
        
        results.append(result)
    
    return results

