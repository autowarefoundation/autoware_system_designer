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

from typing import Any, Dict, List, Literal, Optional, TypedDict

# Version for the on-disk system structure JSON payload.
SCHEMA_VERSION = "1.0"


class EventData(TypedDict, total=False):
    unique_id: str
    name: str
    type: str
    process_event: bool
    frequency: Optional[float]
    warn_rate: Optional[float]
    error_rate: Optional[float]
    timeout: Optional[float]
    trigger_ids: List[str]
    action_ids: List[str]


class PortData(TypedDict, total=False):
    unique_id: str
    name: str
    msg_type: str
    namespace: List[str]
    topic: List[str]
    is_global: bool
    remap_target: Optional[str]
    port_path: str
    event: Optional[EventData]
    connected_ids: List[str]


class ParameterData(TypedDict, total=False):
    name: str
    value: Any
    type: str
    parameter_type: str
    source: Optional[Dict[str, Any]]


class ParameterFileData(TypedDict, total=False):
    name: str
    path: str
    allow_substs: bool
    is_override: bool
    parameter_type: str
    source: Optional[Dict[str, Any]]


class LauncherPortData(TypedDict, total=False):
    direction: Literal["input", "output"]
    name: str
    topic: str
    remap_target: Optional[str]


class LauncherParamValueData(TypedDict, total=False):
    name: str
    value: Any
    parameter_type: str


class LauncherParamFileData(TypedDict, total=False):
    name: str
    path: str
    allow_substs: bool
    parameter_type: str


class LauncherData(TypedDict, total=False):
    package: str
    ros2_launch_file: Optional[str]
    node_output: str
    args: str
    launch_state: str  # "ros2_launch_file" | "single_node" | "composable_node" | "node_container"
    plugin: str
    executable: str
    container: str
    ports: List[LauncherPortData]
    param_values: List[LauncherParamValueData]
    param_files: List[LauncherParamFileData]


class LinkData(TypedDict, total=False):
    unique_id: str
    from_port: PortData
    to_port: PortData
    msg_type: Optional[str]
    topic: Optional[str]


class InstanceData(TypedDict, total=False):
    name: str
    unique_id: str
    entity_type: str
    namespace: str
    resolved_path: str
    path: str
    compute_unit: Optional[str]
    vis_guide: Optional[Dict[str, Any]]
    source_file: Optional[str]
    in_ports: List[PortData]
    out_ports: List[PortData]
    children: List["InstanceData"]
    links: List[LinkData]
    events: List[Optional[EventData]]
    parameters: List[ParameterData]

    package: str
    parameter_files_all: List[ParameterFileData]
    launcher: LauncherData


class SystemStructureMetadata(TypedDict, total=False):
    system_name: str
    mode: str
    generated_at: str
    step: str
    error: Dict[str, str]


class SystemStructurePayload(TypedDict):
    schema_version: str
    metadata: SystemStructureMetadata
    data: InstanceData


DeploymentDataByMode = Dict[str, InstanceData]
