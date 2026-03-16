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

from typing import TYPE_CHECKING, Any, Dict

from ..runtime.execution import LaunchConfig, LaunchState

if TYPE_CHECKING:
    from ..instances.instances import Instance


class LaunchManager:
    """Manages launch configuration for a node instance.

    Holds canonical launch config in a single LaunchConfig (runtime) object.
    Used for launcher generation and serialization instead of parsing
    instance.configuration.launch. Handles launch_override via apply_override().
    """

    def __init__(self, *, launch_config: LaunchConfig):
        self.launch_config = launch_config

    @classmethod
    def from_config(cls, config: Any) -> "LaunchManager":
        """Build LaunchManager from NodeConfig (config.launch and config.package_name)."""
        launch_config = LaunchConfig.from_config(config)
        return cls(launch_config=launch_config)

    def update(self, container_target: str = ""):
        """Update launch configuration with new container target and/or launch type."""
        if container_target:
            self.launch_config.container_target = container_target
            self.launch_config.launch_state = LaunchState.COMPOSABLE_NODE

    @property
    def package_name(self) -> str:
        """Convenience access for code that expects launch_manager.package_name."""
        return self.launch_config.package_name

    def get_launcher_data(self, instance: "Instance") -> Dict[str, Any]:
        """Build full launcher dict for this node instance (for generation/serialization)."""

        cfg = self.launch_config
        resolved_args = instance.parameter_manager.resolve_substitutions(cfg.args)

        launcher_data: Dict[str, Any] = {
            "package": cfg.package_name,
            "node_output": cfg.node_output,
            "args": resolved_args,
            "launch_state": cfg.launch_state.value,
        }

        # Set container name and launch-type-specific fields by launch state
        match cfg.launch_state:
            case LaunchState.ROS2_LAUNCH_FILE:
                launcher_data["ros2_launch_file"] = cfg.ros2_launch_file
            case LaunchState.NODE_CONTAINER:
                launcher_data["executable"] = cfg.executable
            case LaunchState.COMPOSABLE_NODE:
                launcher_data["container_target"] = cfg.container_target
                launcher_data["plugin"] = cfg.plugin
            case _:  # SINGLE_NODE
                launcher_data["executable"] = cfg.executable

        # Ports for topic remapping
        launcher_data["ports"] = instance.link_manager.get_all_remap_ports()

        # param_values and param_files from instance (template-ready: parameter_type as string)
        launcher_data["param_values"] = list(instance.parameter_manager.get_parameters_for_launch())
        launcher_data["param_files"] = list(instance.parameter_manager.get_parameter_files_for_launch())

        return launcher_data
