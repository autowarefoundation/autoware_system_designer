#!/usr/bin/env python3

from typing import Optional
from lsprotocol import types as lsp

from autoware_system_designer.models.config import Config, ConfigType

from registry_manager import RegistryManager
from utils.text_utils import get_word_at_position
from utils.uri_utils import uri_to_path, path_to_uri


class DefinitionProvider:
    """Provides go-to-definition functionality."""

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_definition(self, params: lsp.DefinitionParams, server) -> Optional[lsp.Location]:
        """Handle go-to-definition requests."""
        document = server.workspace.get_document(params.text_document.uri)
        if not document:
            return None

        # Get current word
        line = document.lines[params.position.line]
        word = get_word_at_position(line, params.position.character)

        # Check if it's an entity name
        if word in self.registry_manager.entity_registry:
            config = self.registry_manager.entity_registry[word]
            return lsp.Location(
                uri=path_to_uri(str(config.file_path)),
                range=lsp.Range(
                    start=lsp.Position(line=1, character=0),  # Skip the format header
                    end=lsp.Position(line=2, character=0)
                )
            )

        # Check if it's a connection reference that points to another entity
        current_file_path = uri_to_path(params.text_document.uri)
        current_config = self.registry_manager.get_entity_by_file(current_file_path)

        if current_config:
            location = self._find_definition_in_connection(word, current_config)
            if location:
                return location

        return None

    def _find_definition_in_connection(self, word: str, config: Config) -> Optional[lsp.Location]:
        """Find definition for connection references."""
        # Parse the word as a potential connection reference
        parts = word.split('.')

        if len(parts) >= 3:
            if config.entity_type == ConfigType.MODULE:
                # Handle module connections: instance.port_type.port_name
                if len(parts) >= 3 and parts[1] in ['input', 'output']:
                    instance_name = parts[0]
                    port_type = parts[1]
                    port_name = parts[2]

                    # Find the instance
                    instances = config.instances or []
                    for instance in instances:
                        if instance.get('name') == instance_name:
                            entity_name = instance.get('entity')
                            if entity_name in self.registry_manager.entity_registry:
                                entity_config = self.registry_manager.entity_registry[entity_name]
                                # Return location in the entity file at the port definition
                                return lsp.Location(
                                    uri=path_to_uri(str(entity_config.file_path)),
                                    range=lsp.Range(
                                        start=lsp.Position(line=10, character=0),  # Approximate location
                                        end=lsp.Position(line=11, character=0)
                                    )
                                )

            elif config.entity_type == ConfigType.SYSTEM:
                # Handle system connections: component.port_type.port_name
                if len(parts) >= 3 and parts[1] in ['input', 'output']:
                    component_name = parts[0]
                    port_type = parts[1]
                    port_name = parts[2]

                    # Find the component
                    components = config.components or []
                    for component in components:
                        if component.get('name') == component_name:
                            component_entity = component.get('entity')
                            if component_entity in self.registry_manager.entity_registry:
                                entity_config = self.registry_manager.entity_registry[component_entity]
                                # Return location in the entity file at the port definition
                                return lsp.Location(
                                    uri=path_to_uri(str(entity_config.file_path)),
                                    range=lsp.Range(
                                        start=lsp.Position(line=10, character=0),  # Approximate location
                                        end=lsp.Position(line=11, character=0)
                                    )
                                )

        return None
