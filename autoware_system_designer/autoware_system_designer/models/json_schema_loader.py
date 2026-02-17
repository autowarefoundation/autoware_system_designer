# Copyright 2026 TIER IV, inc.
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

"""JSON Schema loader for autoware_system_design_format validation."""

import json
from pathlib import Path
from typing import Dict, Optional

from ..utils.format_version import SemanticVersion, parse_format_version


# Schema cache to avoid reloading files
_SCHEMA_CACHE: Dict[str, dict] = {}


def get_schema_path(entity_type: str, version: str) -> Path:
    """Get the path to a JSON Schema file for the given entity type and version.
    
    Args:
        entity_type: Entity type (node, module, system, parameter_set)
        version: Format version string (e.g., "0.2.0")
        
    Returns:
        Path to the schema file
    """
    # Get the directory containing this module
    schema_dir = Path(__file__).parent.parent / "schema"
    return schema_dir / f"{entity_type}-v{version}.json"


def resolve_schema_version(entity_type: str, version: str) -> str:
    """Resolve the schema version, using the largest available schema within the same major version.
    
    Version resolution rules:
    - Major version must match exactly
    - If exact version exists, use it
    - Otherwise, use the largest available minor version within the same major version
      (supports both newer configs checking against older schemas, and older configs
      checking against newer schemas for backward compatibility)
    
    Args:
        entity_type: Entity type (node, module, system, parameter_set)
        version: Format version string (e.g., "0.2.0")
        
    Returns:
        Resolved version string that exists, or the original version if none found
    """
    try:
        parsed_version = parse_format_version(version)
    except Exception:
        # If version parsing fails, return original
        return version
    
    # Try the exact version first
    schema_path = get_schema_path(entity_type, version)
    if schema_path.exists():
        return version
    
    # Find all available schema files for this entity type and major version
    schema_dir = Path(__file__).parent.parent / "schema"
    available_versions = []
    
    # Look for all schema files matching the pattern
    pattern = f"{entity_type}-v{parsed_version.major}.*.json"
    for schema_file in schema_dir.glob(pattern):
        # Extract version from filename: entity_type-vX.Y.Z.json
        try:
            version_part = schema_file.stem.replace(f"{entity_type}-v", "")
            file_version = parse_format_version(version_part)
            # Only consider versions with matching major version
            if file_version.major == parsed_version.major:
                available_versions.append(file_version)
        except Exception:
            # Skip files that don't match the version pattern
            continue
    
    if not available_versions:
        # No schemas found for this major version, return original (will cause error)
        return version
    
    # Use the largest available version (highest minor, then highest patch)
    largest_version = max(available_versions, key=lambda v: (v.minor, v.patch))
    return str(largest_version)


def load_schema(entity_type: str, version: str) -> dict:
    """Load a JSON Schema file for the given entity type and version.
    
    Args:
        entity_type: Entity type (node, module, system, parameter_set)
        version: Format version string (e.g., "0.2.0")
        
    Returns:
        Schema dictionary
        
    Raises:
        FileNotFoundError: If the schema file doesn't exist
        json.JSONDecodeError: If the schema file is invalid JSON
    """
    # Resolve version (may fall back to earlier minor version)
    resolved_version = resolve_schema_version(entity_type, version)
    
    # Check cache
    cache_key = f"{entity_type}-v{resolved_version}"
    if cache_key in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[cache_key]
    
    # Load schema file
    schema_path = get_schema_path(entity_type, resolved_version)
    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema file not found for {entity_type} version {version} "
            f"(resolved to {resolved_version}): {schema_path}"
        )
    
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"Invalid JSON in schema file {schema_path}: {e.msg}",
            e.doc,
            e.pos,
        ) from e
    
    # Cache the schema
    _SCHEMA_CACHE[cache_key] = schema
    
    return schema


def clear_cache() -> None:
    """Clear the schema cache. Useful for testing."""
    global _SCHEMA_CACHE
    _SCHEMA_CACHE.clear()
