from typing import List, Optional

from lsprotocol import types as lsp
from registry_manager import RegistryManager


class InlayHintProvider:
    """Provides inlay hints."""

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_inlay_hints(self, _params: lsp.InlayHintParams, _server) -> Optional[List[lsp.InlayHint]]:
        """Handle inlay hint requests."""
        return None
