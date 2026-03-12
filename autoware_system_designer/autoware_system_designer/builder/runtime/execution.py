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

from enum import Enum
from typing import Any, Dict, Optional


class LaunchState(str, Enum):
    """Launch mode for a node instance."""

    ROS2_LAUNCH_FILE = "ros2_launch_file"
    SINGLE_NODE = "single_node"
    COMPOSABLE_NODE = "composable_node"
    NODE_CONTAINER = "node_container"

    @classmethod
    def from_config(cls, launch: Optional[Dict[str, Any]]) -> "LaunchState":
        """Derive launch state from config dict (configuration.launch)."""
        if not launch:
            return cls.SINGLE_NODE
        if launch.get("ros2_launch_file") not in (None, ""):
            return cls.ROS2_LAUNCH_FILE
        if launch.get("type") == "node_container":
            return cls.NODE_CONTAINER
        if launch.get("use_container") is True or launch.get("type") == "composable_node":
            return cls.COMPOSABLE_NODE
        return cls.SINGLE_NODE


class LaunchConfig:
    """Canonical launch configuration for a node instance.

    Holds all launch-related fields; used by LaunchManager for launcher
    generation and serialization. Handles launch_override via apply_override().
    """

    def __init__(
        self,
        *,
        package_name: str = "",
        ros2_launch_file: Optional[str] = None,
        node_output: str = "screen",
        args: str = "",
        plugin: str = "",
        executable: str = "",
        use_container: bool = False,
        container_name: str = "default_container",
        launch_state: LaunchState = LaunchState.SINGLE_NODE,
        launch_type: Optional[str] = None,
    ):
        self.package_name = package_name
        self.ros2_launch_file = ros2_launch_file
        self.node_output = node_output
        self.args = args
        self.plugin = plugin
        self.executable = executable
        self.use_container = use_container
        self.container_name = container_name
        self.launch_state = launch_state
        self.launch_type = launch_type

    @classmethod
    def from_config(cls, config: Any) -> "LaunchConfig":
        """Build LaunchConfig from NodeConfig (config.launch and config.package_name)."""
        launch = getattr(config, "launch", None) or {}
        package_name = getattr(config, "package_name", None) or ""

        ros2_launch_file = launch.get("ros2_launch_file")
        node_output = launch.get("node_output", "screen")
        args = launch.get("args", "")
        plugin = launch.get("plugin", "")
        executable = launch.get("executable", "")
        use_container = launch.get("use_container", False)
        container_name = launch.get("container_name", "default_container")
        launch_state = LaunchState.from_config(launch)
        launch_type = launch.get("type")

        return cls(
            package_name=package_name,
            ros2_launch_file=ros2_launch_file,
            node_output=node_output,
            args=args,
            plugin=plugin,
            executable=executable,
            use_container=use_container,
            container_name=container_name,
            launch_state=launch_state,
            launch_type=launch_type,
        )

    def apply_override(self, override: Dict[str, Any]) -> None:
        """Merge launch override into this config (e.g. from module instance config)."""
        if not override:
            return
        if "ros2_launch_file" in override:
            self.ros2_launch_file = override["ros2_launch_file"]
        if "node_output" in override:
            self.node_output = override["node_output"]
        if "args" in override:
            self.args = override["args"]
        if "use_container" in override:
            self.use_container = override["use_container"]
        if "container_name" in override:
            self.container_name = override["container_name"]
        if "type" in override:
            self.launch_type = override["type"]
        self._update_launch_state()

    def _update_launch_state(self) -> None:
        """Set launch_state from current members."""
        if self.ros2_launch_file not in (None, ""):
            self.launch_state = LaunchState.ROS2_LAUNCH_FILE
        elif self.launch_type == "node_container":
            self.launch_state = LaunchState.NODE_CONTAINER
        elif self.use_container or self.launch_type == "composable_node":
            self.launch_state = LaunchState.COMPOSABLE_NODE
        else:
            self.launch_state = LaunchState.SINGLE_NODE
