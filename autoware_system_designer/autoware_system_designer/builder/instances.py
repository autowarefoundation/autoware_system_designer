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

import logging
from typing import List, Dict

from ..models.config import Config, NodeConfig, ModuleConfig, ParameterSetConfig, SystemConfig
from ..models.parameters import ParameterType
from ..parsers.data_parser import entity_name_decode
from ..config import config
from ..exceptions import ValidationError
from ..utils.naming import generate_unique_id
from ..visualization.visualization_guide import get_component_color, get_component_position
from .config_registry import ConfigRegistry
from .parameter_manager import ParameterManager
from .link_manager import LinkManager
from .event_manager import EventManager

logger = logging.getLogger(__name__)


def normalize_mode_field(mode_field) -> List[str]:
    """Normalize mode field to list of mode names.
    
    Args:
        mode_field: Can be None, string, or list of strings
        
    Returns:
        List of mode names (empty list if None)
    """
    if mode_field is None:
        return []
    if isinstance(mode_field, str):
        return [mode_field]
    if isinstance(mode_field, list):
        return mode_field
    raise ValidationError(f"Invalid mode field type: {type(mode_field)}")

def filter_components_by_mode(components: List[Dict], mode_name: str, available_modes: List[str]) -> List[Dict]:
    """Filter components that are enabled for the given mode.
    
    Args:
        components: List of component configurations
        mode_name: Name of the mode to filter for
        available_modes: List of all available mode names
        
    Returns:
        Filtered list of components enabled for this mode
        
    Raises:
        ValidationError: If mode references are invalid or components overlap
    """
    enabled_components = []
    component_mode_map = {}  # Track which modes each component name uses
    
    for cfg_component in components:
        component_name = cfg_component.get("component")
        component_modes = normalize_mode_field(cfg_component.get("mode"))
        
        # If no mode specified, enable for all modes
        if not component_modes:
            component_modes = available_modes
        
        # Validate mode references
        for mode in component_modes:
            if mode not in available_modes:
                raise ValidationError(
                    f"Component '{component_name}' references undefined mode '{mode}'. "
                    f"Available modes: {available_modes}"
                )
        
        # Check for mode overlap with same component name
        if component_name not in component_mode_map:
            component_mode_map[component_name] = set()
        
        for mode in component_modes:
            if mode in component_mode_map[component_name]:
                raise ValidationError(
                    f"Component '{component_name}' is defined multiple times for mode '{mode}'"
                )
            component_mode_map[component_name].add(mode)
        
        # Add to enabled list if this mode is in component's mode list
        if mode_name in component_modes:
            enabled_components.append(cfg_component)
    
    return enabled_components

