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
from typing import List, Dict, Any, TypeVar, Optional
from ..models.config import SystemConfig, NodeConfig, ModuleConfig

logger = logging.getLogger(__name__)

class InheritanceResolver:
    """Base class for resolving inheritance merging and removals."""

    def _merge_list(self, base_list: List[Dict], override_list: List[Dict], key_field: str = None) -> List[Dict]:
        """
        Merge override_list into base_list.
        If key_field is provided, items with matching key_field in override_list replace those in base_list.
        Otherwise, items are appended.
        """
        if not override_list:
            return base_list or []

        merged_list = [item.copy() for item in (base_list or [])]

        if key_field:
            # Create a map for quick lookup and replacement
            base_map = {item[key_field]: i for i, item in enumerate(merged_list) if key_field in item}
            
            for item in override_list:
                key = item.get(key_field)
                if key and key in base_map:
                    # Replace existing item
                    merged_list[base_map[key]] = item
                else:
                    # Append new item
                    merged_list.append(item)
        else:
            # Simple append if no key_field is provided
            merged_list.extend(override_list)

        return merged_list

    def _remove_list(self, target_list: List[Dict], remove_specs: List[Dict], key_field: str = None) -> List[Dict]:
        """
        Remove items from target_list based on remove_specs.
        If key_field is provided, remove items where item[key_field] matches spec[key_field].
        Otherwise, remove items that match all properties in spec.
        """
        if not remove_specs or not target_list:
            return target_list

        result_list = []
        
        # Prepare lookup for key-based removal
        remove_keys = set()
        if key_field:
            for spec in remove_specs:
                if key_field in spec:
                    remove_keys.add(spec[key_field])

        for item in target_list:
            should_remove = False
            if key_field:
                if item.get(key_field) in remove_keys:
                    should_remove = True
            else:
                # Subset match: checks if any spec matches the item
                for spec in remove_specs:
                    # Check if spec is a subset of item
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
        for spec in merge_specs:
            field = spec['field']
            key_field = spec['key_field']
            
            # Get current list from object
            base_list = getattr(config_object, field)
            
            # Get override list from yaml
            override_list = config_yaml.get(field, [])
            
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


class SystemInheritanceResolver(InheritanceResolver):
    """Resolver for System entity inheritance."""

    def resolve(self, system_config: SystemConfig, config_yaml: Dict[str, Any]):
        """
        Apply inheritance rules from config_yaml to system_config.
        Modifies system_config in-place.
        """
        merge_specs = [
            {'field': 'variables', 'key_field': 'name'},
            {'field': 'variable_files', 'key_field': None},
            {'field': 'modes', 'key_field': 'name'},
            {'field': 'parameter_sets', 'key_field': None},  # Parameter sets are appended
            {'field': 'components', 'key_field': 'component'},
            {'field': 'connections', 'key_field': None},
        ]
        self._resolve_merges(system_config, config_yaml, merge_specs)

        # Apply removals if 'remove' section exists
        remove_config = config_yaml.get('remove', {})
        if remove_config:
            self._apply_removals(system_config, remove_config)

    def _apply_removals(self, system_config: SystemConfig, remove_config: Dict[str, Any]):
        remove_specs = [
            {'field': 'modes', 'key_field': 'name'},
            {'field': 'parameter_sets', 'key_field': None},  # Remove parameter sets by value
            {'field': 'components', 'key_field': 'component'},
            {'field': 'variables', 'key_field': 'name'},
            {'field': 'connections', 'key_field': None},
        ]
        self._resolve_removals(system_config, remove_config, remove_specs)


class NodeInheritanceResolver(InheritanceResolver):
    """Resolver for Node entity inheritance."""

    def resolve(self, node_config: NodeConfig, config_yaml: Dict[str, Any]):
        """
        Apply inheritance rules from config_yaml to node_config.
        Modifies node_config in-place.
        """
        # 1. Launch (dict merge)
        if 'launch' in config_yaml:
             if node_config.launch is None:
                 node_config.launch = {}
             node_config.launch.update(config_yaml['launch'])

        merge_specs = [
            {'field': 'inputs', 'key_field': 'name'},
            {'field': 'outputs', 'key_field': 'name'},
            {'field': 'parameter_files', 'key_field': 'name'},
            {'field': 'parameters', 'key_field': 'name'},
            {'field': 'processes', 'key_field': 'name'},
        ]
        self._resolve_merges(node_config, config_yaml, merge_specs)

        # Apply removals if 'remove' section exists
        remove_config = config_yaml.get('remove', {})
        if remove_config:
            self._apply_removals(node_config, remove_config)

    def _apply_removals(self, node_config: NodeConfig, remove_config: Dict[str, Any]):
        remove_specs = [
            {'field': 'inputs', 'key_field': 'name'},
            {'field': 'outputs', 'key_field': 'name'},
            {'field': 'parameter_files', 'key_field': 'name'},
            {'field': 'parameters', 'key_field': 'name'},
            {'field': 'processes', 'key_field': 'name'},
        ]
        self._resolve_removals(node_config, remove_config, remove_specs)


class ModuleInheritanceResolver(InheritanceResolver):
    """Resolver for Module entity inheritance."""

    def resolve(self, module_config: ModuleConfig, config_yaml: Dict[str, Any]):
        """
        Apply inheritance rules from config_yaml to module_config.
        Modifies module_config in-place.
        """
        merge_specs = [
            {'field': 'instances', 'key_field': 'instance'},
            {'field': 'connections', 'key_field': None},
        ]
        self._resolve_merges(module_config, config_yaml, merge_specs)

        # Merge external_interfaces
        if 'external_interfaces' in config_yaml:
            self._resolve_external_interfaces(module_config, config_yaml['external_interfaces'])

        # Apply removals if 'remove' section exists
        remove_config = config_yaml.get('remove', {})
        if remove_config:
            self._apply_removals(module_config, remove_config)

    def _resolve_external_interfaces(self, module_config: ModuleConfig, overrides: Dict[str, Any]):
        # Ensure base is initialized
        if not module_config.external_interfaces:
            module_config.external_interfaces = {}
        
        # We expect external_interfaces to be a dict with 'input' and 'output' lists
        if not isinstance(module_config.external_interfaces, dict):
            module_config.external_interfaces = {}

        for interface_type in ['input', 'output']:
            if interface_type in overrides:
                base_list = module_config.external_interfaces.get(interface_type, [])
                override_list = overrides[interface_type]
                # Merge lists using 'name' as key
                merged = self._merge_list(base_list, override_list, key_field='name')
                module_config.external_interfaces[interface_type] = merged

    def _apply_removals(self, module_config: ModuleConfig, remove_config: Dict[str, Any]):
        remove_specs = [
            {'field': 'instances', 'key_field': 'instance'},
            {'field': 'connections', 'key_field': None},
        ]
        self._resolve_removals(module_config, remove_config, remove_specs)

        if 'external_interfaces' in remove_config:
            self._remove_external_interfaces(module_config, remove_config['external_interfaces'])

    def _remove_external_interfaces(self, module_config: ModuleConfig, remove_specs: Dict[str, Any]):
        if not module_config.external_interfaces or not isinstance(module_config.external_interfaces, dict):
            return

        for interface_type in ['input', 'output']:
            if interface_type in remove_specs:
                target_list = module_config.external_interfaces.get(interface_type, [])
                specs = remove_specs[interface_type]
                # Remove items using 'name' as key
                result = self._remove_list(target_list, specs, key_field='name')
                module_config.external_interfaces[interface_type] = result
