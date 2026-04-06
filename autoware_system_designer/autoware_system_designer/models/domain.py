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

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ParameterType(Enum):
    """Parameter type with priority ordering (lower value = lower priority).
    Used only for individual parameters, not parameter files.
    """

    GLOBAL = 0  # Global parameter (lowest priority)
    DEFAULT_FILE = 1  # Parameter loaded from default parameter file
    DEFAULT = 2  # Default parameter
    OVERRIDE_FILE = 3  # Parameter loaded from override parameter file
    OVERRIDE = 4  # Directly set override parameter
    MODE_FILE = 5  # Parameter loaded from mode parameter file
    MODE = 6  # Mode specific parameter (highest priority)


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


