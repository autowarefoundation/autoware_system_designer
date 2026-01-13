#!/usr/bin/env python3

from typing import List, Optional
from lsprotocol import types as lsp

from autoware_system_designer.models.config import Config, ConfigType

from registry_manager import RegistryManager
from utils.text_utils import get_word_at_position


class CompletionProvider:
    """Provides auto-completion functionality."""

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_completions(self, params: lsp.CompletionParams, server) -> lsp.CompletionList:
        """Handle completion requests."""
        # Disabled completion popups to avoid conflicts with AI editor tab completion
        # Instead, validation warnings are shown for incomplete references
        return lsp.CompletionList(is_incomplete=False, items=[])

    def _analyze_completion_context(self, line: str, character: int, config: Config) -> str:
        """Analyze the completion context based on the current line."""
        line = line.strip()

        # Check for entity references
        if 'entity:' in line:
            return 'entity'

        # Check for message type fields
        if 'message_type:' in line:
            return 'message_type'

        # Check for parameter names
        if 'name:' in line and ('parameters:' in '\n'.join(self._get_context_lines(line))):
            return 'parameter_name'

        # Check for connection references
        if 'from:' in line or 'to:' in line:
            # Determine if it's a from or to reference
            if 'from:' in line:
                return 'connection_from'
            elif 'to:' in line:
                return 'connection_to'

        return 'unknown'

    def _get_context_lines(self, current_line: str) -> List[str]:
        """Get context lines around the current line (simplified)."""
        # In a real implementation, you'd get the actual document lines
        # For now, just return the current line
        return [current_line]

    def _get_connection_completion_items(self, config: Config, context: str) -> List[lsp.CompletionItem]:
        """Get completion items for connection references."""
        items = []

        if config.entity_type == ConfigType.MODULE:
            # Module connections
            external_interfaces = config.external_interfaces or {}

            # Input interfaces
            inputs = external_interfaces.get('input', [])
            for interface in inputs:
                name = interface.get('name')
                if name:
                    items.append(lsp.CompletionItem(
                        label=f"input.{name}",
                        kind=lsp.CompletionItemKind.Field,
                        detail="External input interface",
                        documentation=f"Input interface: {name}"
                    ))

            # Output interfaces
            outputs = external_interfaces.get('output', [])
            for interface in outputs:
                name = interface.get('name')
                if name:
                    items.append(lsp.CompletionItem(
                        label=f"output.{name}",
                        kind=lsp.CompletionItemKind.Field,
                        detail="External output interface",
                        documentation=f"Output interface: {name}"
                    ))

            # Instance ports
            instances = config.instances or []
            for instance in instances:
                instance_name = instance.get('instance')
                entity_name = instance.get('entity')

                if instance_name and entity_name and entity_name in self.registry_manager.entity_registry:
                    entity_config = self.registry_manager.entity_registry[entity_name]

                    # Input ports
                    if entity_config.inputs:
                        for port in entity_config.inputs:
                            port_name = port.get('name')
                            msg_type = port.get('message_type', 'unknown')
                            if port_name:
                                items.append(lsp.CompletionItem(
                                    label=f"{instance_name}.input.{port_name}",
                                    kind=lsp.CompletionItemKind.Field,
                                    detail=f"Input port: {msg_type}",
                                    documentation=f"Instance: {instance_name}\nEntity: {entity_name}\nPort: {port_name}\nType: {msg_type}"
                                ))

                    # Output ports
                    if entity_config.outputs:
                        for port in entity_config.outputs:
                            port_name = port.get('name')
                            msg_type = port.get('message_type', 'unknown')
                            if port_name:
                                items.append(lsp.CompletionItem(
                                    label=f"{instance_name}.output.{port_name}",
                                    kind=lsp.CompletionItemKind.Field,
                                    detail=f"Output port: {msg_type}",
                                    documentation=f"Instance: {instance_name}\nEntity: {entity_name}\nPort: {port_name}\nType: {msg_type}"
                                ))

        elif config.entity_type == ConfigType.SYSTEM:
            # System connections
            components = config.components or []
            for component in components:
                component_name = component.get('name')
                component_entity = component.get('entity')

                if component_name and component_entity and component_entity in self.registry_manager.entity_registry:
                    entity_config = self.registry_manager.entity_registry[component_entity]

                    # Input ports
                    if entity_config.inputs:
                        for port in entity_config.inputs:
                            port_name = port.get('name')
                            msg_type = port.get('message_type', 'unknown')
                            if port_name:
                                items.append(lsp.CompletionItem(
                                    label=f"{component_name}.input.{port_name}",
                                    kind=lsp.CompletionItemKind.Field,
                                    detail=f"Input port: {msg_type}",
                                    documentation=f"Component: {component_name}\nEntity: {component_entity}\nPort: {port_name}\nType: {msg_type}"
                                ))

                    # Output ports
                    if entity_config.outputs:
                        for port in entity_config.outputs:
                            port_name = port.get('name')
                            msg_type = port.get('message_type', 'unknown')
                            if port_name:
                                items.append(lsp.CompletionItem(
                                    label=f"{component_name}.output.{port_name}",
                                    kind=lsp.CompletionItemKind.Field,
                                    detail=f"Output port: {msg_type}",
                                    documentation=f"Component: {component_name}\nEntity: {component_entity}\nPort: {port_name}\nType: {msg_type}"
                                ))

        return items

    def _uri_to_path(self, uri: str) -> str:
        """Convert URI to file path."""
        from urllib.parse import urlparse, unquote
        parsed = urlparse(uri)
        return unquote(parsed.path)
