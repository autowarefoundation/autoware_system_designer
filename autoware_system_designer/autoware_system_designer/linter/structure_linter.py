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

"""Structure and schema linter for autoware_system_design_format files.

This linter validates YAML against schema definitions (single source of truth)
and reports errors with YAML locations when available.
"""

from pathlib import Path
from typing import Any, Dict

from ..models.parsing.yaml_parser import yaml_parser
from ..models.parsing.data_validator import entity_name_decode
from ..file_io.source_location import SourceLocation, lookup_source, format_source
from ..schema.yaml_schema import get_entity_schema, validate_against_schema
from .report import LintResult


class StructureLinter:
    """Linter for structure and schema validation."""
    
    def __init__(self):
        """Initialize the structure linter."""
        pass
    
    def lint(self, file_path: Path, result: LintResult):
        """Lint structure and schema of the YAML file.
        
        Args:
            file_path: Path to the file to lint
            result: LintResult to add errors/warnings to
        """
        try:
            config, source_map = yaml_parser.load_config_with_source(str(file_path))
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
            name_loc = lookup_source(source_map, "/name")
            result.add_error(
                "Missing required field 'name'",
                line=name_loc.line,
                column=name_loc.column,
                yaml_path=name_loc.yaml_path,
            )
            return
        
        try:
            entity_name, entity_type = entity_name_decode(config['name'])
            name_loc = lookup_source(source_map, "/name")
            
            # Check entity name matches filename
            if entity_name != file_entity_name:
                src = SourceLocation(file_path=file_path, yaml_path=name_loc.yaml_path, line=name_loc.line, column=name_loc.column)
                result.add_error(
                    f"Entity name '{entity_name}' does not match file name '{file_entity_name}'.{format_source(src)}",
                    line=name_loc.line,
                    column=name_loc.column,
                    yaml_path=name_loc.yaml_path,
                )
            
            # Check entity type matches file extension
            if entity_type != file_entity_type:
                src = SourceLocation(file_path=file_path, yaml_path=name_loc.yaml_path, line=name_loc.line, column=name_loc.column)
                result.add_error(
                    f"Entity type '{entity_type}' does not match file extension type '{file_entity_type}'.{format_source(src)}",
                    line=name_loc.line,
                    column=name_loc.column,
                    yaml_path=name_loc.yaml_path,
                )

            # Schema-driven validation (single source of truth)
            try:
                schema = get_entity_schema(entity_type)
            except Exception as e:
                result.add_error(f"Unknown entity type '{entity_type}': {e}")
                return

            issues = validate_against_schema(config, schema=schema)
            for issue in issues:
                loc = lookup_source(source_map, issue.yaml_path)
                src = SourceLocation(file_path=file_path, yaml_path=loc.yaml_path, line=loc.line, column=loc.column)
                suffix = format_source(src)
                result.add_error(
                    f"{issue.message}{suffix}",
                    line=loc.line,
                    column=loc.column,
                    yaml_path=loc.yaml_path,
                )
            
        except Exception as e:
            result.add_error(f"Error validating entity name: {str(e)}")

