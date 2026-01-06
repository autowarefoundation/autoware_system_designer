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
import math
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class ParameterResolver:
    """Resolves ROS-specific substitutions in parameters to make autoware_system_designer ROS-independent.

    Handles:
    1. $(env ENV_VAR) -> environment variable value
    2. $(var variable_name) -> resolved variable value
    3. $(find-pkg-share package_name) -> absolute package path
    4. $(eval expression) -> evaluated python expression (e.g., $(eval 1 + 2))
    5. Nested substitutions: $(find-pkg-share $(var vehicle_model)_description)

    Resolution order: environment variables first, then variables, then find-pkg-share commands, then eval.
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
        self.var_pattern = re.compile(r'\$\(var\s+([\w\.]+)\)')
        self.pkgshare_pattern = re.compile(r'\$\(find-pkg-share\s+([^)]+)\)')
        # eval_pattern removed in favor of manual parsing to support balanced parentheses

    def update_variables(self, new_variables: Dict[str, str]):
        """Update the variable map with new variables.
        
        Args:
            new_variables: Dictionary of new variables to add/update
        """
        self.variable_map.update(new_variables)

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
            
            # Then resolve eval commands (last step to ensure all variables are resolved)
            result = self._resolve_eval_substitutions(result)

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

    def _resolve_eval_substitutions(self, text: str) -> str:
        """Resolve $(eval ...) with balanced parentheses support."""
        if not text or '$(eval ' not in text:
            return text
            
        result = text
        cursor = 0
        
        while True:
            start_idx = result.find('$(eval ', cursor)
            if start_idx == -1:
                break
                
            # Find matching parenthesis
            balance = 1
            i = start_idx + len('$(eval ')
            end_idx = -1
            
            while i < len(result):
                if result[i] == '(':
                    balance += 1
                elif result[i] == ')':
                    balance -= 1
                    if balance == 0:
                        end_idx = i
                        break
                i += 1
                
            if end_idx != -1:
                # Found the block
                inner_expr = result[start_idx + len('$(eval '):end_idx]
                
                # Recursively resolve any evals inside this expression
                resolved_inner = self._resolve_eval_substitutions(inner_expr)
                
                # Check if we can evaluate
                if '$' in resolved_inner:
                     replacement = f"$(eval {resolved_inner})"
                     old_block = result[start_idx:end_idx+1]
                     
                     if replacement != old_block:
                         result = result[:start_idx] + replacement + result[end_idx+1:]
                         cursor = start_idx + len(replacement)
                     else:
                         cursor = end_idx + 1
                else:
                    # Evaluate
                    evaluated = self._evaluate_expression(resolved_inner)
                    result = result[:start_idx] + evaluated + result[end_idx+1:]
                    cursor = start_idx + len(evaluated)
            else:
                # No matching paren
                cursor = start_idx + len('$(eval ')
                
        return result

    def _evaluate_expression(self, expression: str) -> str:
        """Evaluate a python expression safely."""
        expression = expression.strip()
        
        if '$' in expression:
             return f"$(eval {expression})"

        try:
            # Safe evaluation scope with math module
            safe_scope = {
                '__builtins__': {},
                'math': math,
                'abs': abs,
                'min': min,
                'max': max,
                'pow': pow,
                'round': round,
                'int': int,
                'float': float,
                'str': str,
                # Add math constants and functions directly to scope for convenience
                'pi': math.pi,
                'sin': math.sin,
                'cos': math.cos,
                'tan': math.tan,
                'sqrt': math.sqrt,
                'atan2': math.atan2,
            }
            
            # Evaluate expression
            result = eval(expression, safe_scope)
            return str(result)
        except Exception as e:
            logger.warning(f"Failed to evaluate expression '$(eval {expression})': {e}")
            return f"$(eval {expression})"


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
                if 'name' in resolved_param:
                    self.variable_map[resolved_param['name']] = str(resolved_param['value'])
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
