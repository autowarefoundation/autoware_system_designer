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

import logging
from dataclasses import dataclass, field
from typing import ClassVar, List, Optional

from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.naming import generate_unique_id
from autoware_system_designer.model.events import Event

logger = logging.getLogger(__name__)


def generate_port_path(namespace: List[str], name: str) -> str:
    if namespace:
        return "/" + "/".join(namespace) + "/" + name
    return "/" + name


@dataclass(init=False, eq=False, repr=False)
class PortEvent(Event):
    direction: str = field(default="", metadata={"exclude": True})  # "input" or "output"
    port_name: str = field(default="", metadata={"exclude": True})

    def __init__(self, name: str, namespace: List[str], direction: str, port_name: str):
        super().__init__(name, namespace)
        self.direction = direction
        self.port_name = port_name

    @property
    def unique_id(self):
        # Match the Port's unique_id format
        return generate_unique_id(self.namespace, "port", self.direction, self.port_name)

    @property
    def is_port_event(self):
        return True


@dataclass(init=False, eq=False, repr=False)
class Port:
    name: str
    msg_type: str
    namespace: List[str] = field(default_factory=list)
    # Reference list: ports that this port points to (for hierarchical port connections).
    # OutPort (publisher) can have at most 1 reference (one topic published by one node).
    # InPort (subscriber) can have multiple references (one topic subscribed by multiple nodes).
    reference: List["Port"] = field(default_factory=list, metadata={"exclude": True})
    # Topic owned by a node port; interface ports of modules hold none and resolve
    # theirs from the publishing node port.
    own_topic: List[str] = field(default_factory=list, metadata={"exclude": True})
    event: Optional[Event] = None
    is_global: bool = False
    # True when topic is set by a system remap entry
    is_remapped: bool = False
    remap_target: Optional[str] = None
    # ROS interface kind: publisher/subscriber/server/client
    role: Optional[str] = field(default=None, metadata={"exclude": True})

    __serde_computed__: ClassVar[tuple] = (
        ("topic", "topic"),
        ("unique_id", "unique_id"),
        ("port_path", "port_path"),
    )

    def __init__(self, name: str, msg_type: str, namespace: List[str] = [], remap_target: str = None, role: str = None):
        self.name = name
        self.msg_type = msg_type
        self.namespace = namespace
        self.reference = []
        self.own_topic = []
        self.event = None
        self.is_global = False
        self.is_remapped = False
        self.remap_target = remap_target
        self.role = role

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, "port", self.name)

    @property
    def port_path(self):
        return generate_port_path(self.namespace, self.name)

    def set_references(self, port_list: List["Port"]):
        reference_name_list = [p.port_path for p in self.reference]
        added = []
        for port in port_list:
            if port.port_path not in reference_name_list:
                self.reference.append(port)
                added.append(port.port_path)
        # an interface port holds no topic of its own
        self.own_topic = []
        if added:
            logger.debug(f"Port '{self.port_path}' added references: {added}")

    def get_chain_list(self):
        """This port and every port below it in the reference chain."""
        ports = [self]
        for port in self.reference:
            ports.extend(port.get_chain_list())
        return ports

    def get_reference_list(self):
        """Node ports at the bottom of the reference chain; a node port stands for itself."""
        if self.reference == []:
            return [self]
        leaves = []
        for port in self.reference:
            leaves.extend(port.get_reference_list())
        return leaves

    @property
    def is_interface(self) -> bool:
        """True for a module interface port, which stands for the node ports it references."""
        return bool(self.reference)

    @property
    def topic(self) -> List[str]:
        return self.own_topic

    def set_topic(self, topic_namespace: List[str], topic_name: str):
        self.own_topic = topic_namespace + [topic_name]

    def get_topic(self) -> str:
        if self.topic == []:
            return ""
        return "/" + "/".join(self.topic)


