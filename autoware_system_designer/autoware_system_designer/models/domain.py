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

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ConnectionType(Enum):
    UNDEFINED = 0
    EXTERNAL_TO_INTERNAL = 1
    INTERNAL_TO_INTERNAL = 2
    INTERNAL_TO_EXTERNAL = 3


class LaunchState(Enum):
    ROS2_LAUNCH_FILE = "ros2_launch_file"
    SINGLE_NODE = "single_node"
    COMPOSABLE_NODE = "composable_node"
    NODE_CONTAINER = "node_container"


class ParameterType(Enum):
    GLOBAL = 0
    DEFAULT_FILE = 1
    DEFAULT = 2
    OVERRIDE_FILE = 3
    OVERRIDE = 4
    MODE_FILE = 5
    MODE = 6


@dataclass
class PortDefinition:
    """Typed definition for a node or module input/output port."""

    name: str
    port_role: str  # "subscriber" | "client" | "publisher" | "server"
    message_type: Optional[str] = None
    remap_target: Optional[str] = None
    global_topic: Optional[str] = None  # global ROS topic override (was "global" key)

    @classmethod
    def from_dict(cls, d: dict) -> PortDefinition:
        return cls(
            name=d["name"],
            port_role=d.get("port_role", "subscriber"),
            message_type=d.get("message_type"),
            remap_target=d.get("remap_target"),
            global_topic=d.get("global"),
        )


@dataclass
class ParameterFileDefinition:
    """Typed definition for a parameter file reference."""

    name: str
    path: str  # file path (was "value" or "default" key in YAML)
    allow_substs: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> ParameterFileDefinition:
        path = d.get("value") or d.get("default") or ""
        return cls(
            name=d["name"],
            path=path,
            allow_substs=bool(d.get("allow_substs", False)),
        )


@dataclass
class ParameterValueDefinition:
    """Typed definition for an inline parameter value."""

    name: str
    value: Any  # resolved value (from "value" or "default" in YAML)
    type: str = "string"

    @classmethod
    def from_dict(cls, d: dict) -> ParameterValueDefinition:
        value = d["value"] if "value" in d else d.get("default")
        return cls(
            name=d["name"],
            value=value,
            type=d.get("type", "string"),
        )


@dataclass
class Port:
    """Runtime port instance (communication endpoint)."""

    name: str
    msg_type: str
    namespace: List[str]
    remap_target: Optional[str] = None
    is_global: bool = False
    topic: List[str] = field(default_factory=list)
    reference: List[Port] = field(default_factory=list)


@dataclass
class InPort(Port):
    """Input port (subscriber/client)."""

    is_required: bool = False
    servers: List[Port] = field(default_factory=list)


@dataclass
class OutPort(Port):
    """Output port (publisher/server)."""

    frequency: Optional[float] = None
    is_monitored: bool = False
    users: List[Port] = field(default_factory=list)


@dataclass
class Link:
    """Connection between two ports."""

    msg_type: str
    from_port: Port
    to_port: Port
    namespace: List[str] = field(default_factory=list)
    connection_type: ConnectionType = ConnectionType.UNDEFINED


@dataclass
class Event:
    """Runtime event that triggers processes."""

    name: str
    namespace: List[str]
    type: str
    frequency: Optional[float] = None
    warn_rate: Optional[float] = None
    error_rate: Optional[float] = None
    timeout: Optional[float] = None
    triggers: List[Event] = field(default_factory=list)
    actions: List[Event] = field(default_factory=list)
    process_event: bool = False


@dataclass
class LaunchConfig:
    """Node launch configuration."""

    package_name: str
    executable: str
    launch_state: LaunchState
    ros2_launch_file: Optional[str] = None
    node_output: str = "screen"
    args: str = ""
    plugin: str = ""
    container_target: str = ""


@dataclass
class Parameter:
    """Runtime parameter instance with resolved value."""

    name: str
    value: Any
    data_type: str = "string"
    schema_path: Optional[str] = None
    allow_substs: bool = False
    parameter_type: ParameterType = ParameterType.DEFAULT
    source: Optional[Dict[str, Any]] = None


@dataclass
class ParameterFile:
    """Runtime parameter file reference."""

    name: str
    path: str
    schema_path: Optional[str] = None
    allow_substs: bool = False
    is_override: bool = False
    parameter_type: ParameterType = ParameterType.DEFAULT_FILE
    source: Optional[Dict[str, Any]] = None
