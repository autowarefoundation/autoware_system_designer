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

from enum import Enum
from typing import List, Optional

from ...exceptions import DeploymentError, ValidationError
from ...file_io.source_location import SourceLocation
from ...utils.naming import generate_unique_id
from .ports import InPort, OutPort, Port


class ConnectionType(int, Enum):
    UNDEFINED = 0
    EXTERNAL_TO_INTERNAL = 1
    INTERNAL_TO_INTERNAL = 2
    INTERNAL_TO_EXTERNAL = 3


class Link:
    # Link is a connection between two ports
    def __init__(
        self,
        msg_type: str,
        from_port: Port,
        to_port: Port,
        namespace: List[str] = [],
        connection_type: ConnectionType = ConnectionType.UNDEFINED,
    ):
        self.msg_type: str = msg_type
        # from-port and to-port connection
        self.from_port: Port = from_port
        self.to_port: Port = to_port
        # namespace
        self.namespace: List[str] = namespace
        # connection type
        self.connection_type: ConnectionType = connection_type
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
    def __init__(self, connection_dict: list, source: Optional[SourceLocation] = None):

        self.source = source

        # connection type
        self.type: ConnectionType = ConnectionType.UNDEFINED

        if type(connection_dict) is not list:
            raise DeploymentError(f"Connection must be an array of size 2 : {connection_dict}")
        if len(connection_dict) != 2:
            raise DeploymentError(f"Connection must be an array of size 2 : {connection_dict}")

        # Parse both ports
        port0_instance, port0_type, port0_name = self._parse_port_name(connection_dict[0])
        port1_instance, port1_type, port1_name = self._parse_port_name(connection_dict[1])

        # Determine connection type based on from/to instance presence
        if port0_instance and port1_instance:
            self.type = ConnectionType.INTERNAL_TO_INTERNAL
        elif port0_instance:
            self.type = ConnectionType.INTERNAL_TO_EXTERNAL
        elif port1_instance:
            self.type = ConnectionType.EXTERNAL_TO_INTERNAL
        else:
            raise DeploymentError(f"Invalid connection scope combination: {connection_dict}")

        # Determine link direction: which port should be 'from' and which should be 'to'
        if self.type == ConnectionType.INTERNAL_TO_INTERNAL:
            # For internal connections, direction is determined by port types
            if (port0_type, port1_type) in [("publisher", "subscriber"), ("server", "client")]:
                port0_is_from = True
            elif (port0_type, port1_type) in [("subscriber", "publisher"), ("client", "server")]:
                port0_is_from = False
            else:
                raise DeploymentError(f"Invalid internal connection type: {connection_dict}")
        else:
            # For external connections, port types must match
            if port0_type != port1_type:
                raise DeploymentError(f"Invalid external connection type: {connection_dict}")
            # Direction is determined based on port type and instance presence
            instance_port_is_port0 = bool(port0_instance)
            instance_port_type = port0_type if instance_port_is_port0 else port1_type
            if instance_port_type in ["publisher", "server"]:
                port0_is_from = instance_port_is_port0
            else:
                port0_is_from = not instance_port_is_port0

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
