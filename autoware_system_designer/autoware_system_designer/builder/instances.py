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
from ..deployment_config import deploy_config
from ..exceptions import ValidationError
from ..utils.naming import generate_unique_id
from ..visualization.visualization_guide import get_component_color, get_component_position
from .config_registry import ConfigRegistry
from .parameter_resolver import ParameterResolver
from .parameter_manager import ParameterManager
from .link_manager import LinkManager
from .event_manager import EventManager

logger = logging.getLogger(__name__)


class Instance:
    # Common attributes for node hierarch instance
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
        # No more mode filtering - components are already resolved by mode configuration
        components_to_instantiate = self.configuration.components
        
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
            if self.parameter_resolver:
                instance.set_parameter_resolver(self.parameter_resolver)

            try:
                instance.set_instances(entity_id, config_registry)
            except Exception as e:
                # add the instance to the children dict for debugging
                self.children[instance_name] = instance
                raise ValidationError(f"Error in setting component instance '{instance_name}', at {self.configuration.file_path}")

            self.children[instance_name] = instance
            logger.debug(f"System instance '{self.namespace_str}' added component '{instance_name}' (uid={instance.unique_id})")
        
        # Apply system-level parameter sets
        # The parameter_sets in configuration have already been resolved by deployment.py
        # (including any mode-specific overrides)
        if hasattr(self.configuration, 'parameter_sets') and self.configuration.parameter_sets:
            parameter_sets_to_apply = self.configuration.parameter_sets
            logger.info(f"Applying {len(parameter_sets_to_apply)} system-level parameter set(s)")
            # Create a dummy component config to reuse _apply_parameter_set
            dummy_component_config = {'parameter_set': parameter_sets_to_apply}
            # Apply to self (root), disabling namespace check to allow global parameters
            self._apply_parameter_set(self, dummy_component_config, config_registry, check_namespace=False,
                                      file_parameter_type=ParameterType.MODE_FILE,
                                      direct_parameter_type=ParameterType.MODE)

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

    def _apply_parameter_set(self, instance: "Instance", cfg_component: dict, config_registry: ConfigRegistry, check_namespace: bool = True,
                             file_parameter_type: ParameterType = ParameterType.OVERRIDE_FILE,
                             direct_parameter_type: ParameterType = ParameterType.OVERRIDE):
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

                # Determine which resolver to use
                resolver_to_use = self.parameter_resolver

                # If local_variables exist and we have a resolver, create a scoped resolver
                if cfg_param_set.local_variables and resolver_to_use:
                    resolver_to_use = resolver_to_use.copy()
                    # Resolve local variables (updating the scoped resolver's map)
                    resolver_to_use.resolve_parameters(cfg_param_set.local_variables)
                    logger.debug(f"Created scoped resolver for '{param_set_name}' with {len(cfg_param_set.local_variables)} local variables")

                for param_config in node_params:
                    if isinstance(param_config, dict) and "node" in param_config:
                        node_namespace = param_config.get("node")
                        
                        # Only apply if the target node is under this component's namespace
                        if check_namespace and node_namespace != instance.namespace_str and not node_namespace.startswith(instance.namespace_str + "/"):
                            logger.debug(f"Parameter set '{param_set_name}' skip node '{node_namespace}' (component namespace '{instance.namespace_str}')")
                            continue
                        
                        parameter_files_raw = param_config.get("parameter_files", [])
                        parameters = param_config.get("parameters", [])

                        # Resolve ROS substitutions if resolver is available
                        if resolver_to_use:
                            parameter_files_raw = resolver_to_use.resolve_parameter_files(parameter_files_raw)
                            parameters = resolver_to_use.resolve_parameters(parameters)

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
                            node_namespace, parameter_files, parameters, config_registry,
                            file_parameter_type=file_parameter_type,
                            direct_parameter_type=direct_parameter_type
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

            child_name = cfg_node.get("instance")
            instance = Instance(
                child_name, self.compute_unit, self.namespace + [child_name], self.layer + 1
            )
            instance.parent = self
            instance.parent_module_list = self.parent_module_list.copy()
            if self.parameter_resolver:
                instance.set_parameter_resolver(self.parameter_resolver)

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

    def _serialize_event(self, event):
        if not event:
            return None
        return {
            "unique_id": event.unique_id,
            "name": event.name,
            "type": event.type,
            "process_event": event.process_event,
            "frequency": event.frequency,
            "warn_rate": event.warn_rate,
            "error_rate": event.error_rate,
            "timeout": event.timeout,
            "trigger_ids": [t.unique_id for t in event.triggers],
            "action_ids": [a.unique_id for a in event.actions],
        }

    def _serialize_port(self, port):
        data = {
            "unique_id": port.unique_id,
            "name": port.name,
            "msg_type": port.msg_type,
            "namespace": port.namespace,
            "topic": port.topic,
            "is_global": port.is_global,
            "port_path": port.port_path,
            "event": self._serialize_event(port.event)
        }
        
        # Add connected_ids for graph traversal
        connected_ids = []
        if hasattr(port, "servers"):  # InPort
            connected_ids = [p.unique_id for p in port.servers]
        elif hasattr(port, "users"):  # OutPort
            connected_ids = [p.unique_id for p in port.users]
        data["connected_ids"] = connected_ids
        
        return data

    def collect_instance_data(self) -> dict:
        data = {
            "name": self.name,
            "unique_id": self.unique_id,
            "entity_type": self.entity_type,
            "namespace": self.namespace,
            "compute_unit": self.compute_unit,
            "vis_guide": self.vis_guide,
            "in_ports": [self._serialize_port(p) for p in self.link_manager.get_all_in_ports()],
            "out_ports": [self._serialize_port(p) for p in self.link_manager.get_all_out_ports()],
            "children": (
                [child.collect_instance_data() for child in self.children.values()]
                if hasattr(self, "children")
                else []
            ),
            "links": (
                [
                    {
                        "unique_id": link.unique_id,
                        "from_port": self._serialize_port(link.from_port),
                        "to_port": self._serialize_port(link.to_port),
                        "msg_type": link.msg_type,
                        "topic": link.topic,
                    }
                    for link in self.link_manager.get_all_links()
                ]
                if hasattr(self.link_manager, "links")
                else []
            ),
            "events": [self._serialize_event(e) for e in self.event_manager.get_all_events()],
            "parameters": [
                {
                    "name": p.name,
                    "value": p.value,
                    "type": p.data_type,
                    "parameter_type": p.parameter_type.name if hasattr(p.parameter_type, 'name') else str(p.parameter_type)
                } for p in self.parameter_manager.get_all_parameters()
            ],
        }

        return data

    def _finalize_parameters_recursive(self):
        """Recursively finalize all parameters in the instance tree.
        Resolve any remaining substitutions in all parameters and parameter files.
        """
        # If this is a node, resolve all its parameters
        if self.entity_type == "node" and hasattr(self, 'parameter_manager'):
            self.parameter_manager.resolve_all_parameters()

        # Recursively process children
        for child in self.children.values():
            child._finalize_parameters_recursive()

