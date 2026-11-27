# Copyright 2026 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, List, Optional

from autoware_system_designer.common.exceptions import DeploymentError, ValidationError
from autoware_system_designer.common.naming import generate_unique_id
from autoware_system_designer.common.source_location import SourceLocation, format_source
from autoware_system_designer.model.ports import InPort, OutPort, Port


class ConnectionType(int, Enum):
    UNDEFINED = 0
    EXTERNAL_TO_INTERNAL = 1
    INTERNAL_TO_INTERNAL = 2
    INTERNAL_TO_EXTERNAL = 3


@dataclass(init=False, eq=False, repr=False)
class Link:
    # Link is a connection between two ports
    msg_type: Optional[str] = None
    # from-port and to-port connection; exported boundary-relative by the serializer
    from_port: Optional[Port] = field(default=None, metadata={"exclude": True})
    to_port: Optional[Port] = field(default=None, metadata={"exclude": True})
    namespace: List[str] = field(default_factory=list, metadata={"exclude": True})
    connection_type: ConnectionType = ConnectionType.UNDEFINED

    __serde_computed__: ClassVar[tuple] = (("unique_id", "unique_id"), ("topic", "topic"))

    def __init__(
        self,
        msg_type: str,
        from_port: Port,
        to_port: Port,
        namespace: Optional[List[str]] = None,
        connection_type: ConnectionType = ConnectionType.UNDEFINED,
    ):
        self.msg_type = msg_type
        self.from_port = from_port
        self.to_port = to_port
        self.namespace = [] if namespace is None else namespace
        self.connection_type = connection_type
        self._check_connection()

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, "link", self.from_port.unique_id, self.to_port.unique_id)

    @property
    def topic(self):
        """Topic of the node port the from-port stands for."""
        return self.from_port.get_reference_list()[0].get_topic()

    def _check_connection(self):
        # if the from port is OutPort, it is internal port
        is_from_port_internal = isinstance(self.from_port, OutPort)
        # if the to port is InPort, it is internal port
        is_to_port_internal = isinstance(self.to_port, InPort)

        # case 1: from internal output to internal input
        if is_from_port_internal and is_to_port_internal:
            # propagate and finish the connection
            from_port_list = self.from_port.get_reference_list()
            to_port_ref_list = self.to_port.get_reference_list()

            # Module interface ports are proxies: every port down the chain carries servers
            # and users like the node ports it stands for, but holds no event of the trigger
            # chain. Topics resolve from the publishing node port.
            to_port_list = list(to_port_ref_list)
            to_port_list.extend(port for port in self.to_port.get_chain_list() if port not in to_port_list)
            from_port_chain = self.from_port.get_chain_list()

            # check the message type is the same
            from_port_ref = from_port_list[0]
            if from_port_ref.msg_type != self.msg_type:
                raise ValidationError(
                    (
                        "Message type mismatch on source port:\n"
                        f"  Link expects : {self.msg_type}\n"
                        f"  Port provides: {from_port_ref.msg_type}\n"
                        f"  Connection  : {from_port_ref.name} -> {self.to_port.name}\n"
                        "Action        : Check the 'message_type' of the output port definition."
                    )
                )
            for to_port in to_port_list:
                if to_port.msg_type != self.msg_type:
                    raise ValidationError(
                        (
                            "Message type mismatch on target port:\n"
                            f"  Source expects: {self.msg_type}\n"
                            f"  Target provides: {to_port.msg_type}\n"
                            f"  Connection     : {self.from_port.name} -> {to_port.name}\n"
                            "Action          : Align the 'message_type' of the input port with the source output."
                        )
                    )

            # link the ports
            for from_port in from_port_chain:
                from_port.set_users(to_port_list)
            for to_port_ref in to_port_list:
                to_port_ref.set_servers(from_port_list)

            # set the trigger event of the node to-ports
            for to_port_ref in to_port_ref_list:
                for server_port in to_port_ref.servers:
                    to_port_ref.event.add_trigger_event(server_port.event)

        # case 2: from internal output to external output
        elif is_from_port_internal and not is_to_port_internal:
            # The external port references its direct child; nested module ports chain down to the node port.
            self.to_port.set_references([self.from_port])
            # The module output names the published topic in the module layer, whether it is
            # connected or not; a preset topic (remap or global) is kept.
            node_port = self.from_port.get_reference_list()[0]
            preset_port = next(
                (port for port in (self.from_port, node_port) if (port.is_remapped or port.is_global) and port.topic),
                None,
            )
            if preset_port is not None:
                if preset_port.is_remapped:
                    self.to_port.is_remapped = True
                else:
                    self.to_port.is_global = True
            else:
                node_port.set_topic(self.to_port.namespace, self.to_port.name)

        # case 3: from external input to internal input; a connection never joins two boundary ports
        else:
            # The external port references its direct child; nested module ports chain down to the node ports.
            self.from_port.set_references([self.to_port])
            # A port attached after the external port is connected subscribes to the same publishers.
            if self.from_port.servers:
                for to_port in self.to_port.get_chain_list():
                    to_port.set_servers(self.from_port.servers)
                for server_port in self.from_port.servers:
                    server_port.set_users(self.to_port.get_chain_list())