class Instance:
    # Common attributes for node hierarch instance
    def __init__(
        self, name: str, compute_unit: str = "", namespace: list[str] = [], layer: int = 0
    ):
        self.name: str = name
        self.namespace: List[str] = namespace.copy()
        # add the instance name to the namespace
        self.namespace.append(name)
        self.namespace_str: str = "/" + "/".join(self.namespace)

        self.compute_unit: str = compute_unit
        self.layer: int = layer
        if self.layer > config.layer_limit:
            raise ValidationError(f"Instance layer is too deep (limit: {config.layer_limit})")

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
        return generate_unique_id(self.namespace, self.compute_unit, self.layer, self.name)
    
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

    def set_instances(self, entity_id: str, config_registry: ConfigRegistry):

        try:
            entity_name, entity_type = entity_name_decode(entity_id)
            if entity_type == "system":
                self._set_system_instances(config_registry)
            elif entity_type == "module":
                self._set_module_instances(entity_id, entity_name, config_registry)
            elif entity_type == "node":
                self._set_node_instances(entity_id, entity_name, config_registry)
        except Exception as e:
            raise ValidationError(f"Error setting instances for {entity_id}, at {self.configuration.file_path}")

    def _set_system_instances(self, config_registry: ConfigRegistry):
        """Set instances for system entity type."""
        # Determine which components to instantiate based on mode
        components_to_instantiate = self.configuration.components
        
        # If this is a DeploymentInstance with a mode, filter components
        if hasattr(self, 'mode') and self.mode is not None:
            # Get available modes from configuration
            modes_config = self.configuration.modes or []
            available_modes = [m.get('name') for m in modes_config]
            
            # Filter components for this mode
            components_to_instantiate = filter_components_by_mode(
                self.configuration.components, 
                self.mode, 
                available_modes
            )
            logger.info(f"System instance '{self.namespace_str}' mode '{self.mode}': "
                       f"{len(components_to_instantiate)}/{len(self.configuration.components)} components enabled")
        
        # First pass: create all component instances
        for cfg_component in components_to_instantiate:
            compute_unit_name = cfg_component.get("compute_unit")
            instance_name = cfg_component.get("component")
            entity_id = cfg_component.get("entity")
            namespace = cfg_component.get("namespace")
            if namespace:
                if isinstance(namespace, str):
                    namespace = namespace.split('/') if '/' in namespace else [namespace]
            else:
                namespace = []

            # create instance
            instance = Instance(instance_name, compute_unit_name, namespace)
            instance.parent = self
            try:
                instance.set_instances(entity_id, config_registry)
            except Exception as e:
                # add the instance to the children dict for debugging
                self.children[instance_name] = instance
                raise ValidationError(f"Error in setting component instance '{instance_name}', at {self.configuration.file_path}")

            self.children[instance_name] = instance
            logger.debug(f"System instance '{self.namespace_str}' added component '{instance_name}' (uid={instance.unique_id})")
        
        # Second pass: apply parameter sets after all instances are created
        # This ensures that parameter_sets can target nodes across different components
        for cfg_component in components_to_instantiate:
            instance_name = cfg_component.get("component")
            instance = self.children[instance_name]
            self._apply_parameter_set(instance, cfg_component, config_registry)
        
        # all children are initialized
        self.is_initialized = True

    def _set_module_instances(self, entity_id: str, entity_name: str, config_registry: ConfigRegistry):
        """Set instances for module entity type."""
        logger.info(f"Setting module entity {entity_id} for instance {self.namespace_str}")
        self.configuration = config_registry.get_module(entity_name)
        self.entity_type = "module"

        # check if the module is already set
        if entity_id in self.parent_module_list:
            raise ValidationError(f"Config is already set: {entity_id}, avoid circular reference")
        self.parent_module_list.append(entity_id)

        # set children
        self._create_module_children(config_registry)

        # run the module configuration
        self._run_module_configuration()

        # recursive call is finished
        self.is_initialized = True

    def _set_node_instances(self, entity_id: str, entity_name: str, config_registry: ConfigRegistry):
        """Set instances for node entity type."""
        logger.info(f"Setting node entity {entity_id} for instance {self.namespace_str}")
        self.configuration = config_registry.get_node(entity_name)
        self.entity_type = "node"

        # run the node configuration
        self._run_node_configuration(config_registry)

        # recursive call is finished
        self.is_initialized = True

    def _apply_parameter_set(self, instance: "Instance", cfg_component: dict, config_registry: ConfigRegistry):
        """Apply parameter set(s) to an instance using direct node targeting.
        
        Supports both single parameter_set (str) and multiple parameter_sets (list of str).
        When multiple parameter_sets are provided, they are applied sequentially, allowing
        later sets to overwrite earlier ones.
        
        Only applies parameters to nodes that are descendants of the given instance.
        """
        parameter_set = cfg_component.get("parameter_set")
        if parameter_set is None:
            return
        
        # Normalize to list for uniform processing
        parameter_set_list = parameter_set if isinstance(parameter_set, list) else [parameter_set]
        
        # Apply each parameter set sequentially
        for param_set_id in parameter_set_list:
            try:
                param_set_name, entity_type = entity_name_decode(param_set_id)
                if entity_type != "parameter_set":
                    raise ValidationError(f"Invalid parameter set type: {entity_type}, at {self.configuration.file_path}")
                
                cfg_param_set = config_registry.get_parameter_set(param_set_name)
                node_params = cfg_param_set.parameters
                logger.info(f"Applying parameter set '{param_set_name}' to component '{instance.name}'")

                for param_config in node_params:
                    if isinstance(param_config, dict) and "node" in param_config:
                        node_namespace = param_config.get("node")
                        
                        # Only apply if the target node is under this component's namespace
                        if not node_namespace.startswith(instance.namespace_str + "/"):
                            logger.debug(f"Parameter set '{param_set_name}' skip node '{node_namespace}' (component namespace '{instance.namespace_str}')")
                            continue
                        
                        parameter_files_raw = param_config.get("parameter_files", [])
                        parameters = param_config.get("parameters", [])

                        # Resolve ROS substitutions if resolver is available
                        if self.parameter_resolver:
                            parameter_files_raw = self.parameter_resolver.resolve_parameter_files(parameter_files_raw)
                            parameters = self.parameter_resolver.resolve_parameters(parameters)

                        # Validate parameter_files format (should be list of dicts)
                        parameter_files = []
                        if parameter_files_raw:
                            for pf in parameter_files_raw:
                                if isinstance(pf, dict):
                                    parameter_files.append(pf)
                                else:
                                    logger.warning(f"Invalid parameter_files format in parameter set '{param_set_name}': {pf}")

                        # Apply parameters directly to the target node
                        instance.parameter_manager.apply_node_parameters(
                            node_namespace, parameter_files, parameters, config_registry
                        )
                        logger.debug(f"Applied parameters to node '{node_namespace}' from set '{param_set_name}' files={len(parameter_files)} configs={len(parameters)}")
            except Exception as e:
                raise ValidationError(f"Error in applying parameter set '{param_set_name}' to instance '{instance.name}': {e}")

    def _create_module_children(self, config_registry: ConfigRegistry):
        """Create child instances for module entities."""
        cfg_node_list = self.configuration.instances
        for cfg_node in cfg_node_list:
            # check if cfg_node has 'node' and 'entity'
            if "instance" not in cfg_node or "entity" not in cfg_node:
                raise ValidationError(f"Module instance configuration must have 'node' and 'entity' fields, at {self.configuration.file_path}")

            instance = Instance(
                cfg_node.get("instance"), self.compute_unit, self.namespace, self.layer + 1
            )
            instance.parent = self
            instance.parent_module_list = self.parent_module_list.copy()
            # recursive call of set_instances
            try:
                instance.set_instances(cfg_node.get("entity"), config_registry)
            except Exception as e:
                # add the instance to the children dict for debugging
                self.children[instance.name] = instance
                raise ValidationError(f"Error in setting child instance {instance.name} : {e}, at {self.configuration.file_path}")
            self.children[instance.name] = instance
        
    def _run_module_configuration(self):
        if self.entity_type != "module":
            raise ValidationError(f"run_module_configuration is only supported for module, at {self.configuration.file_path}")

        # set connections
        if len(self.configuration.connections) == 0:
            logger.warning(f"Module '{self.name}' has no connections configured, at {self.configuration.file_path}")
            return

        # set links first to know topic type for external ports
        self.link_manager.set_links()

        # log module configuration
        self.link_manager.log_module_configuration()

    def _run_node_configuration(self, config_registry: ConfigRegistry):
        if self.entity_type != "node":
            raise ValidationError(f"run_node_configuration is only supported for node, at {self.configuration.file_path}")

        # set ports
        self.link_manager.initialize_node_ports()

        # set parameters
        self.parameter_manager.initialize_node_parameters(config_registry)

        # initialize processes and events
        self.event_manager.initialize_node_processes()

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
        data = {
            "name": self.name,
            "unique_id": self.unique_id,
            "entity_type": self.entity_type,
            "namespace": self.namespace,
            "compute_unit": self.compute_unit,
            "vis_guide": self.vis_guide,
            "in_ports": self.link_manager.get_all_in_ports(),
            "out_ports": self.link_manager.get_all_out_ports(),
            "children": (
                [child.collect_instance_data() for child in self.children.values()]
                if hasattr(self, "children")
                else []
            ),
            "links": (
                [
                    {
                        "from_port": link.from_port,
                        "to_port": link.to_port,
                        "msg_type": link.msg_type,
                    }
                    for link in self.link_manager.get_all_links()
                ]
                if hasattr(self.link_manager, "links")
                else []
            ),
            "events": self.event_manager.get_all_events(),
            "parameters": [
                {
                    "name": p.name,
                    "value": p.value,
                    "type": p.data_type,
                    "parameter_type": p.parameter_type.name if hasattr(p.parameter_type, 'name') else str(p.parameter_type)
                } for p in self.parameter_manager.get_all_parameters()
            ],
        }
        
        # Add mode information if this is a deployment instance
        if hasattr(self, 'mode') and self.mode is not None:
            data["mode"] = self.mode

        return data

    def apply_global_parameters(self, global_params_config):
        """Apply global parameters from deployment configuration to all nodes in the instance.

        Args:
            global_params_config: List of global parameter configurations from deployment
        """
        if not global_params_config:
            logger.debug("No global parameters defined in deployment configuration")
            return

        logger.info(f"Applying {len(global_params_config)} global parameters to all nodes")

        # Traverse all instances and apply global parameters to nodes
        self._apply_global_parameters_recursive(global_params_config)

        # Now that global parameters are applied, resolve any remaining substitutions in all parameters
        if hasattr(self, 'parameter_resolver') and self.parameter_resolver:
            self._resolve_all_parameters_recursive()

    def _apply_global_parameters_recursive(self, global_params_config):
        """Recursively apply global parameters to all nodes in the instance tree.

        Args:
            global_params_config: List of global parameter configurations
        """
        # If this is a node, apply global parameters to it
        if self.entity_type == "node":
            for param_config in global_params_config:
                param_name = param_config.get('name')
                param_value = param_config.get('value')
                param_type = param_config.get('type', 'string')  # Default to string if not specified

                # Resolve parameter value if resolver is available
                if self.parameter_resolver:
                    param_value = self.parameter_resolver.resolve_parameter_value(param_value)

                if param_name is not None and param_value is not None:
                    self.parameter_manager.parameters.set_parameter(
                        param_name,
                        param_value,
                        data_type=param_type,
                        allow_substs=True,
                        parameter_type=ParameterType.GLOBAL
                    )
                    logger.debug(f"Applied global parameter '{param_name}'={param_value} to node '{self.namespace_str}'")

        # Recursively process children
        for child in self.children.values():
            child._apply_global_parameters_recursive(global_params_config)

    def _resolve_all_parameters_recursive(self):
        """Recursively resolve all parameters in the instance tree that may contain substitutions."""
        # If this is a node, resolve all its parameters
        if self.entity_type == "node" and hasattr(self, 'parameter_manager'):
            self.parameter_manager.resolve_all_parameters()

        # Recursively process children
        for child in self.children.values():
            child._resolve_all_parameters_recursive()

