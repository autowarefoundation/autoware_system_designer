#!/usr/bin/env python3

from lsprotocol import types as lsp
from registry_manager import RegistryManager


class CompletionProvider:
    """Provides auto-completion functionality."""

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_completions(self, params: lsp.CompletionParams, server) -> lsp.CompletionList:
        """Handle completion requests."""
        # Disabled — AI editor tab completion takes priority over LSP completion items.
        # Port hints are provided via signature help instead.
        return lsp.CompletionList(is_incomplete=False, items=[])