@dataclass(init=False, eq=False, repr=False)
class InPort(Port):
    is_required: bool = field(default=True, metadata={"exclude": True})
    # Servers list: ports that this port is subscribed to (for hierarchical port connections).
    # InPort (subscriber) can have multiple servers (one topic subscribed by multiple nodes).
    servers: List[Port] = field(default_factory=list, metadata={"ref": True, "alias": "connected_ids"})

    def __init__(self, name, msg_type, namespace: List[str] = [], remap_target: str = None, role: str = None):
        super().__init__(name, msg_type, namespace, remap_target, role)
        self.is_required = True
        self.servers = []
        self.event = PortEvent("input_" + name, namespace, "input", name)
        self.event.set_type("on_input")

        if self.remap_target is None:
            self.remap_target = "~/input/" + name

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, "port", "input", self.name)

    @property
    def port_path(self):
        return generate_port_path(self.namespace, "input/" + self.name)

    def set_servers(self, port_list: List[Port]):
        server_name_list = [p.port_path for p in self.servers]
        added = []
        for port in port_list:
            if port.port_path not in server_name_list:
                self.servers.append(port)
                added.append(port.port_path)
        if added:
            logger.debug(f"InPort '{self.port_path}' added servers: {added}")

    @property
    def topic(self) -> List[str]:
        """The publisher's topic; a global node subscriber keeps its own, an unconnected interface port has none."""
        if self.servers and not (self.is_global and self.own_topic):
            return self.servers[0].topic
        return self.own_topic


@dataclass(init=False, eq=False, repr=False)
class OutPort(Port):
    frequency: float = field(default=0.0, metadata={"exclude": True})
    is_monitored: bool = field(default=False, metadata={"exclude": True})
    # Users list: ports subscribed to this port (for hierarchical port connections).
    # OutPort (publisher) can have multiple users (one topic subscribed by multiple nodes).
    users: List[Port] = field(default_factory=list, metadata={"ref": True, "alias": "connected_ids"})

    def __init__(self, name, msg_type, namespace: List[str] = [], remap_target: str = None, role: str = None):
        super().__init__(name, msg_type, namespace, remap_target, role)
        self.frequency = 0.0
        self.is_monitored = False
        self.users = []
        self.event = PortEvent("output_" + name, namespace, "output", name)
        self.event.set_type("to_output")

        # node-local default; a module output exposing the port renames it
        self.own_topic = self.namespace + [self.name]

        if self.remap_target is None:
            self.remap_target = "~/output/" + name

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, "port", "output", self.name)

    @property
    def port_path(self):
        return generate_port_path(self.namespace, "output/" + self.name)

    def set_users(self, port_list: List[Port]):
        user_name_list = [p.port_path for p in self.users]
        added = []
        for port in port_list:
            if port.port_path not in user_name_list:
                self.users.append(port)
                added.append(port.port_path)
        if added:
            logger.debug(f"OutPort '{self.port_path}' added users: {added}")

    @property
    def topic(self) -> List[str]:
        """The publishing node port's topic."""
        if self.is_interface:
            return self.reference[0].topic
        return self.own_topic

    def set_topic(self, topic_namespace: List[str], topic_name: str):
        """Set the topic on the publishing node port; subscribers and interface ports resolve it from there."""
        if self.is_interface:
            self.reference[0].set_topic(topic_namespace, topic_name)
            return
        super().set_topic(topic_namespace, topic_name)

    def set_references(self, port_list: List["Port"]):
        """
        Override to enforce that OutPort (upstream/publisher) can have at most one reference.
        This ensures one topic is published by one node (pub/sub system constraint).
        """
        super().set_references(port_list)
        if len(self.reference) > 1:
            ref_paths = [p.port_path for p in self.reference]
            raise ValidationError(
                f"OutPort '{self.port_path}' cannot have more than one reference. "
                f"This violates the pub/sub rule: one topic must be published by one node. "
                f"References: {ref_paths}"
            )
