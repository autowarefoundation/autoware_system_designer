#!/usr/bin/env python3

import yaml
import re
import logging
from typing import List, Optional, Tuple
from lsprotocol import types as lsp

from autoware_system_designer.models.config import Config, ConfigType

from registry_manager import RegistryManager
from resolution_service import ResolutionService

logger = logging.getLogger(__name__)


class ValidationEngine:
    """Handles validation of connections and references."""

    def __init__(self, registry_manager: RegistryManager):
        self.registry_manager = registry_manager
        self.resolution_service = ResolutionService(registry_manager)

    def validate_all(self, config: Config, document_content: str = None) -> List[lsp.Diagnostic]:
        """Validate all aspects of the config and return diagnostics."""
        diagnostics = []

        # Validate YAML format first
        try:
            if document_content:
                diagnostics.extend(self.validate_yaml_format(document_content))
        except Exception as e:
            logger.warning(f"Error during YAML format validation: {e}")

        # Validate file name matching
        try:
            diagnostics.extend(self.validate_filename_matching(config, document_content))
        except Exception as e:
            logger.warning(f"Error during filename matching validation: {e}")

        # Validate connections
        try:
            diagnostics.extend(self.validate_connections(config, document_content))
        except Exception as e:
            logger.warning(f"Error during connection validation: {e}")

        # Validate incomplete references (warnings instead of completions)
        try:
            if document_content:
                diagnostics.extend(self.validate_incomplete_references(config, document_content))
        except Exception as e:
            logger.warning(f"Error during incomplete reference validation: {e}")

        return diagnostics

    def validate_filename_matching(self, config: Config, document_content: str = None) -> List[lsp.Diagnostic]:
        """Validate that the file name matches the design format name."""
        diagnostics = []

        if not document_content:
            return diagnostics

        try:
            # Extract the name from document content (to catch unsaved changes)
            name_from_content = self._extract_name_from_content(document_content)
            
            # Safely get filename stem
            file_path = config.file_path
            if isinstance(file_path, str):
                from pathlib import Path
                file_path = Path(file_path)
                
            actual_filename = file_path.stem  # filename without extension

            # Compare the name from content with the filename
            if name_from_content and name_from_content != actual_filename:
                # Find the name field range to underline it
                name_range = self._find_name_field_range(document_content)

                if name_range:
                    diagnostics.append(lsp.Diagnostic(
                        range=name_range,
                        message=f"File name '{actual_filename}' does not match design name '{name_from_content}'. Expected: '{actual_filename}'",
                        severity=lsp.DiagnosticSeverity.Error
                    ))
                else:
                    # Fallback: put diagnostic at the beginning
                    diagnostics.append(lsp.Diagnostic(
                        range=lsp.Range(
                            start=lsp.Position(line=0, character=0),
                            end=lsp.Position(line=0, character=1)
                        ),
                        message=f"File name '{actual_filename}' does not match design name '{name_from_content}'. Expected: '{actual_filename}'",
                        severity=lsp.DiagnosticSeverity.Error
                    ))
        except Exception:
            # Fallback if validation fails (e.g. file path issues)
            pass

        return diagnostics

    def validate_filename_matching_from_content(self, document_content: str, file_path: str) -> List[lsp.Diagnostic]:
        """Validate filename matching from content and file path without requiring a config object."""
        diagnostics = []
        
        if not document_content:
            return diagnostics

        from pathlib import Path
        file_path_obj = Path(file_path)
        actual_filename = file_path_obj.stem  # filename without extension
        
        # Extract the name from document content
        name_from_content = self._extract_name_from_content(document_content)
        
        # Compare the name from content with the filename
        if name_from_content and name_from_content != actual_filename:
            # Find the name field range to underline it
            name_range = self._find_name_field_range(document_content)
            
            message = f"File name '{actual_filename}' does not match design name '{name_from_content}'. Expected: '{actual_filename}'"

            if name_range:
                diagnostics.append(lsp.Diagnostic(
                    range=name_range,
                    message=message,
                    severity=lsp.DiagnosticSeverity.Error
                ))
            else:
                # Fallback: put diagnostic at the beginning
                diagnostics.append(lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=1)
                    ),
                    message=message,
                    severity=lsp.DiagnosticSeverity.Error
                ))

        return diagnostics

    def validate_yaml_format(self, document_content: str) -> List[lsp.Diagnostic]:
        """Validate YAML format and syntax."""
        diagnostics = []
        
        try:
            yaml.safe_load(document_content)
        except yaml.YAMLError as e:
            # Try to extract line number from error
            error_msg = str(e)
            line_num = 0
            if hasattr(e, 'problem_mark') and e.problem_mark:
                line_num = e.problem_mark.line
            elif 'line' in error_msg.lower():
                # Try to extract line number from error message
                import re
                match = re.search(r'line\s+(\d+)', error_msg, re.IGNORECASE)
                if match:
                    line_num = int(match.group(1)) - 1  # Convert to 0-based
            
            lines = document_content.split('\n')
            if line_num < len(lines):
                line = lines[line_num]
                diagnostics.append(lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=line_num, character=0),
                        end=lsp.Position(line=line_num, character=len(line))
                    ),
                    message=f"YAML syntax error: {error_msg}",
                    severity=lsp.DiagnosticSeverity.Error
                ))
            else:
                # Fallback if we can't determine the line
                diagnostics.append(lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=1)
                    ),
                    message=f"YAML syntax error: {error_msg}",
                    severity=lsp.DiagnosticSeverity.Error
                ))
        
        return diagnostics

    def validate_connections(self, config: Config, document_content: str = None) -> List[lsp.Diagnostic]:
        """Validate connections in the config and return diagnostics."""
        diagnostics = []

        if config.entity_type == ConfigType.MODULE:
            connections = config.connections or []
            for i, connection in enumerate(connections):
                from_ref = connection.get('from', '')
                to_ref = connection.get('to', '')

                # Validate from reference
                from_valid, from_message = self._validate_connection_reference(from_ref, config)
                if not from_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'from', document_content),
                        message=f"Invalid connection source: {from_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate to reference
                to_valid, to_message = self._validate_connection_reference(to_ref, config)
                if not to_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'to', document_content),
                        message=f"Invalid connection destination: {to_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate message type compatibility if both are valid
                if from_valid and to_valid and from_ref and to_ref:
                    compatibility_issue = self._check_message_type_compatibility(from_ref, to_ref, config)
                    if compatibility_issue:
                        diagnostics.append(lsp.Diagnostic(
                            range=self._get_connection_range(i, 'to', document_content),
                            message=f"Message type mismatch: {compatibility_issue}",
                            severity=lsp.DiagnosticSeverity.Error
                        ))

        elif config.entity_type == ConfigType.SYSTEM:
            connections = config.connections or []
            for i, connection in enumerate(connections):
                from_ref = connection.get('from', '')
                to_ref = connection.get('to', '')

                # Validate from reference
                from_valid, from_message = self._validate_connection_reference(from_ref, config)
                if not from_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'from', document_content),
                        message=f"Invalid connection source: {from_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate to reference
                to_valid, to_message = self._validate_connection_reference(to_ref, config)
                if not to_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'to', document_content),
                        message=f"Invalid connection destination: {to_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate message type compatibility if both are valid
                if from_valid and to_valid and from_ref and to_ref:
                    compatibility_issue = self._check_message_type_compatibility(from_ref, to_ref, config)
                    if compatibility_issue:
                        diagnostics.append(lsp.Diagnostic(
                            range=self._get_connection_range(i, 'to', document_content),
                            message=f"Message type mismatch: {compatibility_issue}",
                            severity=lsp.DiagnosticSeverity.Error
                        ))

        return diagnostics

    def _get_entity_inputs(self, config: Config) -> List[dict]:
        """Get input ports from a config, handling both Node and Module types."""
        if hasattr(config, 'inputs') and config.inputs:
            return config.inputs
        
        if hasattr(config, 'external_interfaces'):
            ext = config.external_interfaces or {}
            if isinstance(ext, dict):
                return ext.get('input', [])
        
        return []

    def _get_entity_outputs(self, config: Config) -> List[dict]:
        """Get output ports from a config, handling both Node and Module types."""
        if hasattr(config, 'outputs') and config.outputs:
            return config.outputs
        
        if hasattr(config, 'external_interfaces'):
            ext = config.external_interfaces or {}
            if isinstance(ext, dict):
                return ext.get('output', [])
        
        return []

    def _validate_connection_reference(self, ref: str, config: Config) -> Tuple[bool, str]:
        """Validate if a connection reference is valid."""
        if not ref:
            return False, "Empty reference"

        # Handle wildcard references
        if '*' in ref:
            return True, ""  # Wildcards are allowed for now

        # Parse reference (e.g., "input.pointcloud", "node_detector.output.objects")
        parts = ref.split('.')

        if len(parts) < 2:
            return False, f"Invalid reference format: {ref}"

        if config.entity_type == ConfigType.MODULE:
            if parts[0] == 'input':
                # Check external interfaces
                external_interfaces = config.external_interfaces or {}
                inputs = external_interfaces.get('input', [])
                if not inputs:
                    return False, f"No input interfaces defined in module {config.name}"

                input_names = [interface.get('name') for interface in inputs if interface.get('name')]
                if parts[1] not in input_names:
                    return False, f"Input '{parts[1]}' not found. Available inputs: {', '.join(input_names)}"
                return True, ""

            elif parts[0] == 'output':
                # Check external interfaces
                external_interfaces = config.external_interfaces or {}
                outputs = external_interfaces.get('output', [])
                if not outputs:
                    return False, f"No output interfaces defined in module {config.name}"

                output_names = [interface.get('name') for interface in outputs if interface.get('name')]
                if parts[1] not in output_names:
                    return False, f"Output '{parts[1]}' not found. Available outputs: {', '.join(output_names)}"
                return True, ""

            else:
                # Check instance ports
                instance_name = parts[0]
                port_type = parts[1] if len(parts) > 1 else None
                port_name = parts[2] if len(parts) > 2 else None

                if not port_type or not port_name:
                    return False, f"Invalid instance reference format: {ref}"

                instances = config.instances or []
                instance_names = [inst.get('instance') for inst in instances if inst.get('instance')]
                if instance_name not in instance_names:
                    return False, f"Instance '{instance_name}' not found. Available instances: {', '.join(instance_names)}"

                for instance in instances:
                    if instance.get('instance') == instance_name:
                        entity_name = instance.get('entity')
                        if entity_name not in self.registry_manager.entity_registry:
                            return False, f"Entity '{entity_name}' not found in registry"

                        entity_config = self.registry_manager.entity_registry[entity_name]
                        if port_type == 'input':
                            inputs = self._get_entity_inputs(entity_config)
                            if not inputs:
                                return False, f"Entity '{entity_name}' has no input ports"
                            input_names = [port.get('name') for port in inputs if port.get('name')]
                            if port_name not in input_names:
                                return False, f"Input port '{port_name}' not found in entity '{entity_name}'. Available inputs: {', '.join(input_names)}"
                        elif port_type == 'output':
                            outputs = self._get_entity_outputs(entity_config)
                            if not outputs:
                                return False, f"Entity '{entity_name}' has no output ports"
                            output_names = [port.get('name') for port in outputs if port.get('name')]
                            if port_name not in output_names:
                                return False, f"Output port '{port_name}' not found in entity '{entity_name}'. Available outputs: {', '.join(output_names)}"
                        else:
                            return False, f"Invalid port type '{port_type}'. Must be 'input' or 'output'"
                        return True, ""
                return False, f"Instance '{instance_name}' configuration error"

        elif config.entity_type == ConfigType.SYSTEM:
            # System connections reference component ports
            component_name = parts[0]
            port_type = parts[1] if len(parts) > 1 else None
            port_name = parts[2] if len(parts) > 2 else None

            if not port_type or not port_name:
                return False, f"Invalid component reference format: {ref}"

            components = config.components or []
            component_names = [comp.get('name') for comp in components if comp.get('name')]
            if component_name not in component_names:
                return False, f"Component '{component_name}' not found. Available components: {', '.join(component_names)}"

            for component in components:
                if component.get('name') == component_name:
                    component_entity = component.get('entity')
                    if component_entity not in self.registry_manager.entity_registry:
                        return False, f"Entity '{component_entity}' not found in registry"

                    entity_config = self.registry_manager.entity_registry[component_entity]
                    if port_type == 'input':
                        inputs = self._get_entity_inputs(entity_config)
                        if not inputs:
                            return False, f"Entity '{component_entity}' has no input ports"
                        input_names = [port.get('name') for port in inputs if port.get('name')]
                        if port_name not in input_names:
                            return False, f"Input port '{port_name}' not found in component '{component_name}'. Available inputs: {', '.join(input_names)}"
                    elif port_type == 'output':
                        outputs = self._get_entity_outputs(entity_config)
                        if not outputs:
                            return False, f"Entity '{component_entity}' has no output ports"
                        output_names = [port.get('name') for port in outputs if port.get('name')]
                        if port_name not in output_names:
                            return False, f"Output port '{port_name}' not found in component '{component_name}'. Available outputs: {', '.join(output_names)}"
                    else:
                        return False, f"Invalid port type '{port_type}'. Must be 'input' or 'output'"
                    return True, ""
            return False, f"Component '{component_name}' configuration error"

        return False, f"Unsupported reference format for {config.entity_type}: {ref}"

    def _check_message_type_compatibility(self, from_ref: str, to_ref: str, config: Config) -> Optional[str]:
        """Check if message types are compatible between source and destination."""
        from_type = self._get_message_type(from_ref, config)
        to_type = self._get_message_type(to_ref, config)

        if from_type and to_type and from_type != to_type:
            return f"Source type '{from_type}' does not match destination type '{to_type}'"

        return None

    def _get_message_type(self, ref: str, config: Config) -> Optional[str]:
        """Get the message type for a connection reference."""
        if '*' in ref:
            return None  # Wildcards don't have specific types

        parts = ref.split('.')
        # Reference validation is already done elsewhere, assuming valid structure mostly
        if len(parts) < 2:
            return None

        target_entity_config = config
        port_type = None
        port_name = None

        if config.entity_type == ConfigType.MODULE:
            if parts[0] == 'input':
                # External input interface
                # For validation inside the module, 'input.X' refers to the source of data coming IN.
                # Its type is determined by what feeds it externally? No, in module design, we define interfaces.
                # But typically we want to check consistency.
                # If we are checking "from: input.X", we want the type of X.
                # In our ResolutionService, resolve_port_type(module, 'input', X) checks internal connections
                # starting from input.X to find what it connects TO.
                # Wait, if input.X -> instance.input.Y. Then input.X "should be" type(Y).
                # ResolutionService logic:
                # if port_type == 'input': finds connection FROM input.X TO instance.input.Y. Returns type(Y).
                # This seems correct for "what type is required for input.X".
                port_type = 'input'
                port_name = parts[1]
            elif parts[0] == 'output':
                # External output interface
                # If "to: output.X", we want type of X.
                # ResolutionService: if port_type == 'output': finds connection FROM instance.output.Y TO output.X. Returns type(Y).
                port_type = 'output'
                port_name = parts[1]
            else:
                # instance.input.X or instance.output.X
                instance_name = parts[0]
                port_type = parts[1]
                port_name = parts[2] if len(parts) > 2 else None
                
                if not port_name:
                    return None
                    
                target_entity_config = self.resolution_service.get_instance_entity(config, instance_name)
                if not target_entity_config:
                    return None

        elif config.entity_type == ConfigType.SYSTEM:
            # component.input.X or component.output.X
            component_name = parts[0]
            port_type = parts[1]
            port_name = parts[2] if len(parts) > 2 else None
            
            if not port_name:
                return None
                
            target_entity_config = self.resolution_service.get_instance_entity(config, component_name)
            if not target_entity_config:
                return None
                
        if target_entity_config and port_type and port_name:
            return self.resolution_service.resolve_port_type(target_entity_config, port_type, port_name)

        return None

    def _get_connection_range(self, connection_index: int, field: str, document_content: str = None) -> lsp.Range:
        """Get the range for a connection field."""
        if document_content:
            # Try to find the actual line in the document
            lines = document_content.split('\n')
            connections_found = 0
            in_connections_section = False
            
            for line_num, line in enumerate(lines):
                stripped = line.strip()
                
                # Check if we're entering the connections section
                if stripped == 'connections:' or stripped.startswith('connections:'):
                    in_connections_section = True
                    continue
                
                # Check if we're leaving the connections section (new top-level key)
                if in_connections_section and stripped and not line[0].isspace() and not stripped.startswith('-'):
                    in_connections_section = False
                    continue
                
                # Look for connection items (lines starting with '-')
                if in_connections_section and stripped.startswith('-'):
                    # This might be the start of a new connection
                    # Look ahead to find 'from:' or 'to:' fields
                    connection_start_line = line_num
                    found_field = False
                    
                    # Look ahead in the same connection block
                    for next_line_num in range(line_num, min(line_num + 10, len(lines))):
                        next_line = lines[next_line_num]
                        next_stripped = next_line.strip()
                        
                        # Check if this line contains our field
                        if f'{field}:' in next_stripped:
                            if connections_found == connection_index:
                                # Found the right connection, get the field value
                                colon_pos = next_line.find(':')
                                if colon_pos != -1:
                                    value = next_line[colon_pos + 1:].strip().strip('"\'')
                                    value_start_pos = next_line.find(value, colon_pos)
                                    if value_start_pos == -1:
                                        value_start_pos = colon_pos + 1
                                    return lsp.Range(
                                        start=lsp.Position(line=next_line_num, character=value_start_pos),
                                        end=lsp.Position(line=next_line_num, character=value_start_pos + len(value))
                                    )
                            found_field = True
                            break
                        
                        # If we hit another connection item or top-level key, stop
                        if next_line_num > line_num:
                            if (next_stripped.startswith('-') and next_line_num != line_num) or \
                               (next_stripped and not next_line[0].isspace() and not next_stripped.startswith('-')):
                                break
                    
                    if found_field:
                        connections_found += 1
        
        # Fallback: approximate line number
        line = 20 + connection_index * 5
        return lsp.Range(
            start=lsp.Position(line=line, character=0),
            end=lsp.Position(line=line, character=50)
        )

    def _extract_name_from_content(self, document_content: str) -> Optional[str]:
        """Extract the name value from document content."""
        # Use regex to find "name: value" at the start of a line
        # Handles optional quotes and comments
        pattern = r'^name:\s*(?P<quote>[\'"]?)(?P<name>.*?)(?P=quote)\s*(?:#.*)?$'
        
        lines = document_content.splitlines()
        for line in lines:
            match = re.match(pattern, line)
            if match:
                return match.group('name')
        return None

    def _find_name_field_range(self, document_content: str) -> Optional[lsp.Range]:
        """Find the range of the name field value in the document content."""
        pattern = r'^name:\s*(?P<quote>[\'"]?)(?P<name>.*?)(?P=quote)\s*(?:#.*)?$'
        lines = document_content.splitlines()
        for line_num, line in enumerate(lines):
            match = re.match(pattern, line)
            if match:
                # Get the value range
                value_start = match.start('name')
                value_end = match.end('name')
                
                # Include quotes if present
                quote = match.group('quote')
                if quote:
                    value_start -= len(quote)
                    value_end += len(quote)
                
                return lsp.Range(
                    start=lsp.Position(line=line_num, character=value_start),
                    end=lsp.Position(line=line_num, character=value_end)
                )
        return None

    def validate_incomplete_references(self, config: Config, document_content: str) -> List[lsp.Diagnostic]:
        """Validate incomplete references and show warnings with underlines."""
        diagnostics = []
        lines = document_content.split('\n')

        for line_num, line in enumerate(lines):
            stripped = line.strip()

            # Check for entity references that might be incomplete
            if 'entity:' in stripped:
                entity_value = stripped.split(':', 1)[1].strip().strip('"\'')
                if entity_value and not self._is_valid_entity_reference(entity_value):
                    # Find the entity value in the line
                    value_start = line.find(entity_value)
                    if value_start != -1:
                        diagnostics.append(lsp.Diagnostic(
                            range=lsp.Range(
                                start=lsp.Position(line=line_num, character=value_start),
                                end=lsp.Position(line=line_num, character=value_start + len(entity_value))
                            ),
                            message=f"Entity '{entity_value}' not found in registry",
                            severity=lsp.DiagnosticSeverity.Error
                        ))

            # Check for connection references that might be incomplete
            elif 'from:' in stripped or 'to:' in stripped:
                ref_value = stripped.split(':', 1)[1].strip().strip('"\'')
                if ref_value and not ref_value.startswith('*'):  # Skip wildcards
                    # Basic validation - check if it looks like a reference but might be incomplete
                    if '.' in ref_value and not self._is_valid_connection_reference(ref_value, config):
                        # Find the reference value in the line
                        value_start = line.find(ref_value)
                        if value_start != -1:
                            diagnostics.append(lsp.Diagnostic(
                                range=lsp.Range(
                                    start=lsp.Position(line=line_num, character=value_start),
                                    end=lsp.Position(line=line_num, character=value_start + len(ref_value))
                                ),
                                message=f"Connection reference '{ref_value}' may be incomplete or invalid",
                                severity=lsp.DiagnosticSeverity.Error
                            ))

            # Check for message types that might be incomplete
            elif 'message_type:' in stripped:
                msg_type = stripped.split(':', 1)[1].strip().strip('"\'')
                if msg_type and '/' in msg_type and not self._is_valid_message_type(msg_type):
                    # Find the message type in the line
                    value_start = line.find(msg_type)
                    if value_start != -1:
                        diagnostics.append(lsp.Diagnostic(
                            range=lsp.Range(
                                start=lsp.Position(line=line_num, character=value_start),
                                end=lsp.Position(line=line_num, character=value_start + len(msg_type))
                            ),
                            message=f"Message type '{msg_type}' may not be valid",
                            severity=lsp.DiagnosticSeverity.Error
                        ))

        return diagnostics

    def _is_valid_entity_reference(self, entity_name: str) -> bool:
        """Check if an entity reference is valid."""
        return entity_name in self.registry_manager.entity_registry

    def _is_valid_connection_reference(self, ref: str, config: Config) -> bool:
        """Check if a connection reference is valid (simplified check)."""
        valid, _ = self._validate_connection_reference(ref, config)
        return valid

    def _is_valid_message_type(self, msg_type: str) -> bool:
        """Check if a message type looks valid (basic check)."""
        # Basic check for ROS 2 message type format
        return '/' in msg_type and msg_type.count('/') >= 1
