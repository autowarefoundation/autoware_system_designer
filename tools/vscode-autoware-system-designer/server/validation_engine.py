#!/usr/bin/env python3

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import yaml
from lsprotocol import types as lsp
from registry_manager import RegistryManager
from resolution_service import ResolutionService
from utils.source_map_utils import source_map_range

from autoware_system_designer.builder.graph.link_manager import match_and_pair_wildcard_ports
from autoware_system_designer.model.config import Config, ConfigType
from autoware_system_designer.model.domain import PortDefinition
from autoware_system_designer.model.links import Connection

logger = logging.getLogger(__name__)

# Wildcard characters a connection reference may use; each one captures independently.
_WILDCARD_CHARS = "*^+"

# A resolved port entry: the entity owning the port, its direction, and the port itself.
_PortEntry = Tuple[Config, str, PortDefinition]


@dataclass
class _ConnectionGraph:
    """Port key space of one module or system, keyed as ``instance.port``.

    External declarations of the entity itself use an empty instance name, so they
    are keyed as ``.port``.
    """

    outputs: Dict[str, _PortEntry] = field(default_factory=dict)
    inputs: Dict[str, _PortEntry] = field(default_factory=dict)
    child_names: List[str] = field(default_factory=list)
    unresolved: Dict[str, Optional[str]] = field(default_factory=dict)


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
                            message=self._name_mismatch_message(name_from_content, actual_filename),
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
                            message=self._name_mismatch_message(name_from_content, actual_filename),
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

            message = self._name_mismatch_message(name_from_content, actual_filename)

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

    @staticmethod
    def _name_mismatch_message(design_name: str, file_stem: str) -> str:
        return (
            f"Design name '{design_name}' does not match file name '{file_stem}'. "
            f"Rename the file to '{design_name}.yaml' or set name to '{file_stem}'"
        )

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
        diagnostics: List[lsp.Diagnostic] = []

        if config.entity_type not in (ConfigType.MODULE, ConfigType.SYSTEM):
            return diagnostics

        connections = config.connections or []
        if not connections:
            return diagnostics

        graph = self._collect_port_keys(config)

        for index, connection in enumerate(connections):
            diagnostics.extend(self._validate_connection(index, connection, config, graph, document_content))

        return diagnostics

    def _collect_port_keys(self, config: Config) -> "_ConnectionGraph":
        """Collect the ``instance.port`` key space a connection is resolved against.

        Mirrors the key space the designer's link manager builds: children contribute
        ``child.port`` keys, the entity's own external declarations contribute ``.port``
        keys, and outputs and inputs are kept in separate namespaces.
        """
        graph = _ConnectionGraph()

        children = (config.instances or []) if config.entity_type == ConfigType.MODULE else (config.components or [])
        for child in children:
            if not isinstance(child, dict):
                continue
            child_name = child.get("name")
            if not child_name:
                continue
            graph.child_names.append(child_name)
            entity_name = child.get("entity")
            entity_config = self.registry_manager.get_entity(entity_name) if entity_name else None
            if entity_config is None:
                graph.unresolved[child_name] = entity_name
                continue
            for port in self._get_entity_outputs(entity_config):
                graph.outputs[f"{child_name}.{port.name}"] = (entity_config, "output", port)
            for port in self._get_entity_inputs(entity_config):
                graph.inputs[f"{child_name}.{port.name}"] = (entity_config, "input", port)

        # External declarations feed internal targets, so they sit on the opposite side.
        for port in self._get_entity_inputs(config):
            graph.outputs[f".{port.name}"] = (config, "input", port)
        for port in self._get_entity_outputs(config):
            graph.inputs[f".{port.name}"] = (config, "output", port)

        return graph

    def _validate_connection(
        self,
        index: int,
        connection,
        config: Config,
        graph: "_ConnectionGraph",
        document_content: str = None,
    ) -> List[lsp.Diagnostic]:
        """Validate one connection entry against the collected port key space."""
        diagnostics: List[lsp.Diagnostic] = []

        source_map = getattr(config, "source_map", None)

        refs = self._get_connection_refs(connection)
        if refs is None:
            return [
                self._connection_diagnostic(
                    index,
                    "from",
                    "Connection must be a list of 2 references or a mapping with exactly 2 values",
                    document_content,
                    source_map,
                )
            ]

        try:
            parsed = Connection(connection)
        except Exception as exc:  # DeploymentError and malformed reference strings
            return [self._connection_diagnostic(index, "from", str(exc), document_content, source_map)]

        from_side = "from" if self._first_reference_is_source(parsed, refs, connection) else "to"
        to_side = "to" if from_side == "from" else "from"

        # An unregistered entity is already reported on its own 'entity:' line.
        if parsed.from_instance in graph.unresolved or parsed.to_instance in graph.unresolved:
            return diagnostics

        from_key = f"{parsed.from_instance}.{parsed.from_port_name}"
        to_key = f"{parsed.to_instance}.{parsed.to_port_name}"

        if any(char in from_key or char in to_key for char in _WILDCARD_CHARS):
            pairs = match_and_pair_wildcard_ports(from_key, to_key, graph.outputs, graph.inputs)
            if not pairs:
                diagnostics.append(
                    self._connection_diagnostic(
                        index,
                        from_side,
                        f"No ports match the wildcard connection '{refs[0]}' -> '{refs[1]}'",
                        document_content,
                        source_map,
                    )
                )
            return diagnostics

        source_found = from_key in graph.outputs
        target_found = to_key in graph.inputs

        if not source_found:
            diagnostics.append(
                self._connection_diagnostic(
                    index,
                    from_side,
                    self._missing_port_message(parsed.from_instance, parsed.from_port_name, "output", config, graph),
                    document_content,
                    source_map,
                )
            )

        if not target_found:
            diagnostics.append(
                self._connection_diagnostic(
                    index,
                    to_side,
                    self._missing_port_message(parsed.to_instance, parsed.to_port_name, "input", config, graph),
                    document_content,
                    source_map,
                )
            )

        if source_found and target_found:
            mismatch = self._check_message_type_compatibility(graph.outputs[from_key], graph.inputs[to_key])
            if mismatch:
                diagnostics.append(
                    self._connection_diagnostic(
                        index,
                        from_side,
                        mismatch,
                        document_content,
                        source_map,
                        severity=lsp.DiagnosticSeverity.Warning,
                    )
                )

        return diagnostics

    def _missing_port_message(
        self, instance_name: str, port_name: str, direction: str, config: Config, graph: "_ConnectionGraph"
    ) -> str:
        """Build the diagnostic text for a connection endpoint with no matching port."""
        keys = graph.outputs if direction == "output" else graph.inputs
        kind = "Instance" if config.entity_type == ConfigType.MODULE else "Component"

        if not instance_name:
            declared = sorted(key[1:] for key in keys if key.startswith("."))
            declaration = "input" if direction == "output" else "output"
            return (
                f"External {declaration} '{port_name}' is not declared in {config.full_name}. "
                f"Declared: {', '.join(declared) if declared else '(none)'}"
            )

        if instance_name not in graph.child_names:
            available = ", ".join(graph.child_names) if graph.child_names else "(none)"
            return f"{kind} '{instance_name}' not found. Available {kind.lower()}s: {available}"

        prefix = f"{instance_name}."
        available = sorted(key[len(prefix) :] for key in keys if key.startswith(prefix))
        return (
            f"{direction.capitalize()} port '{port_name}' not found in {kind.lower()} '{instance_name}'. "
            f"Available {direction}s: {', '.join(available) if available else '(none)'}"
        )

    def _check_message_type_compatibility(self, source, target) -> Optional[str]:
        """Compare the message types of a resolved source/target port pair."""
        from_type = self._resolve_port_message_type(*source)
        to_type = self._resolve_port_message_type(*target)

        if from_type and to_type and from_type != to_type:
            return f"Source type '{from_type}' does not match destination type '{to_type}'"

        return None

    def _resolve_port_message_type(self, owner: Config, direction: str, port: PortDefinition) -> Optional[str]:
        """Resolve a port's message type, tracing composite entities when undeclared."""
        if port.message_type:
            return port.message_type
        return self.resolution_service.resolve_port_type(owner, direction, port.name)

    @staticmethod
    def _get_connection_refs(connection) -> Optional[Tuple[str, str]]:
        """Extract the two endpoint references of a connection entry in YAML order."""
        if isinstance(connection, (list, tuple)) and len(connection) == 2:
            return str(connection[0]), str(connection[1])
        if isinstance(connection, dict) and len(connection) == 2:
            values = list(connection.values())
            return str(values[0]), str(values[1])
        return None

    @staticmethod
    def _first_reference_is_source(parsed: Connection, refs: Tuple[str, str], connection) -> bool:
        """Report whether the first YAML reference is the connection's source.

        Direction follows the port roles, so it is resolved by the designer's own rule.
        """
        first_instance, first_role, _ = ValidationEngine._split_reference(refs[0])
        _, second_role, _ = ValidationEngine._split_reference(refs[1])
        try:
            return Connection._determine_direction(parsed.type, first_instance, first_role, second_role, connection)
        except Exception:
            return True

    @staticmethod
    def _split_reference(ref: str) -> Tuple[str, str, str]:
        """Split ``[instance.]port_role.port_name`` into its three parts."""
        parts = ref.split(".")
        if len(parts) == 2:
            return "", parts[0], parts[1]
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        return "", "", ref

    def _connection_diagnostic(
        self,
        index: int,
        side: str,
        message: str,
        document_content: str = None,
        source_map: Optional[dict] = None,
        severity: lsp.DiagnosticSeverity = lsp.DiagnosticSeverity.Error,
    ) -> lsp.Diagnostic:
        endpoint_range = source_map_range(source_map, f"/connections/{index}/{0 if side == 'from' else 1}")
        if endpoint_range is None:
            endpoint_range = self._get_connection_range(index, document_content, side)
        return lsp.Diagnostic(range=endpoint_range, message=message, severity=severity)

    def _get_entity_inputs(self, config: Config, _seen: Optional[Set[str]] = None) -> List[PortDefinition]:
        return self.resolution_service.get_entity_inputs(config, _seen)

    def _get_entity_outputs(self, config: Config, _seen: Optional[Set[str]] = None) -> List[PortDefinition]:
        return self.resolution_service.get_entity_outputs(config, _seen)

    def _get_connection_range(
        self, connection_index: int, document_content: str = None, side: str = "from"
    ) -> lsp.Range:
        """Get the range for a specific side of a connection entry.

        Supports both YAML connection formats:
          - List:  ``- - source_ref`` / ``  - dest_ref``
          - Dict:  ``- from: source_ref`` / ``  to: dest_ref``

        Args:
            connection_index: 0-based index of the connection in the connections list.
            document_content: Raw YAML document text.
            side: ``"from"`` to highlight the source line, ``"to"`` for the destination line.
        """
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

                if not in_connections_section:
                    continue

                # List format: "  - - source_ref"
                if stripped.startswith("- -"):
                    if connections_found == connection_index:
                        target_line = line_num if side == "from" else line_num + 1
                        target_line = min(target_line, len(lines) - 1)
                        tl = lines[target_line]
                        return lsp.Range(
                            start=lsp.Position(line=target_line, character=0),
                            end=lsp.Position(line=target_line, character=len(tl)),
                        )
                    connections_found += 1

                # Dict format: "  - from: source_ref"
                elif stripped.startswith("- from:"):
                    if connections_found == connection_index:
                        target_line = line_num if side == "from" else line_num + 1
                        target_line = min(target_line, len(lines) - 1)
                        tl = lines[target_line]
                        return lsp.Range(
                            start=lsp.Position(line=target_line, character=0),
                            end=lsp.Position(line=target_line, character=len(tl)),
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

    def _is_valid_message_type(self, msg_type: str) -> bool:
        """Check if a message type looks valid (basic check)."""
        # Basic check for ROS 2 message type format
        return "/" in msg_type and msg_type.count("/") >= 1
