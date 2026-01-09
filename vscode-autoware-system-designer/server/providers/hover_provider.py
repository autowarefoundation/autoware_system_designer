#!/usr/bin/env python3

from typing import Optional
from lsprotocol import types as lsp

from autoware_system_designer.models.config import Config, ConfigType

from registry_manager import RegistryManager
from utils.text_utils import get_word_at_position


class HoverProvider:
    """Provides hover information functionality."""

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager

    def get_hover(self, params: lsp.HoverParams, server) -> Optional[lsp.Hover]:
        """Handle hover requests."""
        document = server.workspace.get_document(params.text_document.uri)
        if not document:
            return None

        # Get current word
        line = document.lines[params.position.line]
        word = get_word_at_position(line, params.position.character)

        # Check if it's an entity name
        if word in self.registry_manager.entity_registry:
            config = self.registry_manager.entity_registry[word]
            return self._create_entity_hover(config)

        return None

    def _create_entity_hover(self, config: Config) -> lsp.Hover:
        """Create hover information for an entity."""
        hover_text = f"**{config.full_name}**\n\n"
        hover_text += f"**Type:** {config.entity_type.title()}\n"
        hover_text += f"**File:** `{config.file_path.name}`\n\n"

        if config.entity_type == ConfigType.NODE:
            hover_text += "### Launch Configuration\n"
            if config.launch:
                package = config.launch.get('package', 'unknown')
                plugin = config.launch.get('plugin') or config.launch.get('executable', 'unknown')
                hover_text += f"- **Package:** {package}\n"
                hover_text += f"- **Plugin/Executable:** {plugin}\n"
            else:
                hover_text += "_No launch configuration_\n"

            if config.inputs:
                hover_text += "\n### Inputs\n"
                for port in config.inputs:
                    name = port.get('name', 'unknown')
                    msg_type = port.get('message_type', 'unknown')
                    hover_text += f"- `{name}`: {msg_type}\n"

            if config.outputs:
                hover_text += "\n### Outputs\n"
                for port in config.outputs:
                    name = port.get('name', 'unknown')
                    msg_type = port.get('message_type', 'unknown')
                    hover_text += f"- `{name}`: {msg_type}\n"

            if config.parameters:
                hover_text += f"\n### Parameters\n{len(config.parameters)} parameter(s) defined\n"

        elif config.entity_type == ConfigType.MODULE:
            instances = config.instances or []
            hover_text += f"**Instances:** {len(instances)}\n"

            external_interfaces = config.external_interfaces or {}
            inputs = external_interfaces.get('input', [])
            outputs = external_interfaces.get('output', [])
            hover_text += f"**External Inputs:** {len(inputs)}\n"
            hover_text += f"**External Outputs:** {len(outputs)}\n"

            if instances:
                hover_text += "\n### Instances\n"
                for instance in instances[:5]:  # Show first 5
                    inst_name = instance.get('instance', 'unknown')
                    entity = instance.get('entity', 'unknown')
                    hover_text += f"- `{inst_name}`: {entity}\n"
                if len(instances) > 5:
                    hover_text += f"_... and {len(instances) - 5} more_\n"

        elif config.entity_type == ConfigType.SYSTEM:
            modes = config.modes or []
            components = config.components or []
            hover_text += f"**Modes:** {len(modes)}\n"
            hover_text += f"**Components:** {len(components)}\n"

            if components:
                hover_text += "\n### Components\n"
                for component in components[:5]:  # Show first 5
                    comp_name = component.get('component', 'unknown')
                    entity = component.get('entity', 'unknown')
                    hover_text += f"- `{comp_name}`: {entity}\n"
                if len(components) > 5:
                    hover_text += f"_... and {len(components) - 5} more_\n"

        elif config.entity_type == ConfigType.PARAMETER_SET:
            parameters = config.parameters or []
            hover_text += f"**Parameters:** {len(parameters)}\n"

        return lsp.Hover(
            contents=lsp.MarkupContent(
                kind=lsp.MarkupKind.Markdown,
                value=hover_text
            )
        )