class DeploymentInstance(Instance):
    def __init__(self, name: str, mode: str = None):
        super().__init__(name)
        self.mode = mode  # Store mode for this deployment instance

    def set_system(
        self,
        system_config: SystemConfig,
        config_registry,
        mode: str = None,
        package_paths: Dict[str, str] = {},
    ):
        """Set system for this deployment instance.

        Args:
            system_config: System configuration (should have mode-specific config already applied)
            config_registry: Registry of all configurations
            mode: Optional mode name for metadata (not used for filtering - deprecated)
            package_paths: Package paths for parameter resolution
        
        Note:
            Mode-specific configuration should be applied to system_config before calling this method.
            The mode parameter is kept for backward compatibility and metadata only.
        """
        self.mode = mode
        self.parameter_resolver = ParameterResolver(variables=[], package_paths=package_paths)
        logger.info(f"Setting system {system_config.full_name} for instance {self.name}")
        self.configuration = system_config
        self.entity_type = "system"

        # Apply system variables and variable files to the parameter resolver if available
        if self.parameter_resolver:
            if hasattr(system_config, 'variables') and system_config.variables:
                self.parameter_resolver.load_system_variables(system_config.variables)
            
            if hasattr(system_config, 'variable_files') and system_config.variable_files:
                self.parameter_resolver.load_system_variable_files(system_config.variable_files)

        # 1. set component instances
        logger.info(f"Instance '{self.name}': setting component instances")
        self.set_instances(system_config.full_name, config_registry)

        # Propagate parameter resolver to all instances in the tree (now that they exist)
        self.set_parameter_resolver(self.parameter_resolver)

        # 2. set connections
        logger.info(f"Instance '{self.name}': setting connections")
        self.link_manager.set_links()
        self.check_ports()

        # 3. build logical topology
        logger.info(f"Instance '{self.name}': building logical topology")
        # self.build_logical_topology()
        self.set_event_tree()

        # 4. validate node namespaces
        self.check_duplicate_node_namespaces()

        # 5. finalize parameters (resolve substitutions)
        self._finalize_parameters_recursive()

    def check_duplicate_node_namespaces(self):
        """Check for duplicate node namespaces in the entire system."""
        namespace_map = {}
        
        def _collect_namespaces(inst):
            if inst.entity_type == "node":
                if inst.namespace_str in namespace_map:
                    raise ValidationError(
                        f"Duplicate node namespace found: '{inst.namespace_str}'. "
                        f"Conflict between instance '{inst.name}' and '{namespace_map[inst.namespace_str]}'"
                    )
                namespace_map[inst.namespace_str] = inst.name
            
            for child in inst.children.values():
                _collect_namespaces(child)
                
        _collect_namespaces(self)
