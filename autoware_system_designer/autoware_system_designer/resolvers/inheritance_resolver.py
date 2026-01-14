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
from ..models.config import SystemConfig, NodeConfig

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


class SystemInheritanceResolver(InheritanceResolver):
    """Resolver for System entity inheritance."""

    def resolve(self, system_config: SystemConfig, config_yaml: Dict[str, Any]):
        """
        Apply inheritance rules from config_yaml to system_config.
        Modifies system_config in-place.
        """
        # 1. Variables (key='name')
        system_config.variables = self._merge_list(
            system_config.variables, 
            config_yaml.get('variables', []), 
            key_field='name'
        )
        
        # 2. Variable Files (append only)
        system_config.variable_files = self._merge_list(
            system_config.variable_files, 
            config_yaml.get('variable_files', []), 
            key_field=None
        )

        # 3. Modes (key='name')
        system_config.modes = self._merge_list(
            system_config.modes,
            config_yaml.get('modes', []),
            key_field='name'
        )

        # 4. Components (key='component')
        system_config.components = self._merge_list(
            system_config.components,
            config_yaml.get('components', []),
            key_field='component'
        )

        # 5. Connections (append only)
        system_config.connections = self._merge_list(
            system_config.connections,
            config_yaml.get('connections', []),
            key_field=None
        )

        # Apply removals if 'remove' section exists
        remove_config = config_yaml.get('remove', {})
        if remove_config:
            self._apply_removals(system_config, remove_config)

    def _apply_removals(self, system_config: SystemConfig, remove_config: Dict[str, Any]):
        # 1. Remove Modes (key='name')
        if 'modes' in remove_config:
            # Capture removed mode names for component cleanup
            removed_mode_names = [m.get('name') for m in remove_config['modes'] if 'name' in m]
            
            system_config.modes = self._remove_list(
                system_config.modes,
                remove_config['modes'],
                key_field='name'
            )
            
            # Cleanup components referencing removed modes
            self._cleanup_components_modes(system_config, removed_mode_names)
        
        # 2. Remove Components (key='component')
        if 'components' in remove_config:
            system_config.components = self._remove_list(
                system_config.components,
                remove_config['components'],
                key_field='component'
            )

        # 3. Remove Variables (key='name')
        if 'variables' in remove_config:
            system_config.variables = self._remove_list(
                system_config.variables,
                remove_config['variables'],
                key_field='name'
            )

        # 4. Remove Connections (subset match)
        if 'connections' in remove_config:
            system_config.connections = self._remove_list(
                system_config.connections,
                remove_config['connections'],
                key_field=None
            )

    def _cleanup_components_modes(self, system_config: SystemConfig, removed_modes: List[str]):
        """Remove removed modes from components' mode lists.
           If a component was specific to a removed mode and has no modes left, remove the component.
        """
        if not system_config.components:
            return

        components_to_keep = []
        removed_set = set(removed_modes)

        for comp in system_config.components:
            mode_field = comp.get('mode')
            
            # If mode is not specified (None or empty), it applies to all modes.
            # We don't need to change anything, as it will apply to whatever modes remain.
            if not mode_field:
                components_to_keep.append(comp)
                continue
                
            # Normalize to list
            current_modes = mode_field if isinstance(mode_field, list) else [mode_field]
            
            # Check intersection
            if not any(m in removed_set for m in current_modes):
                components_to_keep.append(comp)
                continue
                
            # Filter out removed modes
            new_modes = [m for m in current_modes if m not in removed_set]
            
            if new_modes:
                # Update component with new modes
                comp['mode'] = new_modes
                components_to_keep.append(comp)
            else:
                # Component has no modes left (and it was not "all modes" initially)
                # Drop the component
                logger.info(f"Dropping component '{comp.get('component')}' as all its modes {current_modes} were removed.")
                
        system_config.components = components_to_keep

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

        # 2. Inputs (key='name')
        node_config.inputs = self._merge_list(
            node_config.inputs,
            config_yaml.get('inputs', []),
            key_field='name'
        )

        # 3. Outputs (key='name')
        node_config.outputs = self._merge_list(
            node_config.outputs,
            config_yaml.get('outputs', []),
            key_field='name'
        )
        
        # 4. Parameter Files (key='name')
        node_config.parameter_files = self._merge_list(
            node_config.parameter_files,
            config_yaml.get('parameter_files', []),
            key_field='name'
        )

        # 5. Parameters (key='name')
        node_config.parameters = self._merge_list(
            node_config.parameters,
            config_yaml.get('parameters', []),
            key_field='name'
        )
        
        # 6. Processes (key='name')
        node_config.processes = self._merge_list(
            node_config.processes,
            config_yaml.get('processes', []),
            key_field='name'
        )

        # Apply removals if 'remove' section exists
        remove_config = config_yaml.get('remove', {})
        if remove_config:
            self._apply_removals(node_config, remove_config)

    def _apply_removals(self, node_config: NodeConfig, remove_config: Dict[str, Any]):
        # 1. Remove Inputs
        if 'inputs' in remove_config:
            node_config.inputs = self._remove_list(
                node_config.inputs,
                remove_config['inputs'],
                key_field='name'
            )

        # 2. Remove Outputs
        if 'outputs' in remove_config:
            node_config.outputs = self._remove_list(
                node_config.outputs,
                remove_config['outputs'],
                key_field='name'
            )

        # 3. Remove Parameter Files
        if 'parameter_files' in remove_config:
            node_config.parameter_files = self._remove_list(
                node_config.parameter_files,
                remove_config['parameter_files'],
                key_field='name'
            )

        # 4. Remove Parameters
        if 'parameters' in remove_config:
            node_config.parameters = self._remove_list(
                node_config.parameters,
                remove_config['parameters'],
                key_field='name'
            )

        # 5. Remove Processes
        if 'processes' in remove_config:
            node_config.processes = self._remove_list(
                node_config.processes,
                remove_config['processes'],
                key_field='name'
            )
