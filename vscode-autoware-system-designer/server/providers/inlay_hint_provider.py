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
        parts = connection_str.split('.')
        if len(parts) < 2:
            return None

        # Case 1: instance.input.port or instance.output.port (3 parts)
        # Case 2: input.port or output.port (2 parts) - referring to self (external interface)

        # We need the current config to resolve 'instances' or 'self'
        file_path = uri_to_path(current_uri)
        current_config = self.registry_manager.get_entity_by_file(file_path)
        if not current_config:
            return None

        if len(parts) == 3:
            instance_name = parts[0]
            port_type = parts[1]
            port_name = parts[2]
            
            # Find the instance in current config
            instances = current_config.instances or []
            entity_name = None
            for inst in instances:
                if inst.get('instance') == instance_name:
                    entity_name = inst.get('entity')
                    break
            
            # Also check components if it's a System
            if not entity_name and current_config.components:
                for comp in current_config.components:
                    if comp.get('name') == instance_name:
                        entity_name = comp.get('entity')
                        break
            
            if entity_name:
                entity_config = self.registry_manager.get_entity(entity_name)
                if entity_config:
                    return self._recursive_get_port_type(entity_config, port_type, port_name, set())

        elif len(parts) == 2:
            direction = parts[0] # input or output
            port_name = parts[1]
            
            return self._resolve_internal_connection(current_config, direction, port_name, set())

        return None

    def _recursive_get_port_type(self, entity_config: Config, port_type: str, port_name: str, visited: Set[str]) -> Optional[str]:
        """
        Recursively find the port type by traversing modules until a Node is found.
        """
        # Prevent infinite loops
        config_id = f"{entity_config.full_name}:{port_type}:{port_name}"
        if config_id in visited:
            return None
        visited.add(config_id)

        if entity_config.entity_type == ConfigType.NODE:
            # Base case: Node defines the type
            ports = entity_config.inputs if port_type == 'input' else entity_config.outputs
            if ports:
                for port in ports:
                    if port.get('name') == port_name:
                        return port.get('message_type')
            return None

        elif entity_config.entity_type == ConfigType.MODULE:
            # Recursive case: Module
            # We need to find what this port connects to INTERNALLY.
            return self._resolve_internal_connection(entity_config, port_type, port_name, visited)

        return None

    def _resolve_internal_connection(self, config: Config, port_type: str, port_name: str, visited: Set[str]) -> Optional[str]:
        """
        Find what an external port connects to inside the module/system and resolve that.
        """
        connections = config.connections or []
        
        target_connection_str = None
        
        # Exact match first
        if port_type == 'input':
            source_match = f"input.{port_name}"
            for conn in connections:
                if conn.get('from') == source_match:
                    target_connection_str = conn.get('to')
                    break
            
            # Try wildcard if no exact match
            if not target_connection_str:
                 for conn in connections:
                    src = conn.get('from', '')
                    if src.endswith('*') and src.startswith('input.'):
                         # e.g. input.* -> instance.input.*
                         # check if wildcard pattern matches
                         # input.* matches input.foo
                         target_connection_str = conn.get('to')
                         if target_connection_str:
                             # Replace * with actual port name
                             target_connection_str = target_connection_str.replace('*', port_name)
                         break

        elif port_type == 'output':
            target_match = f"output.{port_name}"
            for conn in connections:
                if conn.get('to') == target_match:
                    source_connection_str = conn.get('from')
                    if source_connection_str:
                        # For output, we look where it comes FROM
                        target_connection_str = source_connection_str
                        break
            
            # Try wildcard if no exact match
            if not target_connection_str:
                for conn in connections:
                    dst = conn.get('to', '')
                    if dst.endswith('*') and dst.startswith('output.'):
                        # e.g. instance.output.* -> output.*
                        source_connection_str = conn.get('from')
                        if source_connection_str:
                             # Replace * with actual port name
                             target_connection_str = source_connection_str.replace('*', port_name)
                        break

        if target_connection_str:
            return self._resolve_target_string_in_config(config, target_connection_str, visited)

        return None

    def _resolve_target_string_in_config(self, config: Config, connection_str: str, visited: Set[str]) -> Optional[str]:
        """
        Resolve a string like 'instance.input.port' within a specific config.
        """
        parts = connection_str.split('.')
        if len(parts) >= 3:
            instance_name = parts[0]
            port_type = parts[1]
            port_name = parts[2]
            
            instances = config.instances or []
            entity_name = None
            for inst in instances:
                if inst.get('instance') == instance_name:
                    entity_name = inst.get('entity')
                    break
                    
            if not entity_name and config.components:
                 for comp in config.components:
                    if comp.get('name') == instance_name:
                        entity_name = comp.get('entity')
                        break
            
            if entity_name:
                entity_config = self.registry_manager.get_entity(entity_name)
                if entity_config:
                    return self._recursive_get_port_type(entity_config, port_type, port_name, visited)
        
        return None
