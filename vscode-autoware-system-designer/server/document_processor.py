#!/usr/bin/env python3

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

from pygls.server import LanguageServer
from lsprotocol import types as lsp

from autoware_system_designer.parsers.data_parser import ConfigParser
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

    def process_document(self, uri: str, content: str, server: LanguageServer):
        """Process a document and update registries."""
        file_path = self._uri_to_path(uri)
        file_path_obj = Path(file_path)

        # Try to parse the document
        try:
            # The parser requires the file to exist on disk with the correct filename
            # to determine the entity type from the filename pattern
            if not file_path_obj.exists():
                logger.debug(f"File {file_path} does not exist yet, skipping parsing")
                return

            # Unregister existing entity if it was already registered
            self.registry_manager.unregister_entity(file_path)

            # Parse the content from the file
            # Note: The parser reads from disk, so unsaved changes won't be reflected
            # until the file is saved. This is a limitation of the current parser design.
            config = self.config_parser.parse_entity_file(file_path)
            self.registry_manager.register_entity(config)

            # Send diagnostics
            diagnostics = self.validation_engine.validate_connections(config)
            server.text_document_publish_diagnostics(
                lsp.PublishDiagnosticsParams(
                    uri=uri,
                    diagnostics=diagnostics
                )
            )

        except ValidationError as e:
            # Send validation error diagnostics
            diagnostics = [
                lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=1)
                    ),
                    message=str(e),
                    severity=lsp.DiagnosticSeverity.Error
                )
            ]
            server.text_document_publish_diagnostics(
                lsp.PublishDiagnosticsParams(
                    uri=uri,
                    diagnostics=diagnostics
                )
            )
        except Exception as e:
            logger.warning(f"Failed to process document {uri}: {e}")

    def close_document(self, uri: str):
        """Handle document close event."""
        file_path = self._uri_to_path(uri)
        self.registry_manager.unregister_entity(file_path)

    def _uri_to_path(self, uri: str) -> str:
        """Convert URI to file path."""
        parsed = urlparse(uri)
        return unquote(parsed.path)
