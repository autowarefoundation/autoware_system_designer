#!/usr/bin/env python3

import re
from typing import List

from lsprotocol import types as lsp
from registry_manager import RegistryManager
from utils.uri_utils import uri_to_path

from autoware_system_designer.parsing.config import Config, ConfigType


class CompletionProvider:
    """Provides auto-completion for connection port references.

    Two-stage completion:
      Stage 1 — after 'instance.'         → offers direction keywords (publisher/subscriber/...)
      Stage 2 — after 'instance.dir.'     → offers port names from that instance's entity
    """

    # Stage 2: instance_name.direction.partial_port
    _PORT_RE = re.compile(r"(\w[\w/-]*)\.(publisher|subscriber|server|client)\.([\w/-]*)$")
    # Stage 1: instance_name.partial_direction  (must not already have a second dot)
    _DIRECTION_RE = re.compile(r"(\w[\w/-]*)\.(\w*)$")

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_completions(self, params: lsp.CompletionParams, server) -> lsp.CompletionList:
        """Handle completion requests."""
        document = server.workspace.get_document(params.text_document.uri)
        if not document:
            return lsp.CompletionList(is_incomplete=False, items=[])

        line = document.lines[params.position.line]
        line_to_cursor = line[: params.position.character]

        file_path = uri_to_path(params.text_document.uri)
        current_config = self.registry_manager.get_entity_by_file(file_path)
        if not current_config or current_config.entity_type not in (ConfigType.MODULE, ConfigType.SYSTEM):
            return lsp.CompletionList(is_incomplete=False, items=[])

        # Stage 2: instance.direction.partial → complete port names
        match = self._PORT_RE.search(line_to_cursor)
        if match:
            instance_name = match.group(1)
            direction = match.group(2)
            partial = match.group(3)
            return self._complete_ports(current_config, instance_name, direction, partial)

        # Stage 1: instance.partial → complete direction keyword
        match = self._DIRECTION_RE.search(line_to_cursor)
        if match:
            instance_name = match.group(1)
            partial = match.group(2)
            return self._complete_directions(current_config, instance_name, partial)

        return lsp.CompletionList(is_incomplete=False, items=[])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve(self, current_config: Config, instance_name: str):
        from resolution_service import ResolutionService
        from validation_engine import ValidationEngine

        rs = ResolutionService(self.registry_manager)
        entity_config = rs.get_instance_entity(current_config, instance_name)
        if not entity_config:
            return None, None, None
        ve = ValidationEngine(self.registry_manager)
        inputs = ve._get_entity_inputs(entity_config)
        outputs = ve._get_entity_outputs(entity_config)
        return entity_config, inputs, outputs

    def _complete_directions(self, current_config: Config, instance_name: str, partial: str) -> lsp.CompletionList:
        _, inputs, outputs = self._resolve(current_config, instance_name)
        if inputs is None:
            return lsp.CompletionList(is_incomplete=False, items=[])

        items: List[lsp.CompletionItem] = []
        if inputs:
            for kw in ("subscriber", "server"):
                if kw.startswith(partial):
                    items.append(
                        lsp.CompletionItem(
                            label=kw,
                            kind=lsp.CompletionItemKind.Field,
                            detail=f"{len(inputs)} input port(s)",
                        )
                    )
        if outputs:
            for kw in ("publisher", "client"):
                if kw.startswith(partial):
                    items.append(
                        lsp.CompletionItem(
                            label=kw,
                            kind=lsp.CompletionItemKind.Field,
                            detail=f"{len(outputs)} output port(s)",
                        )
                    )
        return lsp.CompletionList(is_incomplete=False, items=items)

    def _complete_ports(
        self, current_config: Config, instance_name: str, direction: str, partial: str
    ) -> lsp.CompletionList:
        _, inputs, outputs = self._resolve(current_config, instance_name)
        if inputs is None:
            return lsp.CompletionList(is_incomplete=False, items=[])

        ports = inputs if direction in ("subscriber", "client") else outputs

        items: List[lsp.CompletionItem] = []
        for port in ports:
            name = port.get("name", "")
            msg_type = port.get("message_type", "unknown")
            if name.startswith(partial):
                items.append(
                    lsp.CompletionItem(
                        label=name,
                        kind=lsp.CompletionItemKind.Value,
                        detail=msg_type,
                        documentation=lsp.MarkupContent(
                            kind=lsp.MarkupKind.Markdown,
                            value=f"`{direction}.{name}` — {msg_type}",
                        ),
                    )
                )
        return lsp.CompletionList(is_incomplete=False, items=items)
