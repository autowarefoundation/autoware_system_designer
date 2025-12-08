# Copyright 2025 TIER IV, inc.
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

from typing import List

from .ports import Port, InPort, OutPort
from ..exceptions import ValidationError, DeploymentError

from enum import Enum

class ConnectionType(int, Enum):
    UNDEFINED = 0
    EXTERNAL_TO_INTERNAL = 1
    INTERNAL_TO_INTERNAL = 2
    INTERNAL_TO_EXTERNAL = 3

class Link:
    # Link is a connection between two ports
    def __init__(self, msg_type: str, from_port: Port, to_port: Port, namespace: List[str] = [], connection_type: ConnectionType = ConnectionType.UNDEFINED):
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
    def __init__(self, connection_dict: dict):

        # connection type
        self.type: ConnectionType = ConnectionType.UNDEFINED

        connection_from = connection_dict.get("from")
        if connection_from is None:
            raise DeploymentError(f"Connection couldn't found : {connection_dict}")
        connection_to = connection_dict.get("to")
        if connection_to is None:
            raise DeploymentError(f"Connection couldn't found : {connection_dict}")

        from_instance, from_port_name = self.parse_port_name(connection_from)
        to_instance, to_port_name = self.parse_port_name(connection_to)

        if from_instance == "" and to_instance == "":
            raise DeploymentError(f"Invalid connection: {connection_dict}")
        elif from_instance == "" and to_instance != "":
            self.type = ConnectionType.EXTERNAL_TO_INTERNAL
        elif from_instance != "" and to_instance == "":
            self.type = ConnectionType.INTERNAL_TO_EXTERNAL
        elif from_instance != "" and to_instance != "":
            self.type = ConnectionType.INTERNAL_TO_INTERNAL

        self.from_instance: str = from_instance
        self.from_port_name: str = from_port_name
        self.to_instance: str = to_instance
        self.to_port_name: str = to_port_name

    def parse_port_name(self, port_name: str) -> tuple[str, str]:  # (instance_name, port_name)
        name_splitted = port_name.split(".")
        if len(name_splitted) == 2:
            if name_splitted[0] == "input":
                return "", name_splitted[1]  # external input
            if name_splitted[0] == "output":
                return "", name_splitted[1]  # external output
            raise DeploymentError(f"Invalid port name: {port_name}")
        elif len(name_splitted) == 3:
            if name_splitted[1] == "input":
                return name_splitted[0], name_splitted[2]  # internal input
            if name_splitted[1] == "output":
                return name_splitted[0], name_splitted[2]  # internal output
            raise DeploymentError(f"Invalid port name: {port_name}")
        else:
            raise DeploymentError(f"Invalid port name: {port_name}")