#!/usr/bin/env python3

import re
from typing import Optional

from lsprotocol import types as lsp
from registry_manager import RegistryManager
from utils.uri_utils import uri_to_path

from autoware_system_designer.parsing.config import Config, ConfigType


class SignatureHelpProvider:
    """Provides signature help showing available ports when cursor is after 'instance_name.'."""

    _INSTANCE_PREFIX_RE = re.compile(r"(\w[\w/-]*)\.(\w*)$")

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_signature_help(self, params: lsp.SignatureHelpParams, server) -> Optional[lsp.SignatureHelp]:
        """Handle signature help requests."""
        document = server.workspace.get_document(params.text_document.uri)
        if not document:
            return None

        line = document.lines[params.position.line]
        line_to_cursor = line[: params.position.character]

        file_path = uri_to_path(params.text_document.uri)
        current_config = self.registry_manager.get_entity_by_file(file_path)
        if not current_config or current_config.entity_type not in (ConfigType.MODULE, ConfigType.SYSTEM):
            return None

        match = self._INSTANCE_PREFIX_RE.search(line_to_cursor)
        if not match:
            return None

        instance_name = match.group(1)

        from resolution_service import ResolutionService
        from validation_engine import ValidationEngine

        resolution_service = ResolutionService(self.registry_manager)
        entity_config = resolution_service.get_instance_entity(current_config, instance_name)
        if not entity_config:
            return None

        ve = ValidationEngine(self.registry_manager)
        inputs = ve._get_entity_inputs(entity_config)
        outputs = ve._get_entity_outputs(entity_config)

        signatures = []
        if inputs:
            # For inputs, expose both subscription and client service directions
            signatures.append(self._build_signature(instance_name, entity_config, "subscriber", inputs))
            signatures.append(self._build_signature(instance_name, entity_config, "client", inputs))
        if outputs:
            # For outputs, expose both publication and server service directions
            signatures.append(self._build_signature(instance_name, entity_config, "publisher", outputs))
            signatures.append(self._build_signature(instance_name, entity_config, "server", outputs))

        if not signatures:
            return None

        return lsp.SignatureHelp(signatures=signatures, active_signature=0, active_parameter=0)

    def _build_signature(
        self, instance_name: str, entity_config: Config, direction: str, ports: list
    ) -> lsp.SignatureInformation:
        port_labels = [f"{direction}.{p.get('name', '?')}" for p in ports]
        label = f"{instance_name}.{direction}.({', '.join(port_labels)})"

        doc_lines = [f"**{instance_name}** `{entity_config.full_name}`\n"]
        for p in ports:
            name = p.get("name", "unknown")
            msg_type = p.get("message_type", "unknown")
            doc_lines.append(f"- `{direction}.{name}` — {msg_type}")
        documentation = lsp.MarkupContent(kind=lsp.MarkupKind.Markdown, value="\n".join(doc_lines))

        parameters = []
        offset = len(f"{instance_name}.{direction}.(")
        for port_label in port_labels:
            start = label.index(port_label, offset)
            parameters.append(lsp.ParameterInformation(label=[start, start + len(port_label)]))
            offset = start + len(port_label)

        return lsp.SignatureInformation(label=label, documentation=documentation, parameters=parameters)
