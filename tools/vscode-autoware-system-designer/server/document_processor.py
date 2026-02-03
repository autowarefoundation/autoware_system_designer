#!/usr/bin/env python3

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

from pygls.server import LanguageServer
from lsprotocol import types as lsp

from autoware_system_designer.models.parsing.data_parser import ConfigParser
from autoware_system_designer.models.config import Config
from autoware_system_designer.exceptions import ValidationError

from registry_manager import RegistryManager
from validation_engine import ValidationEngine

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Handles document processing and validation."""

    def __init__(self, config_parser: ConfigParser, registry_manager: RegistryManager):
        self.config_parser = config_parser
        self.registry_manager = registry_manager
        self.validation_engine = ValidationEngine(registry_manager)

    def process_document(self, uri: str, content: str, server: LanguageServer, update_registry: bool = False):
        """Process a document and update registries."""
        file_path = self._uri_to_path(uri)
        file_path_obj = Path(file_path)

        # Always validate, even if parsing fails
        diagnostics = []
        config = None
        
        # Try to parse the document
        try:
            # Parse the content from string
            try:
                # Use the new parse_entity_from_content method
                config = self.config_parser.parse_entity_from_content(content, file_path)
                
                # Update registry only if requested (e.g. on save or open)
                if update_registry and config:
                    self.registry_manager.register_entity(config)
                    
            except (ValidationError, Exception) as parse_error:
                logger.debug(f"Failed to parse {file_path}: {parse_error}")
                # Continue with validation even if parsing fails
                config = None

        except Exception as e:
            logger.warning(f"Error during document processing setup {uri}: {e}")

        # Always validate, regardless of parsing success
        try:
            if config:
                diagnostics = self.validation_engine.validate_all(config, content)
            else:
                # Validate YAML format and filename matching even if parsing failed
                diagnostics = self.validation_engine.validate_yaml_format(content)
                # Also validate filename matching if we have the file path
                if file_path:
                    filename_diagnostics = self.validation_engine.validate_filename_matching_from_content(
                        content, file_path
                    )
                    diagnostics.extend(filename_diagnostics)
        except Exception as validation_error:
            logger.warning(f"Error during validation {uri}: {validation_error}")
            # Still send YAML format validation if possible
            try:
                diagnostics = self.validation_engine.validate_yaml_format(content)
            except:
                pass

        # Send diagnostics
        try:
            logger.info(f"Publishing {len(diagnostics)} diagnostics for {uri}")
            server.publish_diagnostics(uri, diagnostics)
        except Exception as e:
            logger.error(f"Failed to publish diagnostics {uri}: {e}")

    def close_document(self, uri: str):
        """Handle document close event."""
        # We don't unregister on close anymore, as the file might still exist in the workspace
        # File watching will handle unregistration if the file is deleted
        pass

    def _uri_to_path(self, uri: str) -> str:
        """Convert URI to file path."""
        parsed = urlparse(uri)
        return unquote(parsed.path)
