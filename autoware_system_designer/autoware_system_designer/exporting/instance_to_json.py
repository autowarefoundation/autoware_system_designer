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

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from ..building.runtime.parameters import parameter_type_to_str
from ..file_io.source_location import SourceLocation
from .schema import (
    SCHEMA_VERSION,
    EventData,
    InstanceData,
    ParameterData,
    ParameterFileData,
    PortData,
    SystemStructurePayload,
)

if TYPE_CHECKING:
    from ..building.instances import Instance


_SCIENTIFIC_NOTATION_PATTERN = re.compile(r"^[+-]?\d+(\.\d+)?[eE][+-]?\d+$")


def _float_to_decimal_str(value: float) -> str:
    """Convert a float to a decimal string without scientific notation."""
    s = repr(value)
    if "e" in s or "E" in s:
        return f"{value:.15f}".rstrip("0").rstrip(".")
    return s


def _resolve_scientific_notation(value: Any) -> Any:
    """Convert scientific notation to decimal string representation.

    Handles two cases:
    - float: Python's YAML parser converts unquoted '1e-3' to float 0.001,
      which json.dumps may re-serialize as '1e-05' for very small values.
    - str: quoted '1e-3' remains a string and must be expanded to '0.001'.
    """
    if isinstance(value, float):
        return _float_to_decimal_str(value)
    if isinstance(value, str) and _SCIENTIFIC_NOTATION_PATTERN.match(value.strip()):
        return _float_to_decimal_str(float(value))
    return value


def serialize_event(event) -> EventData | None:
    if not event:
        return None
    return {
        "unique_id": event.unique_id,
        "name": event.name,
        "type": event.type,
        "process_event": event.process_event,
        "frequency": event.frequency,
        "warn_rate": event.warn_rate,
        "error_rate": event.error_rate,
        "timeout": event.timeout,
        "trigger_ids": [t.unique_id for t in event.triggers],
        "action_ids": [a.unique_id for a in event.actions],
    }


def serialize_port(port, is_outward: bool = True) -> PortData:
    data = {
        "unique_id": port.unique_id,
        "name": port.name,
        "msg_type": port.msg_type,
        "namespace": port.namespace,
        "topic": port.topic,
        "is_global": port.is_global,
        "remap_target": port.remap_target,
        "port_path": port.port_path,
        "event": serialize_event(port.event),
        "is_outward": is_outward,
    }

    # Add connected_ids for graph traversal
    connected_ids = []
    if hasattr(port, "servers"):  # InPort
        connected_ids = [p.unique_id for p in port.servers]
    elif hasattr(port, "users"):  # OutPort
        connected_ids = [p.unique_id for p in port.users]
    data["connected_ids"] = connected_ids

    return data


def serialize_source(source: SourceLocation | None) -> Dict[str, Any] | None:
    if source is None:
        return None

    return {
        "file_path": str(source.file_path) if source.file_path is not None else None,
        "yaml_path": source.yaml_path,
        "line": source.line,
        "column": source.column,
    }


def collect_launcher_data(instance: "Instance") -> Dict[str, Any]:
    """Collect node data required for launcher generation."""
    if instance.entity_type != "node":
        return {}

    if getattr(instance, "launch_manager", None) is not None:
        data = instance.launch_manager.get_launcher_data(instance)
        for param in data.get("param_values", []):
            param["value"] = _resolve_scientific_notation(param["value"])
        return data

    return {}


def collect_instance_data(instance: "Instance") -> InstanceData:
    """Convert Instance to InstanceData TypedDict.

    Explicit conversion of in-memory Instance graph to typed JSON structure.
    Ensures all InstanceData fields are populated with appropriate defaults.
    """
    data: InstanceData = {
        "name": instance.name,
        "unique_id": instance.unique_id,
        "entity_type": instance.entity_type,
        "namespace": instance.namespace.to_string(),
        "path": instance.path,
        "compute_unit": instance.compute_unit,
        "vis_guide": instance.vis_guide,
        "source_file": instance.source_file,
        "in_ports": _collect_in_ports(instance),
        "out_ports": _collect_out_ports(instance),
        "children": _collect_children(instance),
        "links": _collect_links(instance),
        "events": _collect_events(instance),
        "parameters": _collect_parameters(instance),
    }

    if instance.entity_type == "node":
        data["package"] = instance.launch_manager.package_name
        data["parameter_files_all"] = _collect_parameter_files(instance)
        data["launcher"] = collect_launcher_data(instance)

    return data


def _collect_in_ports(instance: "Instance") -> list[PortData]:
    """Collect and serialize all input ports."""
    return [serialize_port(p, is_outward=True) for p in instance.link_manager.get_all_in_ports()]


def _collect_out_ports(instance: "Instance") -> list[PortData]:
    """Collect and serialize all output ports."""
    return [serialize_port(p, is_outward=True) for p in instance.link_manager.get_all_out_ports()]


def _collect_children(instance: "Instance") -> list[InstanceData]:
    """Recursively collect child instance data."""
    if not hasattr(instance, "children"):
        return []
    return [collect_instance_data(child) for child in instance.children.values()]


def _collect_links(instance: "Instance") -> list[Dict[str, Any]]:
    """Collect and serialize all links."""
    if not hasattr(instance.link_manager, "links"):
        return []

    boundary_path = instance.resolved_path
    return [
        {
            "unique_id": link.unique_id,
            "from_port": serialize_port(link.from_port, is_outward=(link.from_port.namespace == boundary_path)),
            "to_port": serialize_port(link.to_port, is_outward=(link.to_port.namespace == boundary_path)),
            "msg_type": link.msg_type,
            "topic": link.topic,
            "connection_type": link.connection_type.name,
        }
        for link in instance.link_manager.get_all_links()
    ]


def _collect_events(instance: "Instance") -> list[EventData | None]:
    """Collect and serialize all events."""
    return [serialize_event(e) for e in instance.event_manager.get_all_events()]


def _collect_parameters(instance: "Instance") -> list[ParameterData]:
    """Collect and serialize all parameters."""
    return [
        ParameterData(
            name=p.name,
            value=_resolve_scientific_notation(p.value),
            type=p.data_type,
            parameter_type=parameter_type_to_str(p.parameter_type),
            source=serialize_source(p.source),
        )
        for p in instance.parameter_manager.get_all_parameters()
    ]


def _collect_parameter_files(instance: "Instance") -> list[ParameterFileData]:
    """Collect and serialize all parameter files."""
    return [
        ParameterFileData(
            name=pf.name,
            path=pf.path,
            allow_substs=pf.allow_substs,
            is_override=pf.is_override,
            parameter_type=parameter_type_to_str(pf.parameter_type),
            source=serialize_source(pf.source),
        )
        for pf in instance.parameter_manager.get_all_parameter_files()
    ]


def collect_system_structure(instance: "Instance", system_name: str, mode: str) -> SystemStructurePayload:
    """Collect instance data with schema/version metadata for JSON handover."""
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "system_name": system_name,
            "mode": mode,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "data": collect_instance_data(instance),
    }
