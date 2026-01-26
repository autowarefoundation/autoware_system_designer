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

from typing import List, Dict, Any, Callable

from ..models.config import NodeConfig, ModuleConfig, ParameterSetConfig, SystemConfig
from ..deployment_config import deploy_config
from ..exceptions import ValidationError
from ..utils.naming import generate_unique_id
from ..visualization.visualization_guide import get_component_color, get_component_position
from .parameter_manager import ParameterManager
from .link_manager import LinkManager
from .event_manager import EventManager
from .instance_serializer import (
    collect_instance_data,
    collect_system_structure,
)
from .instance_pipeline import (
    set_system as set_system_impl,
    check_duplicate_node_namespaces as check_duplicate_node_namespaces_impl,
)

class Instance:
    """Base class for all instances in the system hierarchy.
    
    Represents a node in the instance tree, which can be a system, module, or node.
    Manages configuration, topology, interfaces, parameters, and events.
    """
    
    def __init__(
        self, name: str, compute_unit: str = "", namespace: list[str] = [], layer: int = 0
    ):
        self.name: str = name
        self.namespace: List[str] = namespace.copy()
        # add the instance name to the namespace
        self.namespace_str: str = "/" + "/".join(self.namespace)

        self.compute_unit: str = compute_unit
        self.layer: int = layer
        if self.layer > deploy_config.layer_limit:
            raise ValidationError(f"Instance layer is too deep (limit: {deploy_config.layer_limit})")

        # configuration
        self.configuration: NodeConfig | ModuleConfig | ParameterSetConfig | SystemConfig | None = None

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
    def unique_id(self):
        return generate_unique_id(self.namespace, "instance", self.compute_unit, self.layer, self.name)
    
    @property
    def vis_guide(self) -> dict:
        """Get visualization guide including colors."""
        return {
            "color": get_component_color(self.namespace, variant="base"),
            "medium_color": get_component_color(self.namespace, variant="medium"),
            "background_color": get_component_color(self.namespace, variant="bright"),
            "text_color": get_component_color(self.namespace, variant="darkest"),
            "dark_color": get_component_color(self.namespace, variant="fade"),
            "dark_medium_color": get_component_color(self.namespace, variant="darkish"),  # Integrated dark+text variant for nodes
            "dark_background_color": get_component_color(self.namespace, variant="dark"),  # Pure dark variant for modules
            "dark_text_color": get_component_color(self.namespace, variant="bright"), 
            "position": get_component_position(self.namespace),
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

class DeploymentInstance(Instance):
    """Top-level deployment instance representing a complete system deployment.
    
    This instance manages the entire system hierarchy, including setting up the system
    configuration, building the instance tree, establishing connections, and resolving parameters.
    """
    
    def __init__(self, name: str):
        super().__init__(name)

    def set_system(
        self,
        system_config: SystemConfig,
        config_registry,
        package_paths: Dict[str, str] = {},
        snapshot_callback: Callable[[str, Exception | None], None] | None = None,
    ):
        set_system_impl(
            self,
            system_config,
            config_registry,
            package_paths=package_paths,
            snapshot_callback=snapshot_callback,
        )

    def check_duplicate_node_namespaces(self):
        """Check for duplicate node namespaces in the entire system."""
        check_duplicate_node_namespaces_impl(self)
