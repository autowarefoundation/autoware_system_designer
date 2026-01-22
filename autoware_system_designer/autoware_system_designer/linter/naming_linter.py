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

"""Naming convention linter for autoware_system_design_format files."""

import re
from pathlib import Path
from typing import Dict, Any

from ..parsers.yaml_parser import yaml_parser
from ..parsers.data_validator import entity_name_decode
from ..models.config import ConfigType
from .report import LintResult


class NamingLinter:
    """Linter for naming conventions."""
    
    def lint(self, file_path: Path, result: LintResult):
        """Lint naming conventions in the YAML file.
        
        Args:
            file_path: Path to the file to lint
            result: LintResult to add errors/warnings to
        """
        try:
            config = yaml_parser.load_config(str(file_path))
        except Exception as e:
            result.add_error(f"Failed to load YAML file: {str(e)}")
            return
        
        # Skip name format checks for parameter_set files
        if file_path.name.endswith(".parameter_set.yaml"):
            return

        # Check entity name format
        if 'name' in config:
            entity_name = config['name']
            try:
                name_part, type_part = entity_name_decode(entity_name)
                base_ref = config.get('base')

                if type_part == ConfigType.PARAMETER_SET:
                    return

                # Check entity name format based on base/variant
                if base_ref:
                    if not self._is_allowed_variant_name(name_part, base_ref):
                        base_name = self._get_base_name(base_ref)
                        base_hint = base_name if base_name else "OriginalName"
                        result.add_error(
                            f"Entity name '{name_part}' should be snake_case or "
                            f"'{base_hint}_snake_variant' for variant config"
                        )
                else:
                    if not self._is_pascal_case(name_part):
                        result.add_error(
                            f"Entity name '{name_part}' should be in PascalCase format "
                            f"(e.g., 'DetectorA', 'MyModule')"
                        )
            except Exception as e:
                result.add_error(f"Invalid entity name format: {str(e)}")
        
        # Check instance names (for modules)
        if 'instances' in config and isinstance(config['instances'], list):
            for idx, instance in enumerate(config['instances']):
                if isinstance(instance, dict) and 'name' in instance:
                    instance_name = instance['name']
                    if not self._is_snake_case(instance_name):
                        result.add_error(
                            f"Instance name '{instance_name}' should be in snake_case format "
                            f"(e.g., 'node_detector', 'pointcloud_input')"
                        )
        
        # Check variant override/remove names
        self._lint_variant_names(config, result)

        # Check port names (for nodes)
        if 'inputs' in config and isinstance(config['inputs'], list):
            for idx, input_port in enumerate(config['inputs']):
                if isinstance(input_port, dict) and 'name' in input_port:
                    port_name = input_port['name']
                    if not self._is_snake_case(port_name):
                        result.add_error(
                            f"Input port name '{port_name}' should be in snake_case format "
                            f"(e.g., 'pointcloud', 'vector_map')"
                        )
        
        if 'outputs' in config and isinstance(config['outputs'], list):
            for idx, output_port in enumerate(config['outputs']):
                if isinstance(output_port, dict) and 'name' in output_port:
                    port_name = output_port['name']
                    if not self._is_snake_case(port_name):
                        result.add_error(
                            f"Output port name '{port_name}' should be in snake_case format "
                            f"(e.g., 'objects', 'detected_objects')"
                        )
        
        # Check external interface names (for modules)
        if 'external_interfaces' in config:
            ext_interfaces = config['external_interfaces']
            if isinstance(ext_interfaces, dict):
                # Check input ports
                if 'input' in ext_interfaces and isinstance(ext_interfaces['input'], list):
                    for ext_input in ext_interfaces['input']:
                        if isinstance(ext_input, dict) and 'name' in ext_input:
                            port_name = ext_input['name']
                            if not self._is_snake_case(port_name):
                                result.add_error(
                                    f"External input port name '{port_name}' should be in snake_case format"
                                )
                
                # Check output ports
                if 'output' in ext_interfaces and isinstance(ext_interfaces['output'], list):
                    for ext_output in ext_interfaces['output']:
                        if isinstance(ext_output, dict) and 'name' in ext_output:
                            port_name = ext_output['name']
                            if not self._is_snake_case(port_name):
                                result.add_error(
                                    f"External output port name '{port_name}' should be in snake_case format"
                                )
                
                # Check parameter namespaces
                if 'parameter' in ext_interfaces and isinstance(ext_interfaces['parameter'], list):
                    for ext_param in ext_interfaces['parameter']:
                        if isinstance(ext_param, dict) and 'name' in ext_param:
                            param_name = ext_param['name']
                            if not self._is_snake_case(param_name):
                                result.add_error(
                                    f"External parameter name '{param_name}' should be in snake_case format"
                                )
    
    @staticmethod
    def _is_pascal_case(name: str) -> bool:
        """Check if a string is in PascalCase format.
        
        PascalCase: Starts with uppercase letter, followed by alphanumeric characters.
        Examples: DetectorA, MyModule, Node123
        
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
        pattern = r'^[A-Z][a-zA-Z0-9]*$'
        return bool(re.match(pattern, name))
    
    @staticmethod
    def _is_snake_case(name: str) -> bool:
        """Check if a string is in snake_case format.
        
        snake_case: Lowercase letters, numbers, and underscores. Must start with a letter.
        Examples: pointcloud_input, node_detector, my_port_123
        Also allows slash-delimited segments with snake_case each:
        Examples: lidar/front_lower/pointcloud, camera/camera0/image_raw
        
        Args:
            name: String to check
            
        Returns:
            True if string is in snake_case format
        """
        if not name:
            return False
        
        # Must start with lowercase letter
        if not name[0].islower():
            return False
        
        # Allow slash-delimited snake_case segments
        pattern = r'^[a-z][a-z0-9_]*(/[a-z][a-z0-9_]*)*$'
        return bool(re.match(pattern, name))

    def _is_allowed_variant_name(self, name: str, base_ref: str) -> bool:
        """Check variant name rules for temporary/variant configs."""
        if self._is_snake_case(name):
            return True

        base_name = self._get_base_name(base_ref)
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
    def _get_base_name(base_ref: Any) -> str:
        """Get base name from base field."""
        if not isinstance(base_ref, str):
            return ""
        try:
            base_name, _ = entity_name_decode(base_ref)
            return base_name
        except Exception:
            return ""

    def _lint_variant_names(self, config: Dict[str, Any], result: LintResult):
        """Lint naming conventions in variant override/remove blocks."""
        override = config.get('override')
        if isinstance(override, dict):
            self._lint_named_list(override.get('inputs'), result, "Override input")
            self._lint_named_list(override.get('outputs'), result, "Override output")
            self._lint_named_list(override.get('parameters'), result, "Override parameter")
            self._lint_named_list(override.get('parameter_files'), result, "Override parameter file")
            self._lint_named_list(override.get('processes'), result, "Override process")
            self._lint_named_list(override.get('instances'), result, "Override instance", key="name")
            self._lint_named_list(override.get('variables'), result, "Override variable")
            self._lint_named_list(override.get('variable_files'), result, "Override variable file")
            self._lint_named_list(override.get('components'), result, "Override component", key="component")
            self._lint_external_interfaces(override.get('external_interfaces'), result, "Override")

        remove = config.get('remove')
        if isinstance(remove, dict):
            self._lint_named_list(remove.get('inputs'), result, "Remove input")
            self._lint_named_list(remove.get('outputs'), result, "Remove output")
            self._lint_named_list(remove.get('parameters'), result, "Remove parameter")
            self._lint_named_list(remove.get('parameter_files'), result, "Remove parameter file")
            self._lint_named_list(remove.get('processes'), result, "Remove process")
            self._lint_named_list(remove.get('instances'), result, "Remove instance", key="name")
            self._lint_named_list(remove.get('variables'), result, "Remove variable")
            self._lint_named_list(remove.get('components'), result, "Remove component", key="component")
            self._lint_external_interfaces(remove.get('external_interfaces'), result, "Remove")

    def _lint_named_list(
        self,
        items: Any,
        result: LintResult,
        label: str,
        key: str = "name",
    ):
        """Lint snake_case for named list items."""
        if not isinstance(items, list):
            return

        for idx, item in enumerate(items):
            if isinstance(item, dict) and key in item:
                name_value = item[key]
                if not self._is_snake_case(name_value):
                    result.add_error(
                        f"{label} name '{name_value}' should be in snake_case format"
                    )

    def _lint_external_interfaces(self, external_interfaces: Any, result: LintResult, label: str):
        """Lint external interface names in variant blocks."""
        if not isinstance(external_interfaces, dict):
            return

        for interface_type in ['input', 'output', 'parameter']:
            if interface_type in external_interfaces and isinstance(external_interfaces[interface_type], list):
                for entry in external_interfaces[interface_type]:
                    if isinstance(entry, dict) and 'name' in entry:
                        port_name = entry['name']
                        if not self._is_snake_case(port_name):
                            result.add_error(
                                f"{label} external {interface_type} name '{port_name}' should be in snake_case format"
                            )

