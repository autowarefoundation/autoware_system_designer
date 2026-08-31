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
from autoware_system_designer.common.source_location import SourceLocation
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
        namespace: List[str] = [],
        connection_type: ConnectionType = ConnectionType.UNDEFINED,
    ):
        self.msg_type = msg_type
        self.from_port = from_port
        self.to_port = to_port
        self.namespace = namespace
        self.connection_type = connection_type
        # early validation to avoid AttributeError later and provide clearer configuration error
        if self.from_port is None or self.to_port is None:
            # build contextual details safely
            from_name = getattr(self.from_port, "name", "<none>")
            to_name = getattr(self.to_port, "name", "<none>")
            raise ValidationError(
                "Invalid link configuration: one or more ports are None. "
                f"msg_type={self.msg_type}, from_port={from_name}, to_port={to_name}, connection_type={self.connection_type.name}. "
                "This usually indicates a typo or undefined port name in a connection definition."
            )

        self._check_connection()

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, "link", self.from_port.unique_id, self.to_port.unique_id)

    @property
    def topic(self):
        """Get the topic name for this link."""
        # Get topic from the from_port's reference port, as that's where topics are typically set
        from_port_ref = (
            self.from_port.get_reference_list()[0] if self.from_port.get_reference_list() else self.from_port
        )
        return from_port_ref.get_topic()

    def _check_connection(self):
        # if the from port is OutPort, it is internal port
        is_from_port_internal = isinstance(self.from_port, OutPort)
        # if the to port is InPort, it is internal port
        is_to_port_internal = isinstance(self.to_port, InPort)

        # case 1: from internal output to internal input
        if is_from_port_internal and is_to_port_internal:
            # propagate and finish the connection
            from_port_list = self.from_port.get_reference_list()
            to_port_list = self.to_port.get_reference_list()

            # if the to_port is not in the reference list (meaning it's a proxy/interface port),
            # add it to the list so it gets updated with topic/servers too.
            if self.to_port not in to_port_list:
                to_port_list.append(self.to_port)

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
            from_port_ref.set_users(to_port_list)
            for to_port_ref in to_port_list:
                to_port_ref.set_servers(from_port_list)

            # determine the topic, set it to the from-ports to publish and to-ports to subscribe
            if (from_port_ref.is_remapped or from_port_ref.is_global) and from_port_ref.topic:
                # Preset topic (remap takes priority over global): propagate to subscribers
                topic_parts = from_port_ref.topic
                for to_port_ref in to_port_list:
                    to_port_ref.set_topic(topic_parts[:-1], topic_parts[-1])
            else:
                from_port_ref.set_topic(self.from_port.namespace, self.from_port.name)
                for to_port_ref in to_port_list:
                    to_port_ref.set_topic(self.from_port.namespace, self.from_port.name)

            # set the trigger event of the to-port
            for to_port_ref in to_port_list:
                for server_port in to_port_ref.servers:
                    to_port_ref.event.add_trigger_event(server_port.event)

        # case 2: from internal output to external output
        elif is_from_port_internal and not is_to_port_internal:
            # bring the from-port reference to the to-port reference
            # Note: set_references() will validate that OutPort (to_port) has at most one reference
            reference_port_list = self.from_port.get_reference_list()
            self.to_port.set_references(reference_port_list)
            # set the topic name to the external output, whether it is connected or not
            for reference_port in reference_port_list:
                if (reference_port.is_remapped or reference_port.is_global) and reference_port.topic:
                    # Preset topic (remap or global): propagate to the external port
                    topic_parts = reference_port.topic
                    self.to_port.set_topic(topic_parts[:-1], topic_parts[-1])
                    if reference_port.is_remapped:
                        self.to_port.is_remapped = True
                    else:
                        self.to_port.is_global = True
                else:
                    reference_port.set_topic(self.to_port.namespace, self.to_port.name)

        # case 3: from external input to internal input
        elif not is_from_port_internal and is_to_port_internal:
            # bring the to-port reference to the from-port reference
            reference_port_list = self.to_port.get_reference_list()
            self.from_port.set_references(reference_port_list)

        # case 4: from-port is InPort and to-port is OutPort
        #   bypass connection, which is invalid
        else:
            raise ValidationError(
                "Invalid connection direction: InPort cannot be a source for OutPort. "
                f"Connection attempted: {getattr(self.from_port, 'name', '<unknown>')} -> {getattr(self.to_port, 'name', '<unknown>')}. "
                "Ensure 'from' refers to an output and 'to' refers to an input in the configuration YAML."
            )


class Connection:
    # Connection is a connection between two entities
    # In other words, it is a configuration to create link(s)
    def __init__(self, connection_dict: list | dict, source: Optional[SourceLocation] = None):

        self.source = source

        # connection type
        self.type: ConnectionType = ConnectionType.UNDEFINED

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
        self.type = self._determine_connection_type(
            port0_instance, port0_type, port1_instance, port1_type, connection_dict
        )
        port0_is_from = self._determine_direction(self.type, port0_instance, port0_type, port1_type, connection_dict)

        # Assign from/to based on determined direction
        from_port = (port0_instance, port0_name) if port0_is_from else (port1_instance, port1_name)
        to_port = (port0_instance, port0_name) if not port0_is_from else (port1_instance, port1_name)

        self.from_instance: str = from_port[0]
        self.from_port_name: str = from_port[1]
        self.from_is_external: bool = not from_port[0]
        self.to_instance: str = to_port[0]
        self.to_port_name: str = to_port[1]
        self.to_is_external: bool = not to_port[0]

    @staticmethod
    def _parse_port_name(port_name: str) -> tuple[str, str, str]:  # (instance_name, port_type, port_name)
        parts = port_name.split(".")
        if len(parts) == 2:
            return "", parts[0], parts[1]
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        raise DeploymentError(f"Invalid port name: {port_name}")

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
        raise DeploymentError(f"Invalid connection scope combination: {connection_dict}")

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
            raise DeploymentError(f"Invalid internal connection type: {connection_dict}")

        # External connections: port types must match
        if port0_type != port1_type:
            raise DeploymentError(f"Invalid external connection type: {connection_dict}")

        # For INTERNAL_TO_EXTERNAL: internal port (with instance) is output type and is the 'from' port
        if connection_type == ConnectionType.INTERNAL_TO_EXTERNAL:
            return has_port0_instance

        # For EXTERNAL_TO_INTERNAL: internal port (with instance) is input type, external port is 'from'
        if connection_type == ConnectionType.EXTERNAL_TO_INTERNAL:
            return not has_port0_instance

        raise DeploymentError(f"Invalid connection type for direction determination: {connection_dict}")
