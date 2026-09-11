#!/usr/bin/env python3

import logging
from pathlib import Path
from typing import Any

from lsprotocol import types as lsp
from pygls.server import LanguageServer
from registry_manager import RegistryManager
from utils.source_map_utils import source_map_range, split_source_suffix
from utils.uri_utils import uri_to_path
from validation_engine import ValidationEngine

from autoware_system_designer.model.config import Config
from autoware_system_designer.parser.data_parser import ConfigParser
from autoware_system_designer.parser.yaml_parser import yaml_parser

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Handles document processing and validation."""

    def __init__(self, config_parser: ConfigParser, registry_manager: RegistryManager):
        self.config_parser = config_parser
        self.registry_manager = registry_manager
        self.validation_engine = ValidationEngine(registry_manager)

    def process_document(self, uri: str, content: str, server: LanguageServer, update_registry: bool = False):
        """Process a document and update registries."""
        file_path = uri_to_path(uri)
        file_path_obj = Path(file_path)

        # Always validate, even if parsing fails
        diagnostics = []
        parse_diagnostics = []
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

            except Exception as parse_error:
                logger.debug(f"Failed to parse {file_path}: {parse_error}")
                # Continue with validation even if parsing fails
                config = None
                parse_diagnostics = self._diagnose_parse_error(parse_error, content)

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
            except Exception:
                pass

        # Schema and format-version failures from the designer parser are reported as-is;
        # a YAML syntax error is already reported by validate_yaml_format.
        if parse_diagnostics and not any(d.message.startswith("YAML syntax error") for d in diagnostics):
            diagnostics.extend(parse_diagnostics)

        # Send diagnostics
        try:
            logger.debug(f"Publishing {len(diagnostics)} diagnostics for {uri}")
            server.publish_diagnostics(uri, diagnostics)
        except Exception as e:
            logger.error(f"Failed to publish diagnostics {uri}: {e}")

    def _diagnose_parse_error(self, error: Exception, content: str) -> list:
        """Turn a designer parse failure into diagnostics anchored by its YAML pointers."""
        source_map = {}
        try:
            _, source_map = yaml_parser.load_config_from_string_with_source(content)
        except Exception:
            source_map = {}

        diagnostics = []
        for line in str(error).splitlines():
            message, yaml_path = split_source_suffix(line)
            if not message or message.endswith(":"):
                continue
            message = message.lstrip("- ").strip()
            diagnostic_range = source_map_range(source_map, yaml_path, content) if yaml_path else None
            if diagnostic_range is None:
                diagnostic_range = source_map_range(source_map, "/name", content) or lsp.Range(
                    start=lsp.Position(line=0, character=0),
                    end=lsp.Position(line=0, character=1),
                )
            diagnostics.append(
                lsp.Diagnostic(
                    range=diagnostic_range,
                    message=message,
                    severity=lsp.DiagnosticSeverity.Error,
                )
            )

        if not diagnostics:
            diagnostics.append(
                lsp.Diagnostic(
                    range=lsp.Range(start=lsp.Position(line=0, character=0), end=lsp.Position(line=0, character=1)),
                    message=str(error),
                    severity=lsp.DiagnosticSeverity.Error,
                )
            )

        return diagnostics

    def close_document(self, uri: str):
        """Handle document close event."""
        # We don't unregister on close anymore, as the file might still exist in the workspace
        # File watching will handle unregistration if the file is deleted
        pass
