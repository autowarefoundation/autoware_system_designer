from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict, Literal


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
    port_path: str
    event: Optional[EventData]
    connected_ids: List[str]


class ParameterData(TypedDict, total=False):
    name: str
    value: Any
    type: str
    parameter_type: str


class ParameterFileData(TypedDict, total=False):
    name: str
    path: str
    allow_substs: bool
    is_override: bool
    parameter_type: str


class LauncherPortData(TypedDict, total=False):
    direction: Literal["input", "output"]
    name: str
    topic: str
    remap_target: Optional[str]


class LauncherData(TypedDict, total=False):
    package: str
    ros2_launch_file: Optional[str]
    node_output: str
    args: str
    is_ros2_file_launch: bool
    plugin: str
    executable: str
    use_container: bool
    container: str
    ports: List[LauncherPortData]
    param_values: List[Dict[str, Any]]
    param_files: List[Dict[str, Any]]


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
    namespace: List[str]
    namespace_str: str
    compute_unit: Optional[str]
    vis_guide: Optional[Dict[str, Any]]
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
