import re
from typing import List, Optional, Set

from lsprotocol import types as lsp
from registry_manager import RegistryManager
from utils.uri_utils import uri_to_path

from autoware_system_designer.models.config import Config, ConfigType


class InlayHintProvider:
    """Provides inlay hints for port types."""

    # Direction terms used in YAML connection strings
    _INPUT_TERMS = {"input", "subscriber", "server"}
    _OUTPUT_TERMS = {"output", "publisher", "client"}
    _ALL_DIRECTION_TERMS = _INPUT_TERMS | _OUTPUT_TERMS

    # Matches list-format connection strings:
    #   "  - - instance.publisher.port_name"  (from ref, double dash)
    #   "    - instance.subscriber.port_name" (to ref, single dash)
    # Also matches external refs like "subscriber.port_name" or "publisher.port_name"
    _LIST_CONN_RE = re.compile(
        r"-\s+(?:-\s+)?((?:\w[\w/-]*\.)?(?:subscriber|publisher|server|client|input|output)\.[\w/*.-]+)"
    )
    # Matches dict-format connection strings: "from: ..." or "to: ..."
    _DICT_CONN_RE = re.compile(r"(?:from|to):\s*([\w\.\*/-]+)")

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_inlay_hints(self, params: lsp.InlayHintParams, server) -> Optional[List[lsp.InlayHint]]:
        """Handle inlay hint requests."""
        document = server.workspace.get_document(params.text_document.uri)
        if not document:
            return None

        hints = []

        # Determine the range to process
        start_line = 0
        end_line = len(document.lines)

        if params.range:
            start_line = params.range.start.line
            end_line = min(params.range.end.line + 1, len(document.lines))

        for i in range(start_line, end_line):
            line = document.lines[i]

            # Try dict-format first: "from: instance.direction.port" or "to: instance.direction.port"
            match = self._DICT_CONN_RE.search(line)
            if match:
                connection_str = match.group(1)
                if "*" in connection_str:
                    continue
                msg_type = self._resolve_type_for_string(connection_str, params.text_document.uri)
                if msg_type:
                    hint = lsp.InlayHint(
                        position=lsp.Position(line=i, character=match.end(1)),
                        label=f": {msg_type}",
                        kind=lsp.InlayHintKind.Type,
                        padding_left=True,
                    )
                    hints.append(hint)
                continue

            # Try list-format: "  - - instance.publisher.port" or "    - instance.subscriber.port"
            match = self._LIST_CONN_RE.search(line)
            if match:
                connection_str = match.group(1)
                if "*" in connection_str:
                    continue
                msg_type = self._resolve_type_for_string(connection_str, params.text_document.uri)
                if msg_type:
                    hint = lsp.InlayHint(
                        position=lsp.Position(line=i, character=match.end(1)),
                        label=f": {msg_type}",
                        kind=lsp.InlayHintKind.Type,
                        padding_left=True,
                    )
                    hints.append(hint)

        return hints

    def _resolve_type_for_string(self, connection_str: str, current_uri: str) -> Optional[str]:
        """
        Resolve the message type for a connection string.

        Handles both formats:
          - 3-part: "instance.publisher.port_name" (instance reference)
          - 2-part: "subscriber.port_name" (external interface reference)

        Direction terms subscriber/server map to input; publisher/client map to output.
        """
        file_path = uri_to_path(current_uri)
        current_config = self.registry_manager.get_entity_by_file(file_path)
        if not current_config:
            return None

        from resolution_service import ResolutionService

        resolution_service = ResolutionService(self.registry_manager)

        parts = connection_str.split(".")
        if len(parts) == 3:
            # instance.direction.port_name
            instance_name = parts[0]
            port_dir = parts[1]
            port_name = parts[2]

            if port_dir in self._INPUT_TERMS:
                port_type = "input"
            elif port_dir in self._OUTPUT_TERMS:
                port_type = "output"
            else:
                return None

            target_entity_config = resolution_service.get_instance_entity(current_config, instance_name)
            if target_entity_config:
                return resolution_service.resolve_port_type(target_entity_config, port_type, port_name)

        elif len(parts) == 2:
            # direction.port_name (external interface of current entity)
            port_dir = parts[0]
            port_name = parts[1]

            if port_dir in self._INPUT_TERMS:
                port_type = "input"
            elif port_dir in self._OUTPUT_TERMS:
                port_type = "output"
            else:
                return None

            return resolution_service.resolve_port_type(current_config, port_type, port_name)

        return None
