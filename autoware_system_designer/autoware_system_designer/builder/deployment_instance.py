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
from typing import Callable, Dict

from autoware_system_designer.builder.compute_units import check_component_compute_units
from autoware_system_designer.builder.instances.instance_tree import set_instances
from autoware_system_designer.builder.instances.instances import Instance
from autoware_system_designer.builder.parameters.parameter_resolver import ParameterResolver
from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.source_location import SourceLocation
from autoware_system_designer.model.config import SystemConfig
from autoware_system_designer.model.namespace import Namespace
from autoware_system_designer.model.ports import InPort

logger = logging.getLogger(__name__)


class DeploymentInstance(Instance):
    """Top-level deployment instance representing a complete system deployment.

    This instance manages the entire system hierarchy, including setting up the system
    configuration, building the instance tree, establishing connections, and resolving parameters.
    Orchestrates Config→Instance conversion.
    """

    def __init__(self, name: str):
        super().__init__(name)

    def set_system(
        self,
        system_config: SystemConfig,
        config_registry,
        package_paths: Dict[str, str],
        snapshot_callback: Callable[[str, Exception | None], None] | None = None,
    ) -> None:
        """Set system for this deployment instance."""

        def _snapshot(step: str, error: Exception | None = None) -> None:
            if snapshot_callback:
                snapshot_callback(step, error)

        current_step = "parse"
        try:
            self.parameter_resolver = ParameterResolver(variables=[], package_paths=package_paths)
            logger.info(f"Setting system {system_config.full_name} for instance {self.name}")
            self.configuration = system_config
            self.entity_type = "system"
            self.set_resolved_path([])

            if system_config.variables:
                self.parameter_resolver.load_system_variables(system_config.variables)
            if system_config.variable_files:
                self.parameter_resolver.load_system_variable_files(system_config.variable_files)

            argument_names = [
                argument.get("name")
                for argument in system_config.arguments or []
                if isinstance(argument, dict) and argument.get("name")
            ]
            # One $(var name) cannot be both build-time and launch-time.
            conflicts = sorted(set(argument_names) & set(self.parameter_resolver.variable_map))
            if conflicts:
                raise ValidationError(
                    f"System {system_config.full_name} declares {', '.join(conflicts)} as both an argument "
                    "and a variable; $(var ...) resolves a variable at build time and an argument at "
                    "launch time, so each name is one or the other",
                    source=SourceLocation(file_path=system_config.file_path),
                )
            self.parameter_resolver.declare_arguments(argument_names)

            # 1. set component instances
            check_component_compute_units(system_config)
            logger.info(f"Instance '{self.name}': setting component instances")
            set_instances(self, system_config.full_name, config_registry)
            self.check_duplicate_node_path()
            _snapshot("1_parse")

            # Propagate parameter resolver to all instances in the tree (now that they exist)
            self.set_parameter_resolver(self.parameter_resolver)

            # 2. set connections
            current_step = "connections"
            logger.info(f"Instance '{self.name}': setting connections")
            self.link_manager.set_links()
            self.check_duplicate_port_path()
            self.check_ports()
            _snapshot("2_connections")

            # 3. finalize parameters (resolve substitutions); the topology reads the effective values
            current_step = "finalize"
            self._finalize_parameters_recursive()

            # 4. build logical topology
            current_step = "events"
            logger.info(f"Instance '{self.name}': building logical topology")
            self.set_event_tree()
            _snapshot("3_events")
        except Exception as e:
            _snapshot(current_step, e)
            raise

    def check_duplicate_node_path(self) -> None:
        """Check for duplicate normalized (namespace + name) node paths.

        Components/modules may share namespaces. Node instances must have unique
        normalized paths generated from namespace + node name.
        """
        node_path_map: Dict[str, Instance] = {}
        for inst in self.iter_tree():
            if inst.entity_type != "node":
                continue
            node_path = Namespace(list(Namespace.from_path(inst.namespace)) + ([inst.name] if inst.name else []))
            normalized_path = node_path.to_string()
            other = node_path_map.setdefault(normalized_path, inst)
            if other is not inst:
                raise ValidationError(
                    f"Duplicate node path found: '{normalized_path}'. "
                    f"Conflict between instance '{inst.name}' ({inst.source_file}) "
                    f"and '{other.name}' ({other.source_file})"
                )

    def check_duplicate_port_path(self) -> None:
        """Every port of the tree has its own path; ids and default topics derive from it.

        Two instances placed on one path collide here when they define a port of the same name.
        """
        owners: Dict[tuple, Instance] = {}
        for inst in self.iter_tree():
            manager = inst.link_manager
            for port in (*manager.in_ports.values(), *manager.out_ports.values()):
                key = (isinstance(port, InPort), tuple(port.namespace), port.name)
                other = owners.setdefault(key, inst)
                if other is not inst:
                    raise ValidationError(
                        f"[E_PORT_PATH_DUPLICATE] Port '{port.port_path}' is defined by both "
                        f"'{other.name}' ({other.source_file}) and '{inst.name}' ({inst.source_file}); "
                        "instances sharing a path need distinct port names"
                    )

    def _finalize_parameters_recursive(self) -> None:
        """Resolve the parameters of every node in the tree."""
        for instance in self.iter_tree():
            if instance.entity_type == "node":
                instance.parameter_manager.resolve_all_parameters()
