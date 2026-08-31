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

"""Projection of the in-memory Instance graph to the system-structure payload.

Field-level serialization is generic (model.serde reads the record schema off
the model dataclasses); this module owns only what a record cannot know about
itself: boundary-relative direction (is_outward) and tree assembly across the
instance's managers.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from autoware_system_designer.model.export_schema import (
    SCHEMA_VERSION,
    EventData,
    InstanceData,
    ParameterData,
    ParameterFileData,
    PortData,
    SystemStructurePayload,
)
from autoware_system_designer.model.serde import dump

if TYPE_CHECKING:
    from autoware_system_designer.builder.instances import Instance


def serialize_event(event) -> EventData | None:
    if not event:
        return None
    return dump(event)


def serialize_port(port, is_outward: bool = True) -> PortData:
    data = dump(port)
    # Base ports carry no server/user references.
    data.setdefault("connected_ids", [])
    data["is_outward"] = is_outward
    return data


def collect_launcher_data(instance: "Instance") -> Dict[str, Any]:
    """Collect node data required for launcher generation."""
    if instance.entity_type != "node":
        return {}

    if getattr(instance, "launch_manager", None) is not None:
        return instance.launch_manager.get_launcher_data(instance)

    return {}


def collect_instance_data(instance: "Instance") -> InstanceData:
    """Assemble the instance tree payload from the instance and its managers."""
    data: InstanceData = {
        "name": instance.name,
        "unique_id": instance.unique_id,
        "entity_type": instance.entity_type,
        "namespace": instance.namespace.to_string(),
        "path": instance.path,
        "compute_unit": instance.compute_unit,
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
    """Collect and serialize all links; port direction is boundary-relative."""
    if not hasattr(instance.link_manager, "links"):
        return []

    boundary_path = instance.resolved_path
    result = []
    for link in instance.link_manager.get_all_links():
        data = dump(link)
        data["from_port"] = serialize_port(link.from_port, is_outward=(link.from_port.namespace == boundary_path))
        data["to_port"] = serialize_port(link.to_port, is_outward=(link.to_port.namespace == boundary_path))
        result.append(data)
    return result


def _collect_events(instance: "Instance") -> list[EventData | None]:
    """Collect and serialize all events."""
    return [serialize_event(e) for e in instance.event_manager.get_all_events()]


def _collect_parameters(instance: "Instance") -> list[ParameterData]:
    """Collect and serialize all parameters."""
    return [dump(p) for p in instance.parameter_manager.get_all_parameters()]


def _collect_parameter_files(instance: "Instance") -> list[ParameterFileData]:
    """Collect and serialize all parameter files."""
    return [dump(pf) for pf in instance.parameter_manager.get_all_parameter_files()]


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
