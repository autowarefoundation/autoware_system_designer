#!/usr/bin/env python3

from typing import Optional

from lsprotocol import types as lsp
from registry_manager import RegistryManager
from utils.text_utils import get_word_at_position
from utils.uri_utils import path_to_uri, uri_to_path

from autoware_system_designer.parsing.config import Config, ConfigType


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
            # Use source_map to find the exact line of the 'name' field
            line_num = 0
            if hasattr(config, 'source_map') and config.source_map and 'name' in config.source_map:
                # source_map['name'] is (line, column) tuple; YAML lines are 0-indexed
                line_num = config.source_map['name'][0]
            return lsp.Location(
                uri=path_to_uri(str(config.file_path)),
                range=lsp.Range(
                    start=lsp.Position(line=line_num, character=0),
                    end=lsp.Position(line=line_num + 1, character=0),
                ),
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
        parts = word.split(".")

        if len(parts) >= 3:
            _PORT_DIRECTIONS = {"subscriber", "publisher", "server", "client"}

            if config.entity_type == ConfigType.MODULE:
                # Handle module connections: instance.direction.port_name
                if len(parts) >= 3 and parts[1] in _PORT_DIRECTIONS:
                    instance_name = parts[0]

                    # Find the instance
                    instances = config.instances or []
                    for instance in instances:
                        if instance.get("name") == instance_name:
                            entity_name = instance.get("entity")
                            if entity_name in self.registry_manager.entity_registry:
                                entity_config = self.registry_manager.entity_registry[entity_name]
                                # Use source_map for precise line of 'name' field
                                line_num = 0
                                if hasattr(entity_config, 'source_map') and entity_config.source_map and 'name' in entity_config.source_map:
                                    line_num = entity_config.source_map['name'][0]
                                return lsp.Location(
                                    uri=path_to_uri(str(entity_config.file_path)),
                                    range=lsp.Range(
                                        start=lsp.Position(line=line_num, character=0),
                                        end=lsp.Position(line=line_num + 1, character=0),
                                    ),
                                )

            elif config.entity_type == ConfigType.SYSTEM:
                # Handle system connections: component.direction.port_name
                if len(parts) >= 3 and parts[1] in _PORT_DIRECTIONS:
                    component_name = parts[0]

                    # Find the component
                    components = config.components or []
                    for component in components:
                        if component.get("name") == component_name:
                            component_entity = component.get("entity")
                            if component_entity in self.registry_manager.entity_registry:
                                entity_config = self.registry_manager.entity_registry[component_entity]
                                # Use source_map for precise line of 'name' field
                                line_num = 0
                                if hasattr(entity_config, 'source_map') and entity_config.source_map and 'name' in entity_config.source_map:
                                    line_num = entity_config.source_map['name'][0]
                                return lsp.Location(
                                    uri=path_to_uri(str(entity_config.file_path)),
                                    range=lsp.Range(
                                        start=lsp.Position(line=line_num, character=0),
                                        end=lsp.Position(line=line_num + 1, character=0),
                                    ),
                                )

        return None
