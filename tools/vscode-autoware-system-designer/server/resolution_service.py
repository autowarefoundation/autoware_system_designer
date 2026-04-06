#!/usr/bin/env python3

import logging
from typing import List, Optional, Set, Tuple

from registry_manager import RegistryManager

from autoware_system_designer.parsing.config import Config, ConfigType
from autoware_system_designer.parsing.domain import PortDefinition

logger = logging.getLogger(__name__)


class ResolutionService:
    """Service for resolving entity connections and types recursively."""

    # Direction terms in YAML connection strings
    _INPUT_TERMS: Set[str] = {"subscriber", "client"}
    _OUTPUT_TERMS: Set[str] = {"publisher", "server"}

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

    def _get_node_port_type(
        self,
        config: Config,
        port_type: str,
        port_name: str,
        _seen_bases: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Get type directly from node definition, including variant overrides and base inheritance."""
        if port_type == "input":
            direct_ports = config.inputs or []
        elif port_type == "output":
            direct_ports = config.outputs or []
        else:
            direct_ports = []

        # Check direct ports first (typed PortDefinition objects)
        for port in direct_ports:
            if port.name == port_name:
                return port.message_type

        # For variant nodes, check override ports (stored as raw dicts in config.config["override"])
        raw = config.config if hasattr(config, "config") and isinstance(config.config, dict) else {}
        override = raw.get("override", {})
        if isinstance(override, dict):
            override_ports = override.get("inputs" if port_type == "input" else "outputs", []) or []
            for port in override_ports:
                if port.get("name") == port_name:
                    return port.get("message_type")

        # Traverse base chain with cycle detection
        base_name = config.base if hasattr(config, "base") else None
        if base_name:
            if _seen_bases is None:
                _seen_bases = set()
            if base_name in _seen_bases:
                return None
            _seen_bases.add(base_name)
            base_config = self.registry_manager.get_entity(base_name)
            if base_config:
                return self._get_node_port_type(base_config, port_type, port_name, _seen_bases)

        return None

    @staticmethod
    def _get_connection_refs(connection) -> Tuple[Optional[str], Optional[str]]:
        """Extract (from_ref, to_ref) from a connection entry.

        Connections are stored either as:
          - list/tuple: [from_str, to_str]  (the primary YAML format)
          - dict: {"from": from_str, "to": to_str}
        """
        if isinstance(connection, (list, tuple)) and len(connection) >= 2:
            return str(connection[0]), str(connection[1])
        elif isinstance(connection, dict):
            return connection.get("from"), connection.get("to")
        return None, None

    def _resolve_composite_port_type(self, config: Config, port_type: str, port_name: str) -> Optional[str]:
        """Resolve type for Module or System by tracing connections."""
        connections = config.connections or []

        # Strategy:
        # If we are looking for the type of an INPUT port of a Module:
        # It is determined by what it connects TO inside the module.
        # e.g. subscriber.A -> instance.subscriber.B
        # type(subscriber.A) == type(instance.entity.input.B)

        # If we are looking for the type of an OUTPUT port of a Module:
        # It is determined by what connects TO it inside the module.
        # e.g. instance.publisher.B -> publisher.A
        # type(publisher.A) == type(instance.entity.output.B)

        # Build all possible ref forms for this port across all direction term variants
        if port_type == "input":
            self_refs = {f"{term}.{port_name}" for term in self._INPUT_TERMS}
        else:
            self_refs = {f"{term}.{port_name}" for term in self._OUTPUT_TERMS}

        candidate_types = set()

        for conn in connections:
            from_ref, to_ref = self._get_connection_refs(conn)
            if from_ref is None or to_ref is None:
                continue

            if port_type == "input":
                # Find connections starting from this module's input port
                if from_ref in self_refs:
                    resolved_type = self._resolve_target_ref_type(config, to_ref)
                    if resolved_type:
                        candidate_types.add(resolved_type)

            elif port_type == "output":
                # Find connections ending at this module's output port
                if to_ref in self_refs:
                    resolved_type = self._resolve_source_ref_type(config, from_ref)
                    if resolved_type:
                        candidate_types.add(resolved_type)

        if not candidate_types:
            return None

        # If multiple branches have different types, that's a conflict, but for now return one
        return list(candidate_types)[0]

    def _resolve_target_ref_type(self, current_config: Config, ref: str) -> Optional[str]:
        """Resolve the type of a target reference (e.g. instance.subscriber.X or instance.input.X)."""
        if not ref:
            return None

        parts = ref.split(".")
        # Expecting: instance_name.direction.port_name
        if len(parts) < 3:
            return None

        instance_name = parts[0]
        direction = parts[1]
        port_name = parts[2]

        if direction not in self._INPUT_TERMS:
            return None  # Can only connect to inputs

        entity_config = self.get_instance_entity(current_config, instance_name)
        if entity_config:
            return self._resolve_type_recursive(entity_config, "input", port_name)

        return None

    def _resolve_source_ref_type(self, current_config: Config, ref: str) -> Optional[str]:
        """Resolve the type of a source reference (e.g. instance.publisher.X or instance.output.X)."""
        if not ref:
            return None

        parts = ref.split(".")
        # Expecting: instance_name.direction.port_name
        if len(parts) < 3:
            return None

        instance_name = parts[0]
        direction = parts[1]
        port_name = parts[2]

        if direction not in self._OUTPUT_TERMS:
            return None

        entity_config = self.get_instance_entity(current_config, instance_name)
        if entity_config:
            return self._resolve_type_recursive(entity_config, "output", port_name)

        return None

    def get_entity_inputs(self, config: Config, _seen: Optional[Set[str]] = None) -> List[PortDefinition]:
        """Get resolved input ports for a config, applying variant overrides and base inheritance."""
        if _seen is None:
            _seen = set()
        if config.full_name in _seen:
            return []
        _seen.add(config.full_name)

        inputs: List[PortDefinition] = list(config.inputs) if (hasattr(config, "inputs") and config.inputs) else []

        # Check override ports in raw config (for unresolved variants in LSP context)
        raw = config.config if hasattr(config, "config") and isinstance(config.config, dict) else {}
        override = raw.get("override", {})
        if isinstance(override, dict):
            override_inputs = override.get("inputs", []) or []
            if override_inputs:
                override_names = {p.get("name") for p in override_inputs if p.get("name")}
                inputs = [p for p in inputs if p.name not in override_names]
                inputs += [PortDefinition.from_dict(p) for p in override_inputs]

        base_name = config.base if hasattr(config, "base") else None
        if base_name:
            base_config = self.registry_manager.get_entity(base_name)
            if base_config:
                base_inputs = self.get_entity_inputs(base_config, _seen)
                existing_names = {p.name for p in inputs}
                inputs = inputs + [p for p in base_inputs if p.name not in existing_names]

        return inputs

    def get_entity_outputs(self, config: Config, _seen: Optional[Set[str]] = None) -> List[PortDefinition]:
        """Get resolved output ports for a config, applying variant overrides and base inheritance."""
        if _seen is None:
            _seen = set()
        if config.full_name in _seen:
            return []
        _seen.add(config.full_name)

        outputs: List[PortDefinition] = list(config.outputs) if (hasattr(config, "outputs") and config.outputs) else []

        # Check override ports in raw config (for unresolved variants in LSP context)
        raw = config.config if hasattr(config, "config") and isinstance(config.config, dict) else {}
        override = raw.get("override", {})
        if isinstance(override, dict):
            override_outputs = override.get("outputs", []) or []
            if override_outputs:
                override_names = {p.get("name") for p in override_outputs if p.get("name")}
                outputs = [p for p in outputs if p.name not in override_names]
                outputs += [PortDefinition.from_dict(p) for p in override_outputs]

        base_name = config.base if hasattr(config, "base") else None
        if base_name:
            base_config = self.registry_manager.get_entity(base_name)
            if base_config:
                base_outputs = self.get_entity_outputs(base_config, _seen)
                existing_names = {p.name for p in outputs}
                outputs = outputs + [p for p in base_outputs if p.name not in existing_names]

        return outputs

    def get_instance_entity(self, config: Config, instance_name: str) -> Optional[Config]:
        """Find the entity config for an instance."""
        entity_name = None

        if config.entity_type == ConfigType.MODULE:
            instances = config.instances or []
            for inst in instances:
                if inst.get("name") == instance_name:
                    entity_name = inst.get("entity")
                    break
        elif config.entity_type == ConfigType.SYSTEM:
            components = config.components or []
            for comp in components:
                if comp.get("name") == instance_name:
                    entity_name = comp.get("entity")
                    break

        if entity_name:
            return self.registry_manager.get_entity(entity_name)

        return None
