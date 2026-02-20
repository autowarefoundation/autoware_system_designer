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

import logging
from typing import List, Dict, Any, TypeVar, Optional
from ...models.config import SystemConfig, NodeConfig, ModuleConfig
from .connection_utils import filter_connections_by_removed_entities

logger = logging.getLogger(__name__)

class VariantResolver:
    """Base class for resolving variant merging and removals."""

    def _merge_list(self, base_list: List[Dict], override_list: List[Dict], key_field: str = None) -> List[Dict]:
        """
        Merge override_list into base_list.
        If key_field is provided, items with matching key_field in override_list replace those in base_list.
        Otherwise, items are appended.
        """
        if not override_list:
            return base_list or []

        merged_list = [item.copy() if isinstance(item, dict) else item for item in (base_list or [])]

        if key_field:
            # Create a map for quick lookup and replacement
            base_map = {
                item[key_field]: i
                for i, item in enumerate(merged_list)
                if isinstance(item, dict) and key_field in item
            }
            
            for item in override_list:
                if isinstance(item, dict):
                    key = item.get(key_field)
                    if key and key in base_map:
                        # Replace existing item
                        merged_list[base_map[key]] = item
                    else:
                        # Append new item
                        merged_list.append(item)
                else:
                    # For non-dict items, always append
                    merged_list.append(item)
        else:
            # Simple append if no key_field is provided
            merged_list.extend(override_list)

        return merged_list

    def _remove_list(self, target_list: List[Any], remove_specs: List[Any], key_field: str = None) -> List[Any]:
        """
        Remove items from target_list based on remove_specs.
        If key_field is provided, remove items where item[key_field] matches spec[key_field].
        Otherwise, remove items that match all properties in spec.
        """
        if not remove_specs or not target_list:
            return target_list

        result_list = []
        
        # Prepare lookup for key-based removal (dict items only)
        remove_keys = set()
        if key_field:
            for spec in remove_specs:
                if isinstance(spec, dict) and key_field in spec:
                    remove_keys.add(spec[key_field])

        # Prepare lookup for scalar removals
        scalar_remove_values = {spec for spec in remove_specs if not isinstance(spec, dict)}

        for item in target_list:
            should_remove = False
            if key_field:
                if isinstance(item, dict) and item.get(key_field) in remove_keys:
                    should_remove = True
                elif not isinstance(item, dict) and item in scalar_remove_values:
                    should_remove = True
            else:
                if not isinstance(item, dict):
                    if item in scalar_remove_values:
                        should_remove = True
                else:
                    # Subset match: checks if any dict spec matches the item
                    for spec in remove_specs:
                        if not isinstance(spec, dict):
                            continue
                        if all(item.get(k) == v for k, v in spec.items()):
                            should_remove = True
                            break
            
            if not should_remove:
                result_list.append(item)

        return result_list

    def _resolve_merges(self, config_object: Any, config_yaml: Dict[str, Any], merge_specs: List[Dict[str, Any]]):
        """
        Generic merge resolver.
        merge_specs format:
        [
            {'field': 'variables', 'key_field': 'name'},
            {'field': 'connections', 'key_field': None}
        ]
        """
        override_config = config_yaml.get('override', {})
        for spec in merge_specs:
            field = spec['field']
            key_field = spec['key_field']
            
            # Get current list from object
            base_list = getattr(config_object, field)
            
            # Get override list from yaml
            override_list = override_config.get(field, [])
            
            # Merge
            merged_list = self._merge_list(base_list, override_list, key_field)
            
            # Set back to object
            setattr(config_object, field, merged_list)

    def _resolve_removals(self, config_object: Any, remove_config: Dict[str, Any], remove_specs: List[Dict[str, Any]]):
        """
        Generic removal resolver.
        remove_specs format: same as merge_specs
        """
        for spec in remove_specs:
            field = spec['field']
            key_field = spec['key_field']
            
            if field in remove_config:
                target_list = getattr(config_object, field)
                remove_items = remove_config[field]
                
                result_list = self._remove_list(target_list, remove_items, key_field)
                setattr(config_object, field, result_list)


