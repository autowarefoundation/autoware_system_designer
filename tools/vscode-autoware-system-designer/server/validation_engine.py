#!/usr/bin/env python3

import logging
import re
from typing import List, Optional, Tuple

import yaml
from lsprotocol import types as lsp
from registry_manager import RegistryManager
from resolution_service import ResolutionService

from autoware_system_designer.models.config import Config, ConfigType

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
                    diagnostics.append(
                        lsp.Diagnostic(
                            range=name_range,
                            message=f"File name '{actual_filename}' does not match design name '{name_from_content}'. Expected: '{actual_filename}'",
                            severity=lsp.DiagnosticSeverity.Error,
                        )
                    )
                else:
                    # Fallback: put diagnostic at the beginning
                    diagnostics.append(
                        lsp.Diagnostic(
                            range=lsp.Range(
                                start=lsp.Position(line=0, character=0),
                                end=lsp.Position(line=0, character=1),
                            ),
                            message=f"File name '{actual_filename}' does not match design name '{name_from_content}'. Expected: '{actual_filename}'",
                            severity=lsp.DiagnosticSeverity.Error,
                        )
                    )
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
                diagnostics.append(
                    lsp.Diagnostic(range=name_range, message=message, severity=lsp.DiagnosticSeverity.Error)
                )
            else:
                # Fallback: put diagnostic at the beginning
                diagnostics.append(
                    lsp.Diagnostic(
                        range=lsp.Range(
                            start=lsp.Position(line=0, character=0),
                            end=lsp.Position(line=0, character=1),
                        ),
                        message=message,
                        severity=lsp.DiagnosticSeverity.Error,
                    )
                )

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
            if hasattr(e, "problem_mark") and e.problem_mark:
                line_num = e.problem_mark.line
            elif "line" in error_msg.lower():
                # Try to extract line number from error message
                import re

                match = re.search(r"line\s+(\d+)", error_msg, re.IGNORECASE)
                if match:
                    line_num = int(match.group(1)) - 1  # Convert to 0-based

            lines = document_content.split("\n")
            if line_num < len(lines):
                line = lines[line_num]
                diagnostics.append(
                    lsp.Diagnostic(
                        range=lsp.Range(
                            start=lsp.Position(line=line_num, character=0),
                            end=lsp.Position(line=line_num, character=len(line)),
                        ),
                        message=f"YAML syntax error: {error_msg}",
                        severity=lsp.DiagnosticSeverity.Error,
                    )
                )
            else:
                # Fallback if we can't determine the line
                diagnostics.append(
                    lsp.Diagnostic(
                        range=lsp.Range(
                            start=lsp.Position(line=0, character=0),
                            end=lsp.Position(line=0, character=1),
                        ),
                        message=f"YAML syntax error: {error_msg}",
                        severity=lsp.DiagnosticSeverity.Error,
                    )
                )

        return diagnostics

    def validate_connections(self, config: Config, document_content: str = None) -> List[lsp.Diagnostic]:
        """Validate connections in the config and return diagnostics."""
        diagnostics = []

        if config.entity_type in [ConfigType.MODULE, ConfigType.SYSTEM]:
            connections = config.connections or []
            for i, connection in enumerate(connections):
                # Connections are stored as 2-element lists [source, dest]
                if isinstance(connection, (list, tuple)) and len(connection) >= 2:
                    from_ref = str(connection[0])
                    to_ref = str(connection[1])
                elif isinstance(connection, dict):
                    from_ref = connection.get("from", "")
                    to_ref = connection.get("to", "")
                else:
                    continue

                # Validate from reference
                from_valid, from_message = self._validate_connection_reference(from_ref, config)
                if not from_valid:
                    diagnostics.append(
                        lsp.Diagnostic(
                            range=self._get_connection_range(i, document_content),
                            message=f"Invalid connection source: {from_message}",
                            severity=lsp.DiagnosticSeverity.Error,
                        )
                    )

                # Validate to reference
                to_valid, to_message = self._validate_connection_reference(to_ref, config)
                if not to_valid:
                    diagnostics.append(
                        lsp.Diagnostic(
                            range=self._get_connection_range(i, document_content),
                            message=f"Invalid connection destination: {to_message}",
                            severity=lsp.DiagnosticSeverity.Error,
                        )
                    )

        return diagnostics

    def _get_entity_inputs(self, config: Config) -> List[dict]:
        """Get input ports from a config, handling both Node and Module types."""
        if hasattr(config, "inputs") and config.inputs:
            return config.inputs

        if hasattr(config, "external_interfaces"):
            ext = config.external_interfaces or {}
            if isinstance(ext, dict):
                return ext.get("input", [])

        return []

    def _get_entity_outputs(self, config: Config) -> List[dict]:
        """Get output ports from a config, handling both Node and Module types."""
        if hasattr(config, "outputs") and config.outputs:
            return config.outputs

        if hasattr(config, "external_interfaces"):
            ext = config.external_interfaces or {}
            if isinstance(ext, dict):
                return ext.get("output", [])

        return []

    # Port direction terms used in YAML connection strings map to stored inputs/outputs
    _INPUT_TERMS = {"input", "subscriber", "server"}
    _OUTPUT_TERMS = {"output", "publisher", "client"}

    def _validate_connection_reference(self, ref: str, config: Config) -> Tuple[bool, str]:
        """Validate if a connection reference is valid."""
        if not ref:
            return False, "Empty reference"

        # Handle wildcard references
        if "*" in ref:
            return True, ""  # Wildcards are allowed for now

        # Parse reference (e.g., "subscriber.pointcloud", "node_detector.publisher.objects")
        parts = ref.split(".")

        if len(parts) < 2:
            return False, f"Invalid reference format: {ref}"

        if config.entity_type == ConfigType.MODULE:
            if parts[0] in self._INPUT_TERMS:
                # External input interface of the module itself
                inputs = config.inputs or []
                if not inputs:
                    return False, f"No input interfaces defined in module {config.name}"
                input_names = [iface.get("name") for iface in inputs if iface.get("name")]
                if parts[1] not in input_names:
                    return (
                        False,
                        f"Input '{parts[1]}' not found. Available inputs: {', '.join(input_names)}",
                    )
                return True, ""

            elif parts[0] in self._OUTPUT_TERMS:
                # External output interface of the module itself
                outputs = config.outputs or []
                if not outputs:
                    return False, f"No output interfaces defined in module {config.name}"
                output_names = [iface.get("name") for iface in outputs if iface.get("name")]
                if parts[1] not in output_names:
                    return (
                        False,
                        f"Output '{parts[1]}' not found. Available outputs: {', '.join(output_names)}",
                    )
                return True, ""

            else:
                # Instance port: instance_name.direction.port_name
                instance_name = parts[0]
                port_dir = parts[1] if len(parts) > 1 else None
                port_name = parts[2] if len(parts) > 2 else None

                if not port_dir or not port_name:
                    return False, f"Invalid instance reference format: {ref}"

                instances = config.instances or []
                instance_names = [inst.get("name") for inst in instances if inst.get("name")]
                if instance_name not in instance_names:
                    return (
                        False,
                        f"Instance '{instance_name}' not found. Available instances: {', '.join(instance_names)}",
                    )

                for instance in instances:
                    if instance.get("name") == instance_name:
                        entity_name = instance.get("entity")
                        if entity_name not in self.registry_manager.entity_registry:
                            return False, f"Entity '{entity_name}' not found in registry"

                        entity_config = self.registry_manager.entity_registry[entity_name]
                        if port_dir in self._INPUT_TERMS:
                            inputs = self._get_entity_inputs(entity_config)
                            if not inputs:
                                return False, f"Entity '{entity_name}' has no input ports"
                            input_names = [port.get("name") for port in inputs if port.get("name")]
                            if port_name not in input_names:
                                return (
                                    False,
                                    f"Input port '{port_name}' not found in entity '{entity_name}'. Available inputs: {', '.join(input_names)}",
                                )
                        elif port_dir in self._OUTPUT_TERMS:
                            outputs = self._get_entity_outputs(entity_config)
                            if not outputs:
                                return False, f"Entity '{entity_name}' has no output ports"
                            output_names = [port.get("name") for port in outputs if port.get("name")]
                            if port_name not in output_names:
                                return (
                                    False,
                                    f"Output port '{port_name}' not found in entity '{entity_name}'. Available outputs: {', '.join(output_names)}",
                                )
                        else:
                            return (
                                False,
                                f"Invalid port direction '{port_dir}'. Must be one of: subscriber, publisher, server, client",
                            )
                        return True, ""
                return False, f"Instance '{instance_name}' configuration error"

        elif config.entity_type == ConfigType.SYSTEM:
            # System connections reference component ports: component.direction.port_name
            component_name = parts[0]
            port_dir = parts[1] if len(parts) > 1 else None
            port_name = parts[2] if len(parts) > 2 else None

            if not port_dir or not port_name:
                return False, f"Invalid component reference format: {ref}"

            components = config.components or []
            component_names = [comp.get("name") for comp in components if comp.get("name")]
            if component_name not in component_names:
                return (
                    False,
                    f"Component '{component_name}' not found. Available components: {', '.join(component_names)}",
                )

            for component in components:
                if component.get("name") == component_name:
                    component_entity = component.get("entity")
                    if component_entity not in self.registry_manager.entity_registry:
                        return False, f"Entity '{component_entity}' not found in registry"

                    entity_config = self.registry_manager.entity_registry[component_entity]
                    if port_dir in self._INPUT_TERMS:
                        inputs = self._get_entity_inputs(entity_config)
                        if not inputs:
                            return False, f"Entity '{component_entity}' has no input ports"
                        input_names = [port.get("name") for port in inputs if port.get("name")]
                        if port_name not in input_names:
                            return (
                                False,
                                f"Input port '{port_name}' not found in component '{component_name}'. Available inputs: {', '.join(input_names)}",
                            )
                    elif port_dir in self._OUTPUT_TERMS:
                        outputs = self._get_entity_outputs(entity_config)
                        if not outputs:
                            return False, f"Entity '{component_entity}' has no output ports"
                        output_names = [port.get("name") for port in outputs if port.get("name")]
                        if port_name not in output_names:
                            return (
                                False,
                                f"Output port '{port_name}' not found in component '{component_name}'. Available outputs: {', '.join(output_names)}",
                            )
                    else:
                        return (
                            False,
                            f"Invalid port direction '{port_dir}'. Must be one of: subscriber, publisher, server, client",
                        )
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
        if "*" in ref:
            return None  # Wildcards don't have specific types

        parts = ref.split(".")
        if len(parts) < 2:
            return None

        target_entity_config = config
        port_type = None
        port_name = None

        if config.entity_type == ConfigType.MODULE:
            if parts[0] in self._INPUT_TERMS:
                port_type = "input"
                port_name = parts[1]
            elif parts[0] in self._OUTPUT_TERMS:
                port_type = "output"
                port_name = parts[1]
            else:
                # instance_name.direction.port_name
                instance_name = parts[0]
                port_dir = parts[1]
                port_name = parts[2] if len(parts) > 2 else None

                if not port_name:
                    return None

                if port_dir in self._INPUT_TERMS:
                    port_type = "input"
                elif port_dir in self._OUTPUT_TERMS:
                    port_type = "output"
                else:
                    return None

                target_entity_config = self.resolution_service.get_instance_entity(config, instance_name)
                if not target_entity_config:
                    return None

        elif config.entity_type == ConfigType.SYSTEM:
            # component_name.direction.port_name
            component_name = parts[0]
            port_dir = parts[1]
            port_name = parts[2] if len(parts) > 2 else None

            if not port_name:
                return None

            if port_dir in self._INPUT_TERMS:
                port_type = "input"
            elif port_dir in self._OUTPUT_TERMS:
                port_type = "output"
            else:
                return None

            target_entity_config = self.resolution_service.get_instance_entity(config, component_name)
            if not target_entity_config:
                return None

        if target_entity_config and port_type and port_name:
            return self.resolution_service.resolve_port_type(target_entity_config, port_type, port_name)

        return None

    def _get_connection_range(self, connection_index: int, document_content: str = None) -> lsp.Range:
        """Get the range for a connection entry (2-element list format)."""
        if document_content:
            lines = document_content.split("\n")
            connections_found = 0
            in_connections_section = False

            for line_num, line in enumerate(lines):
                stripped = line.strip()

                if stripped == "connections:" or stripped.startswith("connections:"):
                    in_connections_section = True
                    continue

                if in_connections_section and stripped and not line[0].isspace() and not stripped.startswith("-"):
                    in_connections_section = False
                    continue

                # Each connection entry starts with a double-dash list item: "  - - source"
                if in_connections_section and stripped.startswith("- -"):
                    if connections_found == connection_index:
                        return lsp.Range(
                            start=lsp.Position(line=line_num, character=0),
                            end=lsp.Position(line=line_num, character=len(line)),
                        )
                    connections_found += 1

        # Fallback: approximate line number
        line = 20 + connection_index * 3
        return lsp.Range(start=lsp.Position(line=line, character=0), end=lsp.Position(line=line, character=50))

    def _extract_name_from_content(self, document_content: str) -> Optional[str]:
        """Extract the name value from document content."""
        # Use regex to find "name: value" at the start of a line
        # Handles optional quotes and comments
        pattern = r'^name:\s*(?P<quote>[\'"]?)(?P<name>.*?)(?P=quote)\s*(?:#.*)?$'

        lines = document_content.splitlines()
        for line in lines:
            match = re.match(pattern, line)
            if match:
                return match.group("name")
        return None

    def _find_name_field_range(self, document_content: str) -> Optional[lsp.Range]:
        """Find the range of the name field value in the document content."""
        pattern = r'^name:\s*(?P<quote>[\'"]?)(?P<name>.*?)(?P=quote)\s*(?:#.*)?$'
        lines = document_content.splitlines()
        for line_num, line in enumerate(lines):
            match = re.match(pattern, line)
            if match:
                # Get the value range
                value_start = match.start("name")
                value_end = match.end("name")

                # Include quotes if present
                quote = match.group("quote")
                if quote:
                    value_start -= len(quote)
                    value_end += len(quote)

                return lsp.Range(
                    start=lsp.Position(line=line_num, character=value_start),
                    end=lsp.Position(line=line_num, character=value_end),
                )
        return None

    def validate_incomplete_references(self, config: Config, document_content: str) -> List[lsp.Diagnostic]:
        """Validate incomplete references and show warnings with underlines."""
        diagnostics = []
        lines = document_content.split("\n")

        for line_num, line in enumerate(lines):
            stripped = line.strip()

            # Check for entity references that might be incomplete
            if "entity:" in stripped:
                entity_value = stripped.split(":", 1)[1].strip().strip("\"'")
                if entity_value and not self._is_valid_entity_reference(entity_value):
                    # Find the entity value in the line
                    value_start = line.find(entity_value)
                    if value_start != -1:
                        diagnostics.append(
                            lsp.Diagnostic(
                                range=lsp.Range(
                                    start=lsp.Position(line=line_num, character=value_start),
                                    end=lsp.Position(line=line_num, character=value_start + len(entity_value)),
                                ),
                                message=f"Entity '{entity_value}' not found in registry",
                                severity=lsp.DiagnosticSeverity.Error,
                            )
                        )

            # Check for connection references that might be incomplete
            elif "from:" in stripped or "to:" in stripped:
                ref_value = stripped.split(":", 1)[1].strip().strip("\"'")
                if ref_value and not ref_value.startswith("*"):  # Skip wildcards
                    # Basic validation - check if it looks like a reference but might be incomplete
                    if "." in ref_value and not self._is_valid_connection_reference(ref_value, config):
                        # Find the reference value in the line
                        value_start = line.find(ref_value)
                        if value_start != -1:
                            diagnostics.append(
                                lsp.Diagnostic(
                                    range=lsp.Range(
                                        start=lsp.Position(line=line_num, character=value_start),
                                        end=lsp.Position(line=line_num, character=value_start + len(ref_value)),
                                    ),
                                    message=f"Connection reference '{ref_value}' may be incomplete or invalid",
                                    severity=lsp.DiagnosticSeverity.Error,
                                )
                            )

            # Check for message types that might be incomplete
            elif "message_type:" in stripped:
                msg_type = stripped.split(":", 1)[1].strip().strip("\"'")
                if msg_type and "/" in msg_type and not self._is_valid_message_type(msg_type):
                    # Find the message type in the line
                    value_start = line.find(msg_type)
                    if value_start != -1:
                        diagnostics.append(
                            lsp.Diagnostic(
                                range=lsp.Range(
                                    start=lsp.Position(line=line_num, character=value_start),
                                    end=lsp.Position(line=line_num, character=value_start + len(msg_type)),
                                ),
                                message=f"Message type '{msg_type}' may not be valid",
                                severity=lsp.DiagnosticSeverity.Error,
                            )
                        )

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
        return "/" in msg_type and msg_type.count("/") >= 1
