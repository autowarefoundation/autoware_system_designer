import re
from typing import List, Optional

from lsprotocol import types as lsp
from registry_manager import RegistryManager
from resolution_service import ResolutionService
from utils.uri_utils import uri_to_path

from autoware_system_designer.models.config import Config, ConfigType


class InlayHintProvider:
    """Provides inlay hints showing available ports for instances in connection refs."""

    # Matches instance_name.(subscriber|publisher|server|client). in connection refs.
    # Captures the instance name so we can look up its entity.
    _CONN_INSTANCE_RE = re.compile(
        r"(\w[\w/-]*)\.(?:subscriber|publisher|server|client)\."
    )

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_inlay_hints(self, params: lsp.InlayHintParams, server) -> Optional[List[lsp.InlayHint]]:
        """Handle inlay hint requests."""
        document = server.workspace.get_document(params.text_document.uri)
        if not document:
            return None

        file_path = uri_to_path(params.text_document.uri)
        current_config = self.registry_manager.get_entity_by_file(file_path)
        if not current_config or current_config.entity_type not in (ConfigType.MODULE, ConfigType.SYSTEM):
            return None

        resolution_service = ResolutionService(self.registry_manager)

        hints = []

        start_line = 0
        end_line = len(document.lines)
        if params.range:
            start_line = params.range.start.line
            end_line = min(params.range.end.line + 1, len(document.lines))

        for i in range(start_line, end_line):
            line = document.lines[i]
            seen_instances = set()

            for match in self._CONN_INSTANCE_RE.finditer(line):
                instance_name = match.group(1)
                if instance_name in seen_instances:
                    continue
                seen_instances.add(instance_name)

                entity_config = resolution_service.get_instance_entity(current_config, instance_name)
                if not entity_config:
                    continue

                label = self._get_port_hint_label(entity_config)
                if not label:
                    continue

                hints.append(
                    lsp.InlayHint(
                        position=lsp.Position(line=i, character=match.end(1)),
                        label=label,
                        kind=lsp.InlayHintKind.Parameter,
                        padding_left=False,
                    )
                )

        return hints

    def _get_port_hint_label(self, entity_config: Config) -> Optional[str]:
        """Format available ports of an entity as a compact hint label."""
        from validation_engine import ValidationEngine

        ve = ValidationEngine(self.registry_manager)
        inputs = ve._get_entity_inputs(entity_config)
        outputs = ve._get_entity_outputs(entity_config)

        input_names = [p["name"] for p in inputs if p.get("name")]
        output_names = [p["name"] for p in outputs if p.get("name")]

        if not input_names and not output_names:
            return None

        parts = []
        if input_names:
            parts.append("subscriber: " + ", ".join(input_names))
        if output_names:
            parts.append("publisher: " + ", ".join(output_names))

        return "[" + " | ".join(parts) + "]"
