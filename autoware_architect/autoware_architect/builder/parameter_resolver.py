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

import logging
import re
import os
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class ParameterResolver:
    """Resolves ROS-specific substitutions in parameters to make autoware_architect ROS-independent.

    Handles:
    1. $(env ENV_VAR) -> environment variable value
    2. $(var variable_name) -> resolved variable value
    3. $(find-pkg-share package_name) -> absolute package path
    4. Nested substitutions: $(find-pkg-share $(var vehicle_model)_description)

    Resolution order: environment variables first, then variables, then find-pkg-share commands.
    """

    def __init__(self, global_params: List[Dict[str, Any]], env_params: List[Dict[str, Any]],
                 package_paths: Dict[str, str]):
        """Initialize resolver with deployment parameters and package paths.

        Args:
            global_params: Global parameters from deployment.yaml
            env_params: Environment parameters from deployment.yaml
            package_paths: Mapping of package_name -> absolute_path from manifest_dir
        """
        self.variable_map = self._build_variable_map(global_params, env_params)
        self.package_paths = package_paths.copy()

        # Regex patterns for substitutions
        self.env_pattern = re.compile(r'\$\(env\s+([^)]+)\)')
        self.var_pattern = re.compile(r'\$\(var\s+(\w+)\)')
        self.pkgshare_pattern = re.compile(r'\$\(find-pkg-share\s+([^)]+)\)')

    def _build_variable_map(self, global_params: List[Dict[str, Any]],
                           env_params: List[Dict[str, Any]]) -> Dict[str, str]:
        """Build variable mapping from deployment parameters."""
        variables = {}

        # Add global parameters
        for param in global_params:
            name = param.get('name')
            value = param.get('value')
            if name and value is not None:
                variables[name] = str(value)

        # Add environment parameters
        for param in env_params:
            name = param.get('name')
            value = param.get('value')
            if name and value is not None:
                variables[name] = str(value)

        return variables

    def resolve_string(self, input_string: str) -> str:
        """Resolve all substitutions in a string.

        Args:
            input_string: String containing $(var ...) and/or $(find-pkg-share ...) substitutions

        Returns:
            String with all substitutions resolved
        """
        if not input_string or not isinstance(input_string, str):
            return input_string

        result = input_string
        max_iterations = 10  # Prevent infinite loops from circular references
        iteration = 0

        while iteration < max_iterations:
            # Track if any substitutions were made this iteration
            original_result = result

            # First resolve environment variables (they might be used in other substitutions)
            result = self.env_pattern.sub(self._resolve_env_match, result)

            # Then resolve variables (they might be used in find-pkg-share)
            result = self.var_pattern.sub(self._resolve_var_match, result)

            # Then resolve find-pkg-share commands
            result = self.pkgshare_pattern.sub(self._resolve_pkgshare_match, result)

            # If no changes were made, we're done
            if result == original_result:
                break

            iteration += 1

        if iteration >= max_iterations:
            logger.warning(f"Possible circular reference in parameter resolution: {input_string}")

        return result

    def _resolve_env_match(self, match) -> str:
        """Resolve a single $(env ENV_VAR) match."""
        env_var = match.group(1).strip()
        env_value = os.environ.get(env_var)
        if env_value is not None:
            return env_value
        else:
            logger.warning(f"Environment variable not set: $(env {env_var})")
            return match.group(0)  # Return original if not found

    def _resolve_var_match(self, match) -> str:
        """Resolve a single $(var variable_name) match."""
        var_name = match.group(1)
        if var_name in self.variable_map:
            return self.variable_map[var_name]
        else:
            logger.warning(f"Undefined variable: $(var {var_name})")
            return match.group(0)  # Return original if not found

    def _resolve_pkgshare_match(self, match) -> str:
        """Resolve a single $(find-pkg-share package_name) match."""
        package_expr = match.group(1).strip()

        # Handle nested environment variables and variables in package name
        resolved_package = self.env_pattern.sub(self._resolve_env_match, package_expr)
        resolved_package = self.var_pattern.sub(self._resolve_var_match, resolved_package)

        if resolved_package in self.package_paths:
            return self.package_paths[resolved_package]
        else:
            logger.warning(f"Package not found in manifest: $(find-pkg-share {resolved_package})")
            return match.group(0)  # Return original if not found

    def resolve_parameter_file_path(self, file_path: str) -> str:
        """Resolve substitutions in a parameter file path.

        Args:
            file_path: Parameter file path that may contain substitutions

        Returns:
            Resolved file path
        """
        return self.resolve_string(file_path)

    def resolve_parameter_value(self, param_value: Any) -> Any:
        """Resolve substitutions in a parameter value.

        Args:
            param_value: Parameter value (string, list, dict) that may contain substitutions

        Returns:
            Parameter value with substitutions resolved
        """
        if isinstance(param_value, str):
            return self.resolve_string(param_value)
        elif isinstance(param_value, list):
            return [self.resolve_parameter_value(item) for item in param_value]
        elif isinstance(param_value, dict):
            return {key: self.resolve_parameter_value(value) for key, value in param_value.items()}
        else:
            # Non-string values (int, float, bool) don't need resolution
            return param_value

    def resolve_parameters(self, parameters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Resolve substitutions in a list of parameter configurations.

        Args:
            parameters: List of parameter dicts with 'name', 'value', etc.

        Returns:
            Parameters with resolved values
        """
        resolved_params = []
        for param in parameters:
            resolved_param = param.copy()
            if 'value' in resolved_param:
                resolved_param['value'] = self.resolve_parameter_value(resolved_param['value'])
            resolved_params.append(resolved_param)
        return resolved_params

    def resolve_parameter_files(self, parameter_files: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Resolve substitutions in parameter file mappings.

        Args:
            parameter_files: List of dicts mapping parameter names to file paths

        Returns:
            Parameter files with resolved paths
        """
        resolved_files = []
        for file_mapping in parameter_files:
            resolved_mapping = {}
            for param_name, file_path in file_mapping.items():
                resolved_mapping[param_name] = self.resolve_parameter_file_path(file_path)
            resolved_files.append(resolved_mapping)
        return resolved_files

    def get_resolved_package_path(self, package_name: str) -> Optional[str]:
        """Get the resolved absolute path for a package.

        Args:
            package_name: Package name (may contain variables)

        Returns:
            Absolute package path or None if not found
        """
        resolved_name = self.resolve_string(package_name)
        return self.package_paths.get(resolved_name)
