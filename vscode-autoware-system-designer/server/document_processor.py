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

from .registry_manager import RegistryManager
from .validation_engine import ValidationEngine

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

        # Try to parse the document
        try:
            # Write content to temporary file for parsing
            temp_path = Path(file_path)
            if temp_path.exists():
                # Unregister existing entity if file exists
                self.registry_manager.unregister_entity(file_path)

            # Parse the content
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
