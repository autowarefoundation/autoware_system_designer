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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict, Union

from .domain import ParameterFileDefinition, ParameterValueDefinition, PortDefinition


class LaunchConfigDict(TypedDict, total=False):
    plugin: str
    executable: str
    ros2_launch_file: str
    node_output: str
    args: str
    container_name: str
    type: str
    launch_state: str


class ProcessDict(TypedDict, total=False):
    name: str
    trigger_conditions: List[Any]
    outcomes: List[Any]


class InstanceRefDict(TypedDict, total=False):
    name: str
    entity: str


class ComponentDict(TypedDict, total=False):
    name: str
    entity: str
    path: str
    compute_unit: str
    parameter_set: Union[str, List[str]]


class ArgumentDict(TypedDict, total=False):
    name: str


class ModeDict(TypedDict, total=False):
    name: str
    description: str
    default: bool


class VariableDict(TypedDict, total=False):
    name: str
    value: Any
    default: Any


class VariableFileDict(TypedDict, total=False):
    name: str
    path: str


class NodeGroupDict(TypedDict, total=False):
    name: str
    type: str
    nodes: List[str]


class ParameterSetItemDict(TypedDict, total=False):
    node: str
    param_values: List[Dict[str, Any]]


class ConfigType:
    """Constants for entity types."""

    NODE = "node"
    MODULE = "module"
    PARAMETER_SET = "parameter_set"
    SYSTEM = "system"

    @classmethod
    def get_all_types(cls) -> List[str]:
        """Get all valid entity types."""
        return [cls.NODE, cls.MODULE, cls.PARAMETER_SET, cls.SYSTEM]


class ConfigSubType:
    """Constants for entity sub-types."""

    # For SYSTEM
    BASE = "base"
    VARIANT = "variant"

    @classmethod
    def get_all_sub_types(cls) -> List[str]:
        return [cls.BASE, cls.VARIANT]


@dataclass
class Config:
    """Pure data structure for entity configuration."""

    name: str
    full_name: str
    entity_type: str
    config: Dict[str, Any]
    file_path: Path
    source_map: Optional[Dict[str, Dict[str, int]]] = None
    package: Optional[str] = None
    sub_type: Optional[str] = None

    def __post_init__(self):
        """Ensure file_path is a Path object."""
        if isinstance(self.file_path, str):
            self.file_path = Path(self.file_path)


@dataclass
class NodeConfig(Config):
    """Data structure for node entities."""

    base: Optional[str] = None  # Parent entity name for variants
    package_name: Optional[str] = None
    package_provider: Optional[str] = None
    package_resolution: Optional[str] = None  # "source" or "installed", set from workspace.yaml
    launch: Optional[LaunchConfigDict] = None
    inputs: List[PortDefinition] = None
    outputs: List[PortDefinition] = None
    param_files: Optional[List[ParameterFileDefinition]] = None
    param_values: Optional[List[ParameterValueDefinition]] = None
    processes: Optional[List[ProcessDict]] = None


@dataclass
class ModuleConfig(Config):
    """Data structure for module entities."""

    base: Optional[str] = None  # Parent entity name for variants
    instances: Optional[List[InstanceRefDict]] = None
    inputs: List[PortDefinition] = None
    outputs: List[PortDefinition] = None
    connections: Optional[List[Any]] = None


@dataclass
class ParameterSetConfig(Config):
    """Data structure for parameter set entities."""

    parameters: Optional[List[ParameterSetItemDict]] = None
    local_variables: Optional[List[VariableDict]] = None


@dataclass
class SystemConfig(Config):
    """Data structure for system entities."""

    base: Optional[str] = None  # Parent entity name for variants
    arguments: Optional[List[ArgumentDict]] = None
    modes: Optional[List[ModeDict]] = None
    mode_configs: Optional[Dict[str, Dict[str, Any]]] = None  # Mode-specific overrides/removals
    parameter_sets: Optional[List[str]] = None  # System-level parameter sets
    components: Optional[List[ComponentDict]] = None
    connections: Optional[List[Any]] = None
    variables: Optional[List[VariableDict]] = None
    variable_files: Optional[List[VariableFileDict]] = None
    node_groups: Optional[List[NodeGroupDict]] = None
