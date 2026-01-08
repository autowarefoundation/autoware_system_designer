#!/usr/bin/env python3

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Any
from urllib.parse import urlparse, unquote

from pygls.server import LanguageServer
from pygls.protocol import default_converter
from lsprotocol import types as lsp

# Import from the autoware_system_designer package
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'autoware_system_designer'))

from autoware_system_designer.parsers.data_parser import ConfigParser
from autoware_system_designer.models.config import Config, ConfigType
from autoware_system_designer.exceptions import ValidationError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AutowareSystemDesignerLanguageServer:
    def __init__(self):
        self.server = LanguageServer("autoware-system-designer", "0.1.0")
        self.config_parser = ConfigParser()
        self.entity_registry: Dict[str, Config] = {}
        self.file_registry: Dict[str, Config] = {}

        # Register handlers
        @self.server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
        def did_open(ls, params):
            self._on_text_document_did_open(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
        def did_change(ls, params):
            self._on_text_document_did_change(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
        def did_close(ls, params):
            self._on_text_document_did_close(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_COMPLETION)
        def completion(ls, params):
            return self._on_completion(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
        def definition(ls, params):
            return self._on_definition(ls, params)

        @self.server.feature(lsp.TEXT_DOCUMENT_HOVER)
        def hover(ls, params):
            return self._on_hover(ls, params)

        @self.server.feature(lsp.INITIALIZE)
        def initialize(ls, params):
            return self._on_initialize(ls, params)

    def start(self):
        """Start the language server."""
        self.server.start_io()

    def _on_initialize(self, ls, params: lsp.InitializeParams) -> lsp.InitializeResult:
        """Handle server initialization."""
        logger.info("Initializing Autoware System Designer Language Server")

        # Scan workspace for entity files
        if params.workspace_folders:
            for folder in params.workspace_folders:
                self._scan_workspace(folder.uri)

        capabilities = lsp.ServerCapabilities(
            text_document_sync=lsp.TextDocumentSyncKind.Full,
            completion_provider=lsp.CompletionOptions(
                trigger_characters=['.', ':']
            ),
            definition_provider=True,
            hover_provider=True
        )

        return lsp.InitializeResult(capabilities=capabilities)

    def _scan_workspace(self, workspace_uri: str):
        """Scan workspace for entity files and build registry."""
        workspace_path = self._uri_to_path(workspace_uri)

        # Find all entity files
        patterns = [
            '**/*.node.yaml',
            '**/*.module.yaml',
            '**/*.system.yaml',
            '**/*.parameter_set.yaml'
        ]

        for pattern in patterns:
            for file_path in Path(workspace_path).glob(pattern):
                try:
                    config = self.config_parser.parse_entity_file(str(file_path))
                    self._register_entity(config)
                except Exception as e:
                    logger.warning(f"Failed to parse {file_path}: {e}")

    def _register_entity(self, config: Config):
        """Register an entity in the registry."""
        self.entity_registry[config.full_name] = config
        self.file_registry[str(config.file_path)] = config
        logger.info(f"Registered entity: {config.full_name} from {config.file_path}")

    def _unregister_entity(self, file_path: str):
        """Unregister an entity from the registry."""
        if file_path in self.file_registry:
            config = self.file_registry[file_path]
            del self.entity_registry[config.full_name]
            del self.file_registry[file_path]
            logger.info(f"Unregistered entity: {config.full_name}")

    def _on_text_document_did_open(self, ls, params: lsp.DidOpenTextDocumentParams):
        """Handle document open event."""
        self._process_document(params.text_document.uri, params.text_document.text)

    def _on_text_document_did_change(self, ls, params: lsp.DidChangeTextDocumentParams):
        """Handle document change event."""
        # For simplicity, we reprocess the entire document on change
        # In a production implementation, you'd want incremental updates
        if params.content_changes:
            content = params.content_changes[0].text
            self._process_document(params.text_document.uri, content)

    def _on_text_document_did_close(self, ls, params: lsp.DidCloseTextDocumentParams):
        """Handle document close event."""
        file_path = self._uri_to_path(params.text_document.uri)
        self._unregister_entity(file_path)

    def _process_document(self, uri: str, content: str):
        """Process a document and update registries."""
        file_path = self._uri_to_path(uri)

        # Try to parse the document
        try:
            # Write content to temporary file for parsing
            temp_path = Path(file_path)
            if temp_path.exists():
                # Unregister existing entity if file exists
                self._unregister_entity(file_path)

            # Parse the content
            config = self.config_parser.parse_entity_file(file_path)
            self._register_entity(config)

            # Send diagnostics
            diagnostics = self._validate_connections(config)
            self.server.text_document_publish_diagnostics(
                lsp.PublishDiagnosticsParams(
                    uri=uri,
                    diagnostics=diagnostics
                )
            )

        except ValidationError as e:
            # Send validation error diagnostics
            diagnostics = [
                lsp.Diagnostic(
                    range=lsp.Range(
                        start=lsp.Position(line=0, character=0),
                        end=lsp.Position(line=0, character=1)
                    ),
                    message=str(e),
                    severity=lsp.DiagnosticSeverity.Error
                )
            ]
            self.server.text_document_publish_diagnostics(
                lsp.PublishDiagnosticsParams(
                    uri=uri,
                    diagnostics=diagnostics
                )
            )
        except Exception as e:
            logger.warning(f"Failed to process document {uri}: {e}")

    def _validate_connections(self, config: Config) -> List[lsp.Diagnostic]:
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
                        range=self._get_connection_range(i, 'from'),
                        message=f"Invalid connection source: {from_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate to reference
                to_valid, to_message = self._validate_connection_reference(to_ref, config)
                if not to_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'to'),
                        message=f"Invalid connection destination: {to_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate message type compatibility if both are valid
                if from_valid and to_valid and from_ref and to_ref:
                    compatibility_issue = self._check_message_type_compatibility(from_ref, to_ref, config)
                    if compatibility_issue:
                        diagnostics.append(lsp.Diagnostic(
                            range=self._get_connection_range(i, 'to'),
                            message=f"Message type mismatch: {compatibility_issue}",
                            severity=lsp.DiagnosticSeverity.Warning
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
                        range=self._get_connection_range(i, 'from'),
                        message=f"Invalid connection source: {from_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate to reference
                to_valid, to_message = self._validate_connection_reference(to_ref, config)
                if not to_valid:
                    diagnostics.append(lsp.Diagnostic(
                        range=self._get_connection_range(i, 'to'),
                        message=f"Invalid connection destination: {to_message}",
                        severity=lsp.DiagnosticSeverity.Error
                    ))

                # Validate message type compatibility if both are valid
                if from_valid and to_valid and from_ref and to_ref:
                    compatibility_issue = self._check_message_type_compatibility(from_ref, to_ref, config)
                    if compatibility_issue:
                        diagnostics.append(lsp.Diagnostic(
                            range=self._get_connection_range(i, 'to'),
                            message=f"Message type mismatch: {compatibility_issue}",
                            severity=lsp.DiagnosticSeverity.Warning
                        ))

        return diagnostics

    def _validate_connection_reference(self, ref: str, config: Config) -> tuple[bool, str]:
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

            elif parts[0].startswith('node_'):
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
                        if entity_name not in self.entity_registry:
                            return False, f"Entity '{entity_name}' not found in registry"

                        entity_config = self.entity_registry[entity_name]
                        if port_type == 'input':
                            if not entity_config.inputs:
                                return False, f"Entity '{entity_name}' has no input ports"
                            input_names = [port.get('name') for port in entity_config.inputs if port.get('name')]
                            if port_name not in input_names:
                                return False, f"Input port '{port_name}' not found in entity '{entity_name}'. Available inputs: {', '.join(input_names)}"
                        elif port_type == 'output':
                            if not entity_config.outputs:
                                return False, f"Entity '{entity_name}' has no output ports"
                            output_names = [port.get('name') for port in entity_config.outputs if port.get('name')]
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
                    if component_entity not in self.entity_registry:
                        return False, f"Entity '{component_entity}' not found in registry"

                    entity_config = self.entity_registry[component_entity]
                    if port_type == 'input':
                        if not entity_config.inputs:
                            return False, f"Entity '{component_entity}' has no input ports"
                        input_names = [port.get('name') for port in entity_config.inputs if port.get('name')]
                        if port_name not in input_names:
                            return False, f"Input port '{port_name}' not found in component '{component_name}'. Available inputs: {', '.join(input_names)}"
                    elif port_type == 'output':
                        if not entity_config.outputs:
                            return False, f"Entity '{component_entity}' has no output ports"
                        output_names = [port.get('name') for port in entity_config.outputs if port.get('name')]
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

        if config.entity_type == ConfigType.MODULE:
            if parts[0] == 'input':
                # External input interface - no type info available
                return None
            elif parts[0] == 'output':
                # External output interface - no type info available
                return None
            elif parts[0].startswith('node_'):
                instance_name = parts[0]
                port_type = parts[1]
                port_name = parts[2]

                instances = config.instances or []
                for instance in instances:
                    if instance.get('instance') == instance_name:
                        entity_name = instance.get('entity')
                        if entity_name in self.entity_registry:
                            entity_config = self.entity_registry[entity_name]
                            if port_type == 'input' and entity_config.inputs:
                                for port in entity_config.inputs:
                                    if port.get('name') == port_name:
                                        return port.get('message_type')
                            elif port_type == 'output' and entity_config.outputs:
                                for port in entity_config.outputs:
                                    if port.get('name') == port_name:
                                        return port.get('message_type')

        elif config.entity_type == ConfigType.SYSTEM:
            component_name = parts[0]
            port_type = parts[1]
            port_name = parts[2]

            components = config.components or []
            for component in components:
                if component.get('name') == component_name:
                    component_entity = component.get('entity')
                    if component_entity in self.entity_registry:
                        entity_config = self.entity_registry[component_entity]
                        if port_type == 'input' and entity_config.inputs:
                            for port in entity_config.inputs:
                                if port.get('name') == port_name:
                                    return port.get('message_type')
                        elif port_type == 'output' and entity_config.outputs:
                            for port in entity_config.outputs:
                                if port.get('name') == port_name:
                                    return port.get('message_type')

        return None

    def _get_connection_range(self, connection_index: int, field: str) -> lsp.Range:
        """Get the range for a connection field (approximate)."""
        # This is a simplified implementation
        # In a real implementation, you'd parse the YAML and get exact positions
        line = 20 + connection_index * 5  # Approximate line number
        return lsp.Range(
            start=lsp.Position(line=line, character=0),
            end=lsp.Position(line=line, character=50)
        )

    def _on_completion(self, ls, params: lsp.CompletionParams) -> lsp.CompletionList:
        """Handle completion requests."""
        items = []

        # Get the document
        document = self.server.workspace.get_document(params.text_document.uri)
        if not document:
            return lsp.CompletionList(is_incomplete=False, items=[])

        # Get current line and some context
        line = document.lines[params.position.line]
        prefix = line[:params.position.character]

        # Get the file path to determine the entity type
        file_path = self._uri_to_path(params.text_document.uri)
        current_config = self.file_registry.get(file_path)

        if not current_config:
            return lsp.CompletionList(is_incomplete=False, items=[])

        # Determine completion context based on the current line and position
        completion_context = self._analyze_completion_context(line, params.position.character, current_config)

        if completion_context == 'entity':
            # Entity name completion
            for entity_name, config in self.entity_registry.items():
                if config.entity_type in ['node', 'module', 'parameter_set']:
                    items.append(lsp.CompletionItem(
                        label=entity_name,
                        kind=lsp.CompletionItemKind.Class,
                        detail=f"{config.entity_type.title()}: {config.file_path.name}",
                        documentation=f"Entity type: {config.entity_type}\nLocation: {config.file_path}"
                    ))

        elif completion_context == 'connection_from' or completion_context == 'connection_to':
            # Connection reference completion
            items.extend(self._get_connection_completion_items(current_config, completion_context))

        elif completion_context == 'message_type':
            # Message type completion (common ROS 2 message types)
            common_message_types = [
                'sensor_msgs/msg/PointCloud2',
                'sensor_msgs/msg/Image',
                'sensor_msgs/msg/LaserScan',
                'autoware_perception_msgs/msg/DetectedObjects',
                'geometry_msgs/msg/PoseStamped',
                'nav_msgs/msg/Odometry',
                'std_msgs/msg/String',
                'std_msgs/msg/Bool',
                'std_msgs/msg/Int32',
                'std_msgs/msg/Float64'
            ]
            for msg_type in common_message_types:
                items.append(lsp.CompletionItem(
                    label=msg_type,
                    kind=lsp.CompletionItemKind.TypeParameter,
                    detail="ROS 2 Message Type"
                ))

        elif completion_context == 'parameter_name':
            # Parameter name completion based on common patterns
            common_params = [
                'build_only',
                'enable_debug',
                'timeout',
                'max_retries',
                'update_rate',
                'frame_id'
            ]
            for param in common_params:
                items.append(lsp.CompletionItem(
                    label=param,
                    kind=lsp.CompletionItemKind.Variable,
                    detail="Common parameter name"
                ))

        return lsp.CompletionList(is_incomplete=False, items=items)

    def _analyze_completion_context(self, line: str, character: int, config: Config) -> str:
        """Analyze the completion context based on the current line."""
        line = line.strip()

        # Check for entity references
        if 'entity:' in line:
            return 'entity'

        # Check for message type fields
        if 'message_type:' in line:
            return 'message_type'

        # Check for parameter names
        if 'name:' in line and ('parameters:' in '\n'.join(self._get_context_lines(line))):
            return 'parameter_name'

        # Check for connection references
        if 'from:' in line or 'to:' in line:
            # Determine if it's a from or to reference
            if 'from:' in line:
                return 'connection_from'
            elif 'to:' in line:
                return 'connection_to'

        return 'unknown'

    def _get_context_lines(self, current_line: str) -> List[str]:
        """Get context lines around the current line (simplified)."""
        # In a real implementation, you'd get the actual document lines
        # For now, just return the current line
        return [current_line]

    def _get_connection_completion_items(self, config: Config, context: str) -> List[lsp.CompletionItem]:
        """Get completion items for connection references."""
        items = []

        if config.entity_type == ConfigType.MODULE:
            # Module connections
            external_interfaces = config.external_interfaces or {}

            # Input interfaces
            inputs = external_interfaces.get('input', [])
            for interface in inputs:
                name = interface.get('name')
                if name:
                    items.append(lsp.CompletionItem(
                        label=f"input.{name}",
                        kind=lsp.CompletionItemKind.Field,
                        detail="External input interface",
                        documentation=f"Input interface: {name}"
                    ))

            # Output interfaces
            outputs = external_interfaces.get('output', [])
            for interface in outputs:
                name = interface.get('name')
                if name:
                    items.append(lsp.CompletionItem(
                        label=f"output.{name}",
                        kind=lsp.CompletionItemKind.Field,
                        detail="External output interface",
                        documentation=f"Output interface: {name}"
                    ))

            # Instance ports
            instances = config.instances or []
            for instance in instances:
                instance_name = instance.get('instance')
                entity_name = instance.get('entity')

                if instance_name and entity_name and entity_name in self.entity_registry:
                    entity_config = self.entity_registry[entity_name]

                    # Input ports
                    if entity_config.inputs:
                        for port in entity_config.inputs:
                            port_name = port.get('name')
                            msg_type = port.get('message_type', 'unknown')
                            if port_name:
                                items.append(lsp.CompletionItem(
                                    label=f"{instance_name}.input.{port_name}",
                                    kind=lsp.CompletionItemKind.Field,
                                    detail=f"Input port: {msg_type}",
                                    documentation=f"Instance: {instance_name}\nEntity: {entity_name}\nPort: {port_name}\nType: {msg_type}"
                                ))

                    # Output ports
                    if entity_config.outputs:
                        for port in entity_config.outputs:
                            port_name = port.get('name')
                            msg_type = port.get('message_type', 'unknown')
                            if port_name:
                                items.append(lsp.CompletionItem(
                                    label=f"{instance_name}.output.{port_name}",
                                    kind=lsp.CompletionItemKind.Field,
                                    detail=f"Output port: {msg_type}",
                                    documentation=f"Instance: {instance_name}\nEntity: {entity_name}\nPort: {port_name}\nType: {msg_type}"
                                ))

        elif config.entity_type == ConfigType.SYSTEM:
            # System connections
            components = config.components or []
            for component in components:
                component_name = component.get('name')
                component_entity = component.get('entity')

                if component_name and component_entity and component_entity in self.entity_registry:
                    entity_config = self.entity_registry[component_entity]

                    # Input ports
                    if entity_config.inputs:
                        for port in entity_config.inputs:
                            port_name = port.get('name')
                            msg_type = port.get('message_type', 'unknown')
                            if port_name:
                                items.append(lsp.CompletionItem(
                                    label=f"{component_name}.input.{port_name}",
                                    kind=lsp.CompletionItemKind.Field,
                                    detail=f"Input port: {msg_type}",
                                    documentation=f"Component: {component_name}\nEntity: {component_entity}\nPort: {port_name}\nType: {msg_type}"
                                ))

                    # Output ports
                    if entity_config.outputs:
                        for port in entity_config.outputs:
                            port_name = port.get('name')
                            msg_type = port.get('message_type', 'unknown')
                            if port_name:
                                items.append(lsp.CompletionItem(
                                    label=f"{component_name}.output.{port_name}",
                                    kind=lsp.CompletionItemKind.Field,
                                    detail=f"Output port: {msg_type}",
                                    documentation=f"Component: {component_name}\nEntity: {component_entity}\nPort: {port_name}\nType: {msg_type}"
                                ))

        return items

    def _on_definition(self, ls, params: lsp.DefinitionParams) -> Optional[lsp.Location]:
        """Handle go-to-definition requests."""
        document = self.server.workspace.get_document(params.text_document.uri)
        if not document:
            return None

        # Get current word
        line = document.lines[params.position.line]
        word = self._get_word_at_position(line, params.position.character)

        # Check if it's an entity name
        if word in self.entity_registry:
            config = self.entity_registry[word]
            return lsp.Location(
                uri=self._path_to_uri(str(config.file_path)),
                range=lsp.Range(
                    start=lsp.Position(line=1, character=0),  # Skip the format header
                    end=lsp.Position(line=2, character=0)
                )
            )

        # Check if it's a connection reference that points to another entity
        current_file_path = self._uri_to_path(params.text_document.uri)
        current_config = self.file_registry.get(current_file_path)

        if current_config:
            location = self._find_definition_in_connection(word, current_config)
            if location:
                return location

        return None

    def _find_definition_in_connection(self, word: str, config: Config) -> Optional[lsp.Location]:
        """Find definition for connection references."""
        # Parse the word as a potential connection reference
        parts = word.split('.')

        if len(parts) >= 3:
            if config.entity_type == ConfigType.MODULE:
                # Handle module connections: instance.port_type.port_name
                if len(parts) >= 3 and parts[1] in ['input', 'output']:
                    instance_name = parts[0]
                    port_type = parts[1]
                    port_name = parts[2]

                    # Find the instance
                    instances = config.instances or []
                    for instance in instances:
                        if instance.get('instance') == instance_name:
                            entity_name = instance.get('entity')
                            if entity_name in self.entity_registry:
                                entity_config = self.entity_registry[entity_name]
                                # Return location in the entity file at the port definition
                                return lsp.Location(
                                    uri=self._path_to_uri(str(entity_config.file_path)),
                                    range=lsp.Range(
                                        start=lsp.Position(line=10, character=0),  # Approximate location
                                        end=lsp.Position(line=11, character=0)
                                    )
                                )

            elif config.entity_type == ConfigType.SYSTEM:
                # Handle system connections: component.port_type.port_name
                if len(parts) >= 3 and parts[1] in ['input', 'output']:
                    component_name = parts[0]
                    port_type = parts[1]
                    port_name = parts[2]

                    # Find the component
                    components = config.components or []
                    for component in components:
                        if component.get('name') == component_name:
                            component_entity = component.get('entity')
                            if component_entity in self.entity_registry:
                                entity_config = self.entity_registry[component_entity]
                                # Return location in the entity file at the port definition
                                return lsp.Location(
                                    uri=self._path_to_uri(str(entity_config.file_path)),
                                    range=lsp.Range(
                                        start=lsp.Position(line=10, character=0),  # Approximate location
                                        end=lsp.Position(line=11, character=0)
                                    )
                                )

        return None

    def _on_hover(self, ls, params: lsp.HoverParams) -> Optional[lsp.Hover]:
        """Handle hover requests."""
        document = self.server.workspace.get_document(params.text_document.uri)
        if not document:
            return None

        # Get current word
        line = document.lines[params.position.line]
        word = self._get_word_at_position(line, params.position.character)

        # Check if it's an entity name
        if word in self.entity_registry:
            config = self.entity_registry[word]
            return self._create_entity_hover(config)

        # Check if it's a connection reference
        current_file_path = self._uri_to_path(params.text_document.uri)
        current_config = self.file_registry.get(current_file_path)

        if current_config:
            hover = self._create_connection_hover(word, current_config)
            if hover:
                return hover

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
                    comp_name = component.get('name', 'unknown')
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

    def _create_connection_hover(self, word: str, config: Config) -> Optional[lsp.Hover]:
        """Create hover information for connection references."""
        parts = word.split('.')

        if config.entity_type == ConfigType.MODULE and len(parts) >= 3:
            instance_name = parts[0]
            port_type = parts[1]
            port_name = parts[2]

            # Find the instance and get port information
            instances = config.instances or []
            for instance in instances:
                if instance.get('instance') == instance_name:
                    entity_name = instance.get('entity')
                    if entity_name in self.entity_registry:
                        entity_config = self.entity_registry[entity_name]

                        hover_text = f"**{word}**\n\n"
                        hover_text += f"**Instance:** {instance_name}\n"
                        hover_text += f"**Entity:** {entity_name}\n"
                        hover_text += f"**Port Type:** {port_type}\n"

                        # Get port details
                        ports = entity_config.inputs if port_type == 'input' else entity_config.outputs
                        if ports:
                            for port in ports:
                                if port.get('name') == port_name:
                                    msg_type = port.get('message_type', 'unknown')
                                    hover_text += f"**Message Type:** {msg_type}\n"

                                    qos = port.get('qos')
                                    if qos:
                                        hover_text += f"**QoS:** {qos}\n"
                                    break

                        return lsp.Hover(
                            contents=lsp.MarkupContent(
                                kind=lsp.MarkupKind.Markdown,
                                value=hover_text
                            )
                        )

        elif config.entity_type == ConfigType.SYSTEM and len(parts) >= 3:
            component_name = parts[0]
            port_type = parts[1]
            port_name = parts[2]

            # Find the component and get port information
            components = config.components or []
            for component in components:
                if component.get('name') == component_name:
                    component_entity = component.get('entity')
                    if component_entity in self.entity_registry:
                        entity_config = self.entity_registry[component_entity]

                        hover_text = f"**{word}**\n\n"
                        hover_text += f"**Component:** {component_name}\n"
                        hover_text += f"**Entity:** {component_entity}\n"
                        hover_text += f"**Port Type:** {port_type}\n"

                        # Get port details
                        ports = entity_config.inputs if port_type == 'input' else entity_config.outputs
                        if ports:
                            for port in ports:
                                if port.get('name') == port_name:
                                    msg_type = port.get('message_type', 'unknown')
                                    hover_text += f"**Message Type:** {msg_type}\n"

                                    qos = port.get('qos')
                                    if qos:
                                        hover_text += f"**QoS:** {qos}\n"
                                    break

                        return lsp.Hover(
                            contents=lsp.MarkupContent(
                                kind=lsp.MarkupKind.Markdown,
                                value=hover_text
                            )
                        )

        return None

    def _get_word_at_position(self, line: str, character: int) -> str:
        """Get the word at the given character position."""
        # Find word boundaries
        start = character
        while start > 0 and (line[start-1].isalnum() or line[start-1] in '._'):
            start -= 1

        end = character
        while end < len(line) and (line[end].isalnum() or line[end] in '._'):
            end += 1

        return line[start:end]

    def _uri_to_path(self, uri: str) -> str:
        """Convert URI to file path."""
        parsed = urlparse(uri)
        return unquote(parsed.path)

    def _path_to_uri(self, path: str) -> str:
        """Convert file path to URI."""
        return f"file://{path}"


if __name__ == '__main__':
    server = AutowareSystemDesignerLanguageServer()
    server.start()