class SystemVariantResolver(VariantResolver):
    """Resolver for System entity variants."""

    def resolve(self, system_config: SystemConfig, config_yaml: Dict[str, Any]):
        """
        Apply variant rules from config_yaml to system_config.
        Modifies system_config in-place.
        """
        # Apply removals if 'remove' section exists
        remove_config = config_yaml.get('remove', {})
        if remove_config:
            self._apply_removals(system_config, remove_config)

        override_config = config_yaml.get('override', {})
        merge_specs = [
            {'field': 'variables', 'key_field': 'name'},
            {'field': 'variable_files', 'key_field': None},
            {'field': 'modes', 'key_field': 'name'},
            {'field': 'parameter_sets', 'key_field': None},  # Parameter sets are appended
            {'field': 'components', 'key_field': 'name'},
            {'field': 'connections', 'key_field': None},
        ]
        self._resolve_merges(system_config, config_yaml, merge_specs)

        # Handle mode_configs manual merge from override
        if system_config.modes:
            if system_config.mode_configs is None:
                system_config.mode_configs = {}
            
            # mode names are already merged in system_config.modes
            mode_names = [m.get('name') for m in system_config.modes if isinstance(m, dict) and 'name' in m]
            
            for mode_name in mode_names:
                if mode_name in override_config:
                    # Overwrite/Update mode config from override
                    system_config.mode_configs[mode_name] = override_config[mode_name]


    def _apply_removals(self, system_config: SystemConfig, remove_config: Dict[str, Any]):
        if 'components' in remove_config:
            removed_names = [
                spec if isinstance(spec, str) else spec.get('name')
                for spec in remove_config.get('components', [])
                if (isinstance(spec, str) and spec) or (isinstance(spec, dict) and spec.get('name'))
            ]
            if removed_names and system_config.connections:
                system_config.connections = filter_connections_by_removed_entities(
                    system_config.connections, removed_names
                )

        remove_specs = [
            {'field': 'modes', 'key_field': 'name'},
            {'field': 'parameter_sets', 'key_field': None},  # Remove parameter sets by value
            {'field': 'components', 'key_field': 'name'},
            {'field': 'variables', 'key_field': 'name'},
            {'field': 'connections', 'key_field': None},
        ]
        self._resolve_removals(system_config, remove_config, remove_specs)


class NodeVariantResolver(VariantResolver):
    """Resolver for Node entity variants."""

    def resolve(self, node_config: NodeConfig, config_yaml: Dict[str, Any]):
        """
        Apply variant rules from config_yaml to node_config.
        Modifies node_config in-place.
        """
        # Apply removals first
        remove_config = config_yaml.get('remove', {})
        if remove_config:
            self._apply_removals(node_config, remove_config)

        override_config = config_yaml.get('override', {})

        # 1. Launch (dict merge)
        if 'launch' in override_config:
            if node_config.launch is None:
                node_config.launch = {}
            node_config.launch.update(override_config['launch'])

        merge_specs = [
            {'field': 'inputs', 'key_field': 'name'},
            {'field': 'outputs', 'key_field': 'name'},
            {'field': 'param_files', 'key_field': 'name'},
            {'field': 'param_values', 'key_field': 'name'},
            {'field': 'processes', 'key_field': 'name'},
        ]
        self._resolve_merges(node_config, config_yaml, merge_specs)

    def _apply_removals(self, node_config: NodeConfig, remove_config: Dict[str, Any]):
        remove_specs = [
            {'field': 'inputs', 'key_field': 'name'},
            {'field': 'outputs', 'key_field': 'name'},
            {'field': 'param_files', 'key_field': 'name'},
            {'field': 'param_values', 'key_field': 'name'},
            {'field': 'processes', 'key_field': 'name'},
        ]
        self._resolve_removals(node_config, remove_config, remove_specs)


class ModuleVariantResolver(VariantResolver):
    """Resolver for Module entity variants."""

    def resolve(self, module_config: ModuleConfig, config_yaml: Dict[str, Any]):
        """
        Apply variant rules from config_yaml to module_config.
        Modifies module_config in-place.
        """
        # Apply removals first
        remove_config = config_yaml.get('remove', {})
        if remove_config:
            self._apply_removals(module_config, remove_config)

        override_config = config_yaml.get('override', {})
        
        merge_specs = [
            {'field': 'instances', 'key_field': 'name'},
            {'field': 'inputs', 'key_field': 'name'},
            {'field': 'outputs', 'key_field': 'name'},
            {'field': 'connections', 'key_field': None},
        ]
        self._resolve_merges(module_config, config_yaml, merge_specs)

    def _apply_removals(self, module_config: ModuleConfig, remove_config: Dict[str, Any]):
        if 'instances' in remove_config:
            removed_names = [
                spec.get('name')
                for spec in remove_config.get('instances', [])
                if isinstance(spec, dict) and spec.get('name')
            ]
            if removed_names and module_config.connections:
                module_config.connections = filter_connections_by_removed_entities(
                    module_config.connections, removed_names
                )

        remove_specs = [
            {'field': 'instances', 'key_field': 'name'},
            {'field': 'inputs', 'key_field': 'name'},
            {'field': 'outputs', 'key_field': 'name'},
            {'field': 'connections', 'key_field': None},
        ]
        self._resolve_removals(module_config, remove_config, remove_specs)