class Connection:
    # Connection is a connection between two entities
    # In other words, it is a configuration to create link(s)
    def __init__(self, connection_dict: list | dict, source: Optional[SourceLocation] = None):
        self.source = source
        try:
            self._parse(connection_dict)
        except DeploymentError as exc:
            raise DeploymentError(f"{exc.message}{format_source(source)}", source=source) from exc

    def _parse(self, connection_dict: list | dict) -> None:
        # Handle dictionary format: extract values regardless of keys
        if isinstance(connection_dict, dict):
            values = list(connection_dict.values())
            if len(values) != 2:
                raise DeploymentError(f"Connection dictionary must have exactly 2 values: {connection_dict}")
            port0_str = values[0]
            port1_str = values[1]
        # Handle list format: [port1, port2]
        elif isinstance(connection_dict, list):
            if len(connection_dict) != 2:
                raise DeploymentError(f"Connection must be an array of size 2 : {connection_dict}")
            port0_str = connection_dict[0]
            port1_str = connection_dict[1]
        else:
            raise DeploymentError(
                f"Connection must be either a list of size 2 or a dictionary with exactly 2 values: {connection_dict}"
            )

        # Parse both ports
        port0_instance, port0_type, port0_name = self._parse_port_name(port0_str)
        port1_instance, port1_type, port1_name = self._parse_port_name(port1_str)

        # Determine connection type and direction
        self.type: ConnectionType = self._determine_connection_type(
            port0_instance, port0_type, port1_instance, port1_type, connection_dict
        )
        port0_is_from = self._determine_direction(self.type, port0_instance, port0_type, port1_type, connection_dict)

        # Assign from/to based on determined direction
        from_port = (port0_instance, port0_name) if port0_is_from else (port1_instance, port1_name)
        to_port = (port0_instance, port0_name) if not port0_is_from else (port1_instance, port1_name)

        self.from_instance: str = from_port[0]
        self.from_port_name: str = from_port[1]
        self.from_is_external: bool = not from_port[0]
        self.from_port_type: str = port0_type if port0_is_from else port1_type
        self.to_instance: str = to_port[0]
        self.to_port_name: str = to_port[1]
        self.to_is_external: bool = not to_port[0]
        self.to_port_type: str = port1_type if port0_is_from else port0_type
        self.has_wildcard: bool = any(
            char in text
            for text in (self.from_instance, self.from_port_name, self.to_instance, self.to_port_name)
            for char in self.WILDCARDS
        )

    PORT_TYPES: ClassVar[tuple] = ("publisher", "subscriber", "server", "client")
    WILDCARDS: ClassVar[str] = "*^+"

    @staticmethod
    def _parse_port_name(port_name: str) -> tuple[str, str, str]:  # (instance_name, port_type, port_name)
        parts = port_name.split(".")
        if len(parts) == 2:
            instance, port_type, name = "", parts[0], parts[1]
        elif len(parts) == 3:
            instance, port_type, name = parts[0], parts[1], parts[2]
        else:
            raise DeploymentError(f"Invalid port name: {port_name}")
        if port_type not in Connection.PORT_TYPES:
            raise DeploymentError(
                f"[E_CONN_PORT_KIND] Invalid port kind '{port_type}' in '{port_name}' "
                f"(expected one of {', '.join(Connection.PORT_TYPES)})"
            )
        return instance, port_type, name

    @staticmethod
    def _is_output_port_type(port_type: str) -> bool:
        """Check if port type is an output type (publisher or server)."""
        return port_type in ["publisher", "server"]

    @staticmethod
    def _determine_connection_type(
        port0_instance: str,
        port0_type: str,
        port1_instance: str,
        port1_type: str,
        connection_dict: list | dict,
    ) -> ConnectionType:
        """Determine the connection type based on instance presence and port types."""
        has_port0_instance = bool(port0_instance)
        has_port1_instance = bool(port1_instance)

        # Both ports have instances: internal-to-internal connection
        if has_port0_instance and has_port1_instance:
            return ConnectionType.INTERNAL_TO_INTERNAL

        # Only one port has an instance: external connection
        if has_port0_instance:
            # Port0 is internal, check if it's output (publisher/server) or input
            if Connection._is_output_port_type(port0_type):
                return ConnectionType.INTERNAL_TO_EXTERNAL
            else:
                return ConnectionType.EXTERNAL_TO_INTERNAL

        if has_port1_instance:
            # Port1 is internal, check if it's output (publisher/server) or input
            if Connection._is_output_port_type(port1_type):
                return ConnectionType.INTERNAL_TO_EXTERNAL
            else:
                return ConnectionType.EXTERNAL_TO_INTERNAL

        # Neither port has an instance: invalid
        raise DeploymentError(f"[E_CONN_SCOPE] A connection cannot join two boundary ports: {connection_dict}")

    @staticmethod
    def _determine_direction(
        connection_type: ConnectionType,
        port0_instance: str,
        port0_type: str,
        port1_type: str,
        connection_dict: list | dict,
    ) -> bool:
        """Determine if port0 is the 'from' port. Returns True if port0 is from, False if port1 is from."""
        has_port0_instance = bool(port0_instance)

        # Internal-to-internal: direction determined by port type pairs
        if connection_type == ConnectionType.INTERNAL_TO_INTERNAL:
            # Valid pairs: (publisher, subscriber) or (server, client) -> port0 is from
            if (port0_type, port1_type) in [("publisher", "subscriber"), ("server", "client")]:
                return True
            # Valid pairs: (subscriber, publisher) or (client, server) -> port1 is from
            if (port0_type, port1_type) in [("subscriber", "publisher"), ("client", "server")]:
                return False
            raise DeploymentError(
                "[E_CONN_KIND] A publisher connects to a subscriber and a server to a client: " f"{connection_dict}"
            )

        # External connections: port types must match
        if port0_type != port1_type:
            raise DeploymentError(
                f"[E_CONN_KIND] A boundary port connects to a child port of its own kind: {connection_dict}"
            )

        # INTERNAL_TO_EXTERNAL: the child port is the 'from' port; EXTERNAL_TO_INTERNAL: the boundary port is.
        return has_port0_instance == (connection_type == ConnectionType.INTERNAL_TO_EXTERNAL)
