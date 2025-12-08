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

"""Naming utilities for the autoware architecture system."""

import re


def pascal_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case.
    
    Args:
        name: String in PascalCase format
        
    Returns:
        String in snake_case format
    """
    if not name:
        return ""
    
    # Insert underscore before uppercase letters that follow lowercase letters
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    # Insert underscore before uppercase letters that follow lowercase letters or digits
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

def snake_to_pascal(name: str) -> str:
    """Convert snake_case to PascalCase.
    
    Args:
        name: String in snake_case format
        
    Returns:
        String in PascalCase format
    """
    if not name:
        return ""
    
    components = name.split('_')
    return ''.join(word.capitalize() for word in components)

def generate_unique_id(namespace: list[str], *components: str) -> str:
    """Generate a unique identifier string from a namespace and optional suffix components.

    The unique id format concatenates namespace segments and extra components with double underscores
    and replaces any remaining slashes with double underscores for consistency.

    Args:
        namespace: Ordered list of namespace segments (already split, no leading slash).
        *components: Optional additional name parts to append for specificity.

    Returns:
        A sanitized unique identifier string stable across nodes.
    """
    parts: list[str] = []
    if namespace:
        parts.extend(namespace)
    for comp in components:
        if comp:
            parts.append(str(comp))
    raw = "__".join(parts)
    return raw.replace("/", "__")