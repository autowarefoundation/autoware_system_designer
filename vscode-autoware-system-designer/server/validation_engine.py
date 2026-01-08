#!/usr/bin/env python3

from typing import List, Optional, Tuple
from lsprotocol import types as lsp

from autoware_system_designer.models.config import Config, ConfigType

from registry_manager import RegistryManager


class ValidationEngine:
    """Handles validation of connections and references."""

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def validate_connections(self, config: Config) -> List[lsp.Diagnostic]:
        """Validate connections in the config and return diagnostics."""
        diagnostics = []

        if config.entity_type == ConfigType.MODULE:
            connections = config.connections or []
            for i, connection in enumerate(connections):
                from_ref = connection.get('from', '')
                to_ref = connection.get('to', '')

                # Validate from reference
                from_valid, from_message = self._validate_connection_reference(from_ref, config)
                if not from_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'from'),
                        message=f"Invalid connection source: {from_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate to reference
                to_valid, to_message = self._validate_connection_reference(to_ref, config)
                if not to_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'to'),
                        message=f"Invalid connection destination: {to_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate message type compatibility if both are valid
                if from_valid and to_valid and from_ref and to_ref:
                    compatibility_issue = self._check_message_type_compatibility(from_ref, to_ref, config)
                    if compatibility_issue:
                        diagnostics.append(lsp.Diagnostic(
                            range=self._get_connection_range(i, 'to'),
                            message=f"Message type mismatch: {compatibility_issue}",
                            severity=lsp.DiagnosticSeverity.Warning
                        ))

        elif config.entity_type == ConfigType.SYSTEM:
            connections = config.connections or []
            for i, connection in enumerate(connections):
                from_ref = connection.get('from', '')
                to_ref = connection.get('to', '')

                # Validate from reference
                from_valid, from_message = self._validate_connection_reference(from_ref, config)
                if not from_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'from'),
                        message=f"Invalid connection source: {from_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate to reference
                to_valid, to_message = self._validate_connection_reference(to_ref, config)
                if not to_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'to'),
                        message=f"Invalid connection destination: {to_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate message type compatibility if both are valid
                if from_valid and to_valid and from_ref and to_ref:
                    compatibility_issue = self._check_message_type_compatibility(from_ref, to_ref, config)
                    if compatibility_issue:
                        diagnostics.append(lsp.Diagnostic(
                            range=self._get_connection_range(i, 'to'),
                            message=f"Message type mismatch: {compatibility_issue}",
                            severity=lsp.DiagnosticSeverity.Warning
                        ))

        return diagnostics

    def _validate_connection_reference(self, ref: str, config: Config) -> Tuple[bool, str]:
        """Validate if a connection reference is valid."""
        if not ref:
            return False, "Empty reference"

        # Handle wildcard references
        if '*' in ref:
            return True, ""  # Wildcards are allowed for now

        # Parse reference (e.g., "input.pointcloud", "node_detector.output.objects")
        parts = ref.split('.')

        if len(parts) < 2:
            return False, f"Invalid reference format: {ref}"

        if config.entity_type == ConfigType.MODULE:
            if parts[0] == 'input':
                # Check external interfaces
                external_interfaces = config.external_interfaces or {}
                inputs = external_interfaces.get('input', [])
                if not inputs:
                    return False, f"No input interfaces defined in module {config.name}"

                input_names = [interface.get('name') for interface in inputs if interface.get('name')]
                if parts[1] not in input_names:
                    return False, f"Input '{parts[1]}' not found. Available inputs: {', '.join(input_names)}"
                return True, ""

            elif parts[0] == 'output':
                # Check external interfaces
                external_interfaces = config.external_interfaces or {}
                outputs = external_interfaces.get('output', [])
                if not outputs:
                    return False, f"No output interfaces defined in module {config.name}"

                output_names = [interface.get('name') for interface in outputs if interface.get('name')]
                if parts[1] not in output_names:
                    return False, f"Output '{parts[1]}' not found. Available outputs: {', '.join(output_names)}"
                return True, ""

            elif parts[0].startswith('node_'):
                # Check instance ports
                instance_name = parts[0]
                port_type = parts[1] if len(parts) > 1 else None
                port_name = parts[2] if len(parts) > 2 else None

                if not port_type or not port_name:
                    return False, f"Invalid instance reference format: {ref}"

                instances = config.instances or []
                instance_names = [inst.get('instance') for inst in instances if inst.get('instance')]
                if instance_name not in instance_names:
                    return False, f"Instance '{instance_name}' not found. Available instances: {', '.join(instance_names)}"

                for instance in instances:
                    if instance.get('instance') == instance_name:
                        entity_name = instance.get('entity')
                        if entity_name not in self.registry_manager.entity_registry:
                            return False, f"Entity '{entity_name}' not found in registry"

                        entity_config = self.registry_manager.entity_registry[entity_name]
                        if port_type == 'input':
                            if not entity_config.inputs:
                                return False, f"Entity '{entity_name}' has no input ports"
                            input_names = [port.get('name') for port in entity_config.inputs if port.get('name')]
                            if port_name not in input_names:
                                return False, f"Input port '{port_name}' not found in entity '{entity_name}'. Available inputs: {', '.join(input_names)}"
                        elif port_type == 'output':
                            if not entity_config.outputs:
                                return False, f"Entity '{entity_name}' has no output ports"
                            output_names = [port.get('name') for port in entity_config.outputs if port.get('name')]
                            if port_name not in output_names:
                                return False, f"Output port '{port_name}' not found in entity '{entity_name}'. Available outputs: {', '.join(output_names)}"
                        else:
                            return False, f"Invalid port type '{port_type}'. Must be 'input' or 'output'"
                        return True, ""
                return False, f"Instance '{instance_name}' configuration error"

        elif config.entity_type == ConfigType.SYSTEM:
            # System connections reference component ports
            component_name = parts[0]
            port_type = parts[1] if len(parts) > 1 else None
            port_name = parts[2] if len(parts) > 2 else None

            if not port_type or not port_name:
                return False, f"Invalid component reference format: {ref}"

            components = config.components or []
            component_names = [comp.get('name') for comp in components if comp.get('name')]
            if component_name not in component_names:
                return False, f"Component '{component_name}' not found. Available components: {', '.join(component_names)}"

            for component in components:
                if component.get('name') == component_name:
                    component_entity = component.get('entity')
                    if component_entity not in self.registry_manager.entity_registry:
                        return False, f"Entity '{component_entity}' not found in registry"

                    entity_config = self.registry_manager.entity_registry[component_entity]
                    if port_type == 'input':
                        if not entity_config.inputs:
                            return False, f"Entity '{component_entity}' has no input ports"
                        input_names = [port.get('name') for port in entity_config.inputs if port.get('name')]
                        if port_name not in input_names:
                            return False, f"Input port '{port_name}' not found in component '{component_name}'. Available inputs: {', '.join(input_names)}"
                    elif port_type == 'output':
                        if not entity_config.outputs:
                            return False, f"Entity '{component_entity}' has no output ports"
                        output_names = [port.get('name') for port in entity_config.outputs if port.get('name')]
                        if port_name not in output_names:
                            return False, f"Output port '{port_name}' not found in component '{component_name}'. Available outputs: {', '.join(output_names)}"
                    else:
                        return False, f"Invalid port type '{port_type}'. Must be 'input' or 'output'"
                    return True, ""
            return False, f"Component '{component_name}' configuration error"

        return False, f"Unsupported reference format for {config.entity_type}: {ref}"

    def _check_message_type_compatibility(self, from_ref: str, to_ref: str, config: Config) -> Optional[str]:
        """Check if message types are compatible between source and destination."""
        from_type = self._get_message_type(from_ref, config)
        to_type = self._get_message_type(to_ref, config)

        if from_type and to_type and from_type != to_type:
            return f"Source type '{from_type}' does not match destination type '{to_type}'"

        return None

    def _get_message_type(self, ref: str, config: Config) -> Optional[str]:
        """Get the message type for a connection reference."""
        if '*' in ref:
            return None  # Wildcards don't have specific types

        parts = ref.split('.')

        if config.entity_type == ConfigType.MODULE:
            if parts[0] == 'input':
                # External input interface - no type info available
                return None
            elif parts[0] == 'output':
                # External output interface - no type info available
                return None
            elif parts[0].startswith('node_'):
                instance_name = parts[0]
                port_type = parts[1]
                port_name = parts[2]

                instances = config.instances or []
                for instance in instances:
                    if instance.get('instance') == instance_name:
                        entity_name = instance.get('entity')
                        if entity_name in self.registry_manager.entity_registry:
                            entity_config = self.registry_manager.entity_registry[entity_name]
                            if port_type == 'input' and entity_config.inputs:
                                for port in entity_config.inputs:
                                    if port.get('name') == port_name:
                                        return port.get('message_type')
                            elif port_type == 'output' and entity_config.outputs:
                                for port in entity_config.outputs:
                                    if port.get('name') == port_name:
                                        return port.get('message_type')

        elif config.entity_type == ConfigType.SYSTEM:
            component_name = parts[0]
            port_type = parts[1]
            port_name = parts[2]

            components = config.components or []
            for component in components:
                if component.get('name') == component_name:
                    component_entity = component.get('entity')
                    if component_entity in self.registry_manager.entity_registry:
                        entity_config = self.registry_manager.entity_registry[component_entity]
                        if port_type == 'input' and entity_config.inputs:
                            for port in entity_config.inputs:
                                if port.get('name') == port_name:
                                    return port.get('message_type')
                        elif port_type == 'output' and entity_config.outputs:
                            for port in entity_config.outputs:
                                if port.get('name') == port_name:
                                    return port.get('message_type')

        return None

    def _get_connection_range(self, connection_index: int, field: str) -> lsp.Range:
        """Get the range for a connection field (approximate)."""
        # This is a simplified implementation
        # In a real implementation, you'd parse the YAML and get exact positions
        line = 20 + connection_index * 5  # Approximate line number
        return lsp.Range(
            start=lsp.Position(line=line, character=0),
            end=lsp.Position(line=line, character=50)
        )