class DeploymentInstance(Instance):
    def __init__(self, name: str, mode: str = None):
        super().__init__(name)
        self.mode = mode  # Store mode for this deployment instance

    def set_system(
        self,
        system: Config,
        config_registry,
        mode: str = None,
        parameter_resolver = None,
    ):
        """Set system for this deployment instance.

        Args:
            system: System configuration
            config_registry: Registry of all configurations
            mode: Optional mode name to filter components (None means no filtering)
            parameter_resolver: Resolver for ROS-independent parameter substitution
        """
        self.mode = mode
        self.parameter_resolver = parameter_resolver
        logger.info(f"Setting system {system.full_name} for instance {self.name}" +
                   (f" (mode: {mode})" if mode else ""))
        self.configuration = system
        self.entity_type = "system"

        # 1. set component instances
        logger.info(f"Instance '{self.name}': setting component instances")
        self.set_instances(system.full_name, config_registry)

        # Propagate parameter resolver to all instances in the tree (now that they exist)
        if parameter_resolver:
            self.set_parameter_resolver(parameter_resolver)

        # 2. set connections
        logger.info(f"Instance '{self.name}': setting connections")
        self.link_manager.set_links()
        self.check_ports()

        # 3. build logical topology
        logger.info(f"Instance '{self.name}': building logical topology")
        # self.build_logical_topology()
        self.set_event_tree()
