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

    def apply_override(self, override: Dict[str, Any]) -> None:
        """Merge launch override into this manager (e.g. from module instance config)."""
        self.launch_config.apply_override(override)

    @property
    def package_name(self) -> str:
        """Convenience access for code that expects launch_manager.package_name."""
        return self.launch_config.package_name

    def get_launcher_data(self, instance: "Instance") -> Dict[str, Any]:
        """Build full launcher dict for this node instance (for generation/serialization)."""
        from ..instances.instance_serializer import serialize_parameter_type

        cfg = self.launch_config
        resolved_args = instance.parameter_manager.resolve_substitutions(cfg.args)

        launcher_data: Dict[str, Any] = {
            "package": cfg.package_name,
            "ros2_launch_file": cfg.ros2_launch_file,
            "node_output": cfg.node_output,
            "args": resolved_args,
            "launch_state": cfg.launch_state.value,
        }

        if cfg.launch_state != LaunchState.ROS2_LAUNCH_FILE:
            launcher_data["plugin"] = cfg.plugin
            launcher_data["executable"] = cfg.executable
            launcher_data["container"] = cfg.container_name

        # Ports from instance (explicit remap = port.remap_target differs from default)
        in_ports = instance.link_manager.get_all_in_ports()
        out_ports = instance.link_manager.get_all_out_ports()
        remap_inputs_explicit = {
            port.name
            for port in in_ports
            if port.remap_target and port.remap_target != "~/input/" + port.name
        }
        remap_outputs_explicit = {
            port.name
            for port in out_ports
            if port.remap_target and port.remap_target != "~/output/" + port.name
        }
        ports = []
        for port in in_ports:
            if port.is_global and port.name not in remap_inputs_explicit:
                continue
            topic = port.get_topic()
            if not topic:
                continue
            ports.append(
                {
                    "direction": "input",
                    "name": port.name,
                    "topic": topic,
                    "remap_target": port.remap_target,
                }
            )
        for port in out_ports:
            if port.is_global and port.name not in remap_outputs_explicit:
                continue
            topic = port.get_topic()
            if not topic:
                continue
            ports.append(
                {
                    "direction": "output",
                    "name": port.name,
                    "topic": topic,
                    "remap_target": port.remap_target,
                }
            )
        launcher_data["ports"] = ports

        # param_values and param_files from instance
        param_values = []
        for param in instance.parameter_manager.get_parameters_for_launch():
            param_copy = dict(param)
            param_copy["parameter_type"] = serialize_parameter_type(param.get("parameter_type"))
            param_values.append(param_copy)
        launcher_data["param_values"] = param_values

        param_files = []
        for param_file in instance.parameter_manager.get_parameter_files_for_launch():
            param_file_copy = dict(param_file)
            param_file_copy["parameter_type"] = serialize_parameter_type(
                param_file.get("parameter_type")
            )
            param_files.append(param_file_copy)
        launcher_data["param_files"] = param_files

        return launcher_data
