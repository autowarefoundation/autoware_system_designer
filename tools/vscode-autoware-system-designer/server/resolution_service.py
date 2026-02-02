#!/usr/bin/env python3

import logging
from typing import Optional, List, Set, Tuple

from autoware_system_designer.models.config import Config, ConfigType
from registry_manager import RegistryManager

logger = logging.getLogger(__name__)

class ResolutionService:
    """Service for resolving entity connections and types recursively."""

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager
        # To prevent infinite loops in cyclic graphs (though system design should be acyclic)
        self._visited: Set[str] = set()

    def resolve_port_type(self, config: Config, port_type: str, port_name: str) -> Optional[str]:
        """
        recursively resolve the type of a port.
        
        Args:
            config: The entity configuration.
            port_type: 'input' or 'output'.
            port_name: The name of the port.
            
        Returns:
            The message type string, or None if not found/resolvable.
        """
        self._visited = set()
        return self._resolve_type_recursive(config, port_type, port_name)

    def _resolve_type_recursive(self, config: Config, port_type: str, port_name: str) -> Optional[str]:
        # Cycle detection
        key = f"{config.full_name}:{port_type}:{port_name}"
        if key in self._visited:
            return None
        self._visited.add(key)

        if config.entity_type == ConfigType.NODE:
            return self._get_node_port_type(config, port_type, port_name)
        
        elif config.entity_type in [ConfigType.MODULE, ConfigType.SYSTEM]:
            return self._resolve_composite_port_type(config, port_type, port_name)
            
        return None

    def _get_node_port_type(self, config: Config, port_type: str, port_name: str) -> Optional[str]:
        """Get type directly from node definition."""
        ports = []
        if port_type == 'input':
            ports = config.inputs or []
        elif port_type == 'output':
            ports = config.outputs or []
            
        for port in ports:
            if port.get('name') == port_name:
                return port.get('message_type')
        return None

    def _resolve_composite_port_type(self, config: Config, port_type: str, port_name: str) -> Optional[str]:
        """Resolve type for Module or System by tracing connections."""
        connections = config.connections or []
        
        # Strategy:
        # If we are looking for the type of an INPUT port of a Module:
        # It is determined by what it connects TO inside the module.
        # e.g. input.A -> instance.input.B
        # type(input.A) == type(instance.entity.input.B)
        
        # If we are looking for the type of an OUTPUT port of a Module:
        # It is determined by what connects TO it inside the module.
        # e.g. instance.output.B -> output.A
        # type(output.A) == type(instance.entity.output.B)

        candidate_types = set()

        if port_type == 'input':
            # Find connections starting from "input.port_name"
            source_ref = f"input.{port_name}"
            for conn in connections:
                if conn.get('from') == source_ref:
                    to_ref = conn.get('to')
                    resolved_type = self._resolve_target_ref_type(config, to_ref)
                    if resolved_type:
                        candidate_types.add(resolved_type)

        elif port_type == 'output':
            # Find connections ending at "output.port_name"
            target_ref = f"output.{port_name}"
            for conn in connections:
                if conn.get('to') == target_ref:
                    from_ref = conn.get('from')
                    resolved_type = self._resolve_source_ref_type(config, from_ref)
                    if resolved_type:
                        candidate_types.add(resolved_type)

        if not candidate_types:
            return None
        
        # If multiple branches have different types, that's a conflict, but for now return one
        # Ideally should return the first valid one
        return list(candidate_types)[0]

    def _resolve_target_ref_type(self, current_config: Config, ref: str) -> Optional[str]:
        """Resolve the type of a target reference (e.g. instance.input.X)."""
        if not ref:
            return None
        
        parts = ref.split('.')
        # Expecting: instance_name.input.port_name
        if len(parts) < 3:
            return None
        
        # Handle wildcard? skip for now
        
        instance_name = parts[0]
        direction = parts[1] # should be 'input'
        port_name = parts[2]
        
        if direction != 'input':
            return None # Can only connect to inputs
            
        entity_config = self.get_instance_entity(current_config, instance_name)
        if entity_config:
            return self._resolve_type_recursive(entity_config, 'input', port_name)
            
        return None

    def _resolve_source_ref_type(self, current_config: Config, ref: str) -> Optional[str]:
        """Resolve the type of a source reference (e.g. instance.output.X)."""
        if not ref:
            return None
            
        parts = ref.split('.')
        # Expecting: instance_name.output.port_name
        if len(parts) < 3:
            return None

        instance_name = parts[0]
        direction = parts[1] # should be 'output'
        port_name = parts[2]
        
        if direction != 'output':
            return None
            
        entity_config = self.get_instance_entity(current_config, instance_name)
        if entity_config:
            return self._resolve_type_recursive(entity_config, 'output', port_name)
            
        return None

    def get_instance_entity(self, config: Config, instance_name: str) -> Optional[Config]:
        """Find the entity config for an instance."""
        entity_name = None
        
        if config.entity_type == ConfigType.MODULE:
            instances = config.instances or []
            for inst in instances:
                if inst.get('instance') == instance_name:
                    entity_name = inst.get('entity')
                    break
        elif config.entity_type == ConfigType.SYSTEM:
            components = config.components or []
            for comp in components:
                if comp.get('component') == instance_name:
                    entity_name = comp.get('entity')
                    break
        
        if entity_name:
            return self.registry_manager.get_entity(entity_name)
            
        return None
