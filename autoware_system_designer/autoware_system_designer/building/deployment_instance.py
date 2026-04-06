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

from ..exceptions import ValidationError
from ..parsing.config import SystemConfig
from .instances.instance_tree import set_instances
from .instances.instances import Instance
from .parameters.parameter_resolver import ParameterResolver
from .runtime.namespace import Namespace

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
        package_paths: Dict[str, str] = {},
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

            # Apply system variables and variable files to the parameter resolver if available
            if self.parameter_resolver:
                if hasattr(system_config, "variables") and system_config.variables:
                    self.parameter_resolver.load_system_variables(system_config.variables)

                if hasattr(system_config, "variable_files") and system_config.variable_files:
                    self.parameter_resolver.load_system_variable_files(system_config.variable_files)

            # 1. set component instances
            logger.info(f"Instance '{self.name}': setting component instances")
            set_instances(self, system_config.full_name, config_registry)
            _snapshot("1_parse")

            # Propagate parameter resolver to all instances in the tree (now that they exist)
            self.set_parameter_resolver(self.parameter_resolver)

            # 2. set connections
            current_step = "connections"
            logger.info(f"Instance '{self.name}': setting connections")
            self.link_manager.set_links()
            self.check_ports()
            _snapshot("2_connections")

            # 3. build logical topology
            current_step = "events"
            logger.info(f"Instance '{self.name}': building logical topology")
            self.set_event_tree()
            _snapshot("3_events")

            # 4. validate node namespaces
            current_step = "validate"
            self.check_duplicate_node_path()

            # 5. finalize parameters (resolve substitutions)
            current_step = "finalize"
            self._finalize_parameters_recursive()
        except Exception as e:
            _snapshot(current_step, e)
            raise

    def check_duplicate_node_path(self) -> None:
        """Check for duplicate normalized (namespace + name) node paths.

        Components/modules may share namespaces. Node instances must have unique
        normalized paths generated from namespace + node name.
        """
        node_path_map = {}

        def _normalize_namespace_name(namespace, name: str) -> str:
            namespace_segments = Namespace.from_path(namespace)
            path_segments = list(namespace_segments)
            if name:
                path_segments.append(name)
            return f"/{'/'.join(path_segments)}" if path_segments else "/"

        def _collect_namespaces(inst):
            if inst.entity_type == "node":
                normalized_path = _normalize_namespace_name(inst.namespace, inst.name)
                if normalized_path in node_path_map:
                    raise ValidationError(
                        f"Duplicate node path found: '{normalized_path}'. "
                        f"Conflict between instance '{inst.name}' and '{node_path_map[normalized_path]}'"
                    )
                node_path_map[normalized_path] = inst.name

            for child in inst.children.values():
                _collect_namespaces(child)

        _collect_namespaces(self)

    def _finalize_parameters_recursive(self) -> None:
        """Recursively finalize all parameters in the instance tree."""

        def _finalize(instance) -> None:
            # If this is a node, resolve all its parameters
            if instance.entity_type == "node" and hasattr(instance, "parameter_manager"):
                instance.parameter_manager.resolve_all_parameters()

            # Recursively process children
            for child in instance.children.values():
                _finalize(child)

        _finalize(self)
