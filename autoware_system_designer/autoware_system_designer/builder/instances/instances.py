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

from typing import Dict, List, Optional

from ...deployment.deployment_config import deploy_config
from ...exceptions import ValidationError
from ...models.config import ModuleConfig, NodeConfig, ParameterSetConfig, SystemConfig
from ...utils.naming import generate_unique_id
from ...visualization.visualization_guide import get_component_color, get_component_position
from ..config.launch_manager import LaunchManager
from ..graph.event_manager import EventManager
from ..graph.link_manager import LinkManager
from ..parameters.parameter_manager import ParameterManager
from ..runtime.namespace import Namespace
from .instance_serializer import (
    collect_instance_data,
    collect_system_structure,
)


class Instance:
    """Base class for all instances in the system hierarchy.

    Represents a node in the instance tree, which can be a system, module, or node.
    Manages configuration, topology, interfaces, parameters, and events.
    """

    def __init__(
        self, name: str, compute_unit: str = "", namespace: list[str] | Namespace | None = None, layer: int = 0
    ):
        self.name: str = name
        self.namespace: Namespace = Namespace(namespace)
        self.resolved_path = Namespace(list(self.namespace) + [self.name])

        self.compute_unit: str = compute_unit
        self.layer: int = layer
        if self.layer > deploy_config.layer_limit:
            raise ValidationError(f"Instance layer is too deep (limit: {deploy_config.layer_limit})")

        # configuration
        self.configuration: NodeConfig | ModuleConfig | ParameterSetConfig | SystemConfig | None = None
        self.source_file: Optional[str] = None

        # launch (node instances only)
        self.launch_manager: Optional[LaunchManager] = None

        # instance topology
        self.entity_type: str = None
        self.parent: Instance = None
        self.children: Dict[str, Instance] = {}
        self.parent_module_list: List[str] = []

        # interface
        self.link_manager: LinkManager = LinkManager(self)

        # parameter manager
        self.parameter_manager: ParameterManager = ParameterManager(self)

        # parameter resolver (set later by deployment)
        self.parameter_resolver = None

        # event manager
        self.event_manager: EventManager = EventManager(self)

    def set_resolved_path(self, resolved_path: list[str] | Namespace) -> None:
        """Set resolved path and synchronize exported port namespace."""
        self.resolved_path = Namespace(resolved_path)

    @property
    def port_namespace(self) -> Namespace:
        """Get the namespace to be used for port naming."""
        return self.resolved_path

    @property
    def path_list(self) -> List:
        return list(self.resolved_path)

    def set_parameter_resolver(self, parameter_resolver):
        """Set the parameter resolver for this instance and propagate to parameter manager."""
        self.parameter_resolver = parameter_resolver
        if self.parameter_manager:
            self.parameter_manager.parameter_resolver = parameter_resolver

        # Recursively set for all children
        for child in self.children.values():
            child.set_parameter_resolver(parameter_resolver)

        # status
        self.is_initialized = False

    @property
    def path(self) -> str:
        """Get the full path of this instance in the hierarchy."""
        if self.entity_type == "system":
            return "/"

        resolved = self.resolved_path.to_string()
        return resolved if resolved else "/"

    @property
    def unique_id(self):
        return generate_unique_id(self.path, "instance", self.compute_unit, self.layer, self.name)

    @property
    def vis_guide(self) -> dict:
        """Get visualization guide including colors."""
        return {
            "color": get_component_color(self.path_list, variant="base"),
            "medium_color": get_component_color(self.path_list, variant="medium"),
            "background_color": get_component_color(self.path_list, variant="bright"),
            "text_color": get_component_color(self.path_list, variant="darkest"),
            "dark_color": get_component_color(self.path_list, variant="fade"),
            "dark_medium_color": get_component_color(
                self.path_list, variant="darkish"
            ),  # Integrated dark+text variant for nodes
            "dark_background_color": get_component_color(
                self.path_list, variant="dark"
            ),  # Pure dark variant for modules
            "dark_text_color": get_component_color(self.path_list, variant="bright"),
            "position": get_component_position(self.path_list),
        }

    def get_child(self, name: str):
        if name in self.children:
            return self.children[name]
        raise ValidationError(f"Child not found: child name '{name}', instance of '{self.name}'")

    def check_ports(self):
        # recursive call for children
        for child in self.children.values():
            child.check_ports()

        # delegate to link manager
        self.link_manager.check_ports()

    def set_event_tree(self):
        # delegate to event manager
        self.event_manager.set_event_tree()

    def collect_instance_data(self) -> dict:
        return collect_instance_data(self)

    def collect_system_structure(self, system_name: str, mode: str) -> dict:
        return collect_system_structure(self, system_name, mode)
