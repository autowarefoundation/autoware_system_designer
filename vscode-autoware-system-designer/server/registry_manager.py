#!/usr/bin/env python3

import logging
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse, unquote

from autoware_system_designer.parsers.data_parser import ConfigParser
from autoware_system_designer.models.config import Config

logger = logging.getLogger(__name__)


class RegistryManager:
    """Manages entity and file registries for the language server."""

    def __init__(self):
        self.config_parser = ConfigParser(strict_mode=False)
        self.entity_registry: Dict[str, Config] = {}
        self.file_registry: Dict[str, Config] = {}

    def scan_workspace(self, workspace_uri: str):
        """Scan workspace for entity files and build registry."""
        workspace_path = self._uri_to_path(workspace_uri)

        # Find all entity files
        patterns = [
            '**/*.node.yaml',
            '**/*.module.yaml',
            '**/*.system.yaml',
            '**/*.parameter_set.yaml'
        ]

        # Collect all files first
        all_files = []
        for pattern in patterns:
            all_files.extend(Path(workspace_path).glob(pattern))

        # Sort files to prioritize src over other folders (src files processed last to overwrite duplicates)
        def sort_key(file_path):
            path_str = str(file_path)
            if '/src/' in path_str:
                return (1, path_str)  # src files come after (higher priority)
            else:
                return (0, path_str)  # other files come first

        all_files.sort(key=sort_key)

        for file_path in all_files:
            try:
                config = self.config_parser.parse_entity_file(str(file_path))
                self._register_entity(config)
            except Exception as e:
                logger.warning(f"Failed to parse {file_path}: {e}")

    def register_entity(self, config: Config):
        """Register an entity in the registry."""
        self._register_entity(config)

    def unregister_entity(self, file_path: str):
        """Unregister an entity from the registry."""
        self._unregister_entity(file_path)

    def get_entity(self, name: str) -> Optional[Config]:
        """Get an entity by name."""
        return self.entity_registry.get(name)

    def get_entity_by_file(self, file_path: str) -> Optional[Config]:
        """Get an entity by file path."""
        return self.file_registry.get(file_path)

    def get_all_entities(self) -> Dict[str, Config]:
        """Get all registered entities."""
        return self.entity_registry.copy()

    def _register_entity(self, config: Config):
        """Register an entity in the registry."""
        self.entity_registry[config.full_name] = config
        self.file_registry[str(config.file_path)] = config
        logger.info(f"Registered entity: {config.full_name} from {config.file_path}")

    def _unregister_entity(self, file_path: str):
        """Unregister an entity from the registry."""
        if file_path in self.file_registry:
            config = self.file_registry[file_path]
            del self.entity_registry[config.full_name]
            del self.file_registry[file_path]
            logger.info(f"Unregistered entity: {config.full_name}")

    def _uri_to_path(self, uri: str) -> str:
        """Convert URI to file path."""
        parsed = urlparse(uri)
        return unquote(parsed.path)
