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

from typing import List
import logging
from .events import Event
from ...utils.naming import generate_unique_id
from ...exceptions import ValidationError

logger = logging.getLogger(__name__)

def generate_port_path(namespace: List[str], name: str) -> str:
    if namespace:
        return "/" + "/".join(namespace) + "/" + name
    return "/" + name

class PortEvent(Event):
    def __init__(self, name: str, namespace: List[str], direction: str, port_name: str):
        super().__init__(name, namespace)
        self.direction = direction  # "input" or "output"
        self.port_name = port_name

    @property
    def unique_id(self):
        # Match the Port's unique_id format
        return generate_unique_id(self.namespace, "port", self.direction, self.port_name)

    @property
    def is_port_event(self):
        return True


class Port:
    def __init__(self, name: str, msg_type: str, namespace: List[str] = [], remap_target: str = None):
        self.name = name
        self.msg_type = msg_type
        
        if "/srv/" in msg_type:
            self.interface_type = "service"
        elif "/action/" in msg_type:
            self.interface_type = "action"
        else:
            # Default to topic (includes /msg/ and others)
            self.interface_type = "topic"

        self.namespace = namespace
        # Reference list: ports that this port points to (for hierarchical port connections).
        # OutPort (publisher) can have at most 1 reference (one topic published by one node).
        # InPort (subscriber) can have multiple references (one topic subscribed by multiple nodes).
        self.reference: List["Port"] = []
        self.topic: List[str] = []
        self.event = None
        self.is_global = False
        self.remap_target = remap_target

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
        if added:
            logger.debug(f"Port '{self.port_path}' added references: {added}")

    def get_reference_list(self):
        if self.reference == []:
            return [self]
        return self.reference

    def set_topic(self, topic_namespace: List[str], topic_name: str):
        new_topic = topic_namespace + [topic_name]
        # if the topic is already set, return False
        if self.topic == new_topic:
            return False
        self.topic = new_topic
        return True
    
    def get_topic(self) -> str:
        if self.topic == []:
            return ""
        return "/" + "/".join(self.topic)

class InPort(Port):
    def __init__(self, name, msg_type, namespace: List[str] = [], remap_target: str = None):
        super().__init__(name, msg_type, namespace, remap_target)
        self.is_required = True
        # Servers list: ports that this port is subscribed to (for hierarchical port connections).
        # InPort (subscriber) can have multiple servers (one topic subscribed by multiple nodes).
        self.servers: List[Port] = []
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

    def set_topic(self, topic_namespace: List[str], topic_name: str):
        """
        Override to propagate topic changes to all references (Internal InPorts).
        When topic is set by a higher layer (e.g., external connection),
        all internal ports need to update their topic as well.
        """
        if not super().set_topic(topic_namespace, topic_name):
            return
        for ref_port in self.reference:
            ref_port.set_topic(topic_namespace, topic_name)



class OutPort(Port):
    def __init__(self, name, msg_type, namespace: List[str] = [], remap_target: str = None):
        super().__init__(name, msg_type, namespace, remap_target)
        self.frequency = 0.0
        self.is_monitored = False
        self.users: List[Port] = []
        # Users list: ports that this port is subscribed to (for hierarchical port connections).
        # OutPort (publisher) can have multiple users (one topic subscribed by multiple nodes).
        self.event = PortEvent("output_" + name, namespace, "output", name)
        self.event.set_type("to_output")

        # set default topic
        self.set_topic(self.namespace, self.name)
        
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

    def set_topic(self, topic_namespace: List[str], topic_name: str):
        """
        Override to propagate topic changes to all users (InPorts).
        When topic is set by a higher layer (e.g., external connection),
        all subscribers need to update their topic as well.
        """
        if not super().set_topic(topic_namespace, topic_name):
            return
        # Propagate topic to all users (InPorts that subscribe to this OutPort)
        for user_port in self.users:
            user_port.set_topic(topic_namespace, topic_name)

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
