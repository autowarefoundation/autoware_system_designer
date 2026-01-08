#!/usr/bin/env python3

import logging
import sys
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from pygls.server import LanguageServer
from lsprotocol import types as lsp

# Import from the autoware_system_designer package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'autoware_system_designer'))

from autoware_system_designer.parsers.data_parser import ConfigParser
from autoware_system_designer.models.config import Config

from registry_manager import RegistryManager
from document_processor import DocumentProcessor
from validation_engine import ValidationEngine
from providers.completion_provider import CompletionProvider
from providers.definition_provider import DefinitionProvider
from providers.hover_provider import HoverProvider
from providers.inlay_hint_provider import InlayHintProvider

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class AutowareSystemDesignerLanguageServer:
    """Main language server class for Autoware System Designer."""

    def __init__(self):
        self.server = LanguageServer("autoware-system-designer", "0.1.0")
        self.config_parser = ConfigParser()

        # Initialize components
        self.registry_manager = RegistryManager()
        self.document_processor = DocumentProcessor(self.config_parser, self.registry_manager)
        self.validation_engine = ValidationEngine(self.registry_manager)
        self.completion_provider = CompletionProvider(self.registry_manager)
        self.definition_provider = DefinitionProvider(self.registry_manager)
        self.hover_provider = HoverProvider(self.registry_manager)
        self.inlay_hint_provider = InlayHintProvider(self.registry_manager)

        # Register handlers
        self._register_handlers()

    def _register_handlers(self):
        """Register all LSP handlers."""

        @self.server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
        def did_open(ls, params):
            self._on_text_document_did_open(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
        def did_change(ls, params):
            self._on_text_document_did_change(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
        def did_close(ls, params):
            self._on_text_document_did_close(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_COMPLETION)
        def completion(ls, params):
            return self._on_completion(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
        def definition(ls, params):
            return self._on_definition(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_HOVER)
        def hover(ls, params):
            return self._on_hover(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_INLAY_HINT)
        def inlay_hint(ls, params):
            return self._on_inlay_hint(ls, params)

        @self.server.feature(lsp.INITIALIZE)
        def initialize(ls, params):
            return self._on_initialize(ls, params)

    def start(self):
        """Start the language server."""
        self.server.start_io()

    def _on_initialize(self, ls, params: lsp.InitializeParams) -> lsp.InitializeResult:
        """Handle server initialization."""
        logger.info("Initializing Autoware System Designer Language Server")

        # Scan workspace for entity files
        if params.workspace_folders:
            for folder in params.workspace_folders:
                self.registry_manager.scan_workspace(folder.uri)

        capabilities = lsp.ServerCapabilities(
            text_document_sync=lsp.TextDocumentSyncKind.Full,
            # Completion provider disabled - using diagnostics instead
            # completion_provider=lsp.CompletionOptions(
            #     trigger_characters=['.', ':']
            # ),
            definition_provider=True,
            hover_provider=True,
            inlay_hint_provider=True
        )

        return lsp.InitializeResult(capabilities=capabilities)

    def _on_text_document_did_open(self, ls, params: lsp.DidOpenTextDocumentParams):
        """Handle document open event."""
        self.document_processor.process_document(params.text_document.uri, params.text_document.text, self.server)

    def _on_text_document_did_change(self, ls, params: lsp.DidChangeTextDocumentParams):
        """Handle document change event."""
        # For simplicity, we reprocess the entire document on change
        # In a production implementation, you'd want incremental updates
        if params.content_changes:
            content = params.content_changes[0].text
            self.document_processor.process_document(params.text_document.uri, content, self.server)

    def _on_text_document_did_close(self, ls, params: lsp.DidCloseTextDocumentParams):
        """Handle document close event."""
        self.document_processor.close_document(params.text_document.uri)

    def _on_completion(self, ls, params: lsp.CompletionParams) -> lsp.CompletionList:
        """Handle completion requests."""
        return self.completion_provider.get_completions(params, self.server)

    def _on_definition(self, ls, params: lsp.DefinitionParams) -> Optional[lsp.Location]:
        """Handle go-to-definition requests."""
        return self.definition_provider.get_definition(params, self.server)

    def _on_hover(self, ls, params: lsp.HoverParams) -> Optional[lsp.Hover]:
        """Handle hover requests."""
        return self.hover_provider.get_hover(params, self.server)

    def _on_inlay_hint(self, ls, params: lsp.InlayHintParams) -> Optional[List[lsp.InlayHint]]:
        """Handle inlay hint requests."""
        return self.inlay_hint_provider.get_inlay_hints(params, self.server)
