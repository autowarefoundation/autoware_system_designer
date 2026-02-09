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

from typing import List, Dict, Optional, Type, Callable, Any
import logging
from pathlib import Path

import copy
from ...models.parsing.data_parser import ConfigParser
from ...models.config import Config, ConfigType, NodeConfig, ModuleConfig, ParameterSetConfig, SystemConfig, ConfigSubType
from ...exceptions import ValidationError, NodeConfigurationError, ModuleConfigurationError, ParameterConfigurationError
from ..resolution.variant_resolver import SystemVariantResolver, NodeVariantResolver, ModuleVariantResolver, VariantResolver

from ...models.parsing.data_validator import entity_name_decode
from ...file_io.source_location import SourceLocation, format_source

logger = logging.getLogger(__name__)

class ConfigRegistry:
    """Collection for managing multiple entity data structures with efficient lookup methods."""
    
    def __init__(self, config_yaml_file_paths: List[str], package_paths: Dict[str, str] = None, file_package_map: Dict[str, str] = None, workspace_config: List[Dict[str, Any]] = None):
        # Replace list with dict as primary storage
        self.entities: Dict[str, Config] = {}  # full_name → Config
        self._type_map: Dict[str, Dict[str, Config]] = {
            ConfigType.NODE: {},
            ConfigType.MODULE: {},
            ConfigType.PARAMETER_SET: {},
            ConfigType.SYSTEM: {}
        }
        self.package_paths = package_paths or {}
        self.file_package_map = file_package_map or {}
        self._package_source_paths: Dict[str, Optional[str]] = {}
        # Name of the package currently being built/exported (deployment package).
        # When set, build-time source fallbacks should be restricted to this package only.
        self.deployment_package_name: Optional[str] = None

        # Workspace provider resolution map: provider -> "source" | "installed"
        self._provider_resolution_map: Dict[str, str] = {}
        if workspace_config:
            for entry in workspace_config:
                if isinstance(entry, dict) and "provider" in entry and "resolution" in entry:
                    self._provider_resolution_map[entry["provider"]] = entry["resolution"]
        
        self.parser = ConfigParser()
        self._load_entities(config_yaml_file_paths)
    
    def _load_entities(self, config_yaml_file_paths: List[str]) -> None:
        """Load entities from configuration files."""
        for file_path in config_yaml_file_paths:
            logger.debug(f"Loading entity from: {file_path}")
            
            try:
                entity_data = self.parser.parse_entity_file(file_path)

                # Set package name if available
                if entity_data.file_path and str(entity_data.file_path) in self.file_package_map:
                    entity_data.package = self.file_package_map[str(entity_data.file_path)]

                # For node entities, resolve the provider against the workspace config
                if isinstance(entity_data, NodeConfig) and entity_data.package_provider:
                    resolution = self._provider_resolution_map.get(entity_data.package_provider)
                    if resolution:
                        entity_data.package_resolution = resolution
                
                # Check for duplicates
                if entity_data.full_name in self.entities:
                    existing = self.entities[entity_data.full_name]
                    raise ValidationError(
                        f"Duplicate entity '{entity_data.full_name}' found:\n"
                        f"  New: {entity_data.file_path}\n"
                        f"  Existing: {existing.file_path}"
                    )
                
                # Add to collections
                self.entities[entity_data.full_name] = entity_data
                self._type_map[entity_data.entity_type][entity_data.name] = entity_data
                
            except Exception as e:
                src = SourceLocation(file_path=Path(file_path))
                logger.error(f"Failed to load entity from {file_path}: {e}{format_source(src)}")
                raise
    
    def get(self, name: str, default=None) -> Optional[Config]:
        """Get entity by name with default value."""
        return self.entities.get(name, default)
    
    def _get_entity_with_base(self, 
                                     name: str, 
                                     config_type: str, 
                                     error_cls: Type[Exception],
                                     resolver_cls: Optional[Type[VariantResolver]] = None,
                                     recursive_getter: Optional[Callable[[str], Config]] = None) -> Config:
        """
        Generic method to get an entity and resolve base/variant if applicable.
        """
        entity = self._type_map[config_type].get(name)
        
        # If not found, try decoding the name (e.g. MyNode.node -> MyNode)
        if entity is None and "." in name:
            try:
                decoded_name, entity_type = entity_name_decode(name)
                if entity_type == config_type:
                    entity = self._type_map[config_type].get(decoded_name)
            except ValidationError:
                pass
        
        if entity is None:
            available = list(self._type_map[config_type].keys())
            raise error_cls(f"{config_type.capitalize()} '{name}' not found. Available {config_type}s: {available}")
        
        if entity.sub_type == ConfigSubType.VARIANT:
            if not resolver_cls or not recursive_getter:
                # Variant requested but no resolver provided, return as is (or could raise error)
                return entity

            # Get parent name
            base_target = entity.config.get('base')
            if not base_target:
                 # Should have been validated, but fallback
                 return entity

            # Resolve parent (recursive)
            parent = recursive_getter(base_target)
            
            # Create a deep copy of the parent to serve as the base for this entity
            # This ensures we don't modify the parent object
            resolved_entity = copy.deepcopy(parent)
            
            # Update the identity of the resolved entity to match the current entity
            resolved_entity.name = entity.name
            resolved_entity.full_name = entity.full_name
            resolved_entity.file_path = entity.file_path
            resolved_entity.package = entity.package
            resolved_entity.sub_type = entity.sub_type
            resolved_entity.config = entity.config # Keep original config with overrides
            
            # Apply overrides from this entity's config
            resolver = resolver_cls()
            resolver.resolve(resolved_entity, entity.config)
            
            return resolved_entity

        return entity

    # Enhanced methods for type-safe entity access
    def get_node(self, name: str) -> NodeConfig:
        """Get a node entity by name."""
        return self._get_entity_with_base(
            name, 
            ConfigType.NODE, 
            NodeConfigurationError, 
            NodeVariantResolver, 
            self.get_node
        )
    
    def get_module(self, name: str) -> ModuleConfig:
        """Get a module entity by name."""
        return self._get_entity_with_base(
            name,
            ConfigType.MODULE,
            ModuleConfigurationError,
            ModuleVariantResolver,
            self.get_module
        )
    
    def get_parameter_set(self, name: str) -> ParameterSetConfig:
        """Get a parameter set entity by name."""
        return self._get_entity_with_base(
            name,
            ConfigType.PARAMETER_SET,
            ParameterConfigurationError
        )
    
    def get_system(self, name: str) -> SystemConfig:
        """Get an system entity by name. Resolves base/variant if applicable."""
        return self._get_entity_with_base(
            name,
            ConfigType.SYSTEM,
            ValidationError, # System uses ValidationError in original code, keeping it
            SystemVariantResolver,
            self.get_system
        )
    
    def get_entity_by_type(self, name: str, entity_type: str) -> Config:
        """Get an entity by name and type."""
        if entity_type == ConfigType.NODE:
            return self.get_node(name)
        elif entity_type == ConfigType.MODULE:
            return self.get_module(name)
        elif entity_type == ConfigType.PARAMETER_SET:
            return self.get_parameter_set(name)
        elif entity_type == ConfigType.SYSTEM:
            return self.get_system(name)
        else:
            raise ValidationError(f"Unknown entity type: {entity_type}")

    def get_package_path(self, package_name: str) -> Optional[str]:
        """Get package path by package name."""
        return self.package_paths.get(package_name)

    def get_package_source_path(self, package_name: str) -> Optional[str]:
        """Best-effort lookup of a package's *source* directory.

        This is intentionally independent from the install/share path stored in package_paths.
        It is used to avoid false negatives during build-time checks, where install/share may
        not yet contain installed resources (e.g., config/*.yaml).
        """
        if not package_name:
            return None
        if package_name in self._package_source_paths:
            return self._package_source_paths[package_name]

        # Find any design/config yaml that belongs to this package, then walk up to package.xml.
        for file_path, pkg in self.file_package_map.items():
            if pkg != package_name:
                continue
            try:
                current = Path(file_path).resolve().parent
                while True:
                    if (current / "package.xml").exists():
                        self._package_source_paths[package_name] = str(current)
                        return self._package_source_paths[package_name]
                    if current.parent == current:
                        break
                    current = current.parent
            except Exception:
                continue

        # Cache negative result to avoid repeated scans.
        self._package_source_paths[package_name] = None
        return None

    def get_provider_resolution(self, provider: str) -> Optional[str]:
        """Get the resolution type for a given provider.

        Args:
            provider: The provider identifier (e.g., 'autoware', 'ros', 'dummy').

        Returns:
            'source' if the provider's packages are built from source in the workspace,
            'installed' if they are pre-built library packages,
            or None if the provider is not in the workspace config.
        """
        return self._provider_resolution_map.get(provider)
