import re
from typing import List, Optional, Set
from lsprotocol import types as lsp

from autoware_system_designer.models.config import Config, ConfigType
from registry_manager import RegistryManager
from utils.uri_utils import uri_to_path


class InlayHintProvider:
    """Provides inlay hints for port types."""

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

        # Get current file config to understand context
        # Although we mainly parse strings, having context helps if we need to know we are in a Module vs System
        # but the syntax `instance.input.port` implies we are looking up that instance.

        for i in range(start_line, end_line):
            line = document.lines[i]
            # Look for patterns like:
            # - instance.input.port
            # - instance.output.port
            # - input.port (for external interfaces)
            # - output.port (for external interfaces)
            
            # Simple regex to catch these patterns
            # Matches: word.input.word or word.output.word or input.word or output.word
            # We need to be careful not to match random text, but in YAML 'from:' or 'to:' usually precedes
            
            # Check for specific connection patterns in YAML
            # We expect keys like 'from:' or 'to:' 
            # Updated regex to include / and - which are common in ROS topic names / port names
            match = re.search(r'(?:from|to):\s*([\w\.\*/-]+)', line)
            if match:
                connection_str = match.group(1)
                # Skip wildcards for now
                if '*' in connection_str:
                    continue
                    
                msg_type = self._resolve_type_for_string(connection_str, params.text_document.uri)
                if msg_type:
                    # Create inlay hint at the end of the line
                    # Position at the end of the connection string
                    char_idx = match.end(1)
                    position = lsp.Position(line=i, character=char_idx)
                    
                    hint = lsp.InlayHint(
                        position=position,
                        label=f": {msg_type}",
                        kind=lsp.InlayHintKind.Type,
                        padding_left=True
                    )
                    hints.append(hint)

        return hints

    def _resolve_type_for_string(self, connection_str: str, current_uri: str) -> Optional[str]:
        """
        Resolve the type for a connection string like 'instance.input.port'.
        """
        # We need the current config to resolve 'instances' or 'self'
        file_path = uri_to_path(current_uri)
        current_config = self.registry_manager.get_entity_by_file(file_path)
        if not current_config:
            return None

        # Use ResolutionService logic
        from resolution_service import ResolutionService
        resolution_service = ResolutionService(self.registry_manager)

        parts = connection_str.split('.')
        if len(parts) == 3:
            # instance.input.port
            instance_name = parts[0]
            port_type = parts[1]
            port_name = parts[2]
            
            target_entity_config = resolution_service.get_instance_entity(current_config, instance_name)
            if target_entity_config:
                return resolution_service.resolve_port_type(target_entity_config, port_type, port_name)

        elif len(parts) == 2:
            # input.port (self)
            port_type = parts[0] # input or output
            port_name = parts[1]
            return resolution_service.resolve_port_type(current_config, port_type, port_name)

        return None

    # Remove all the duplicated recursive logic below

