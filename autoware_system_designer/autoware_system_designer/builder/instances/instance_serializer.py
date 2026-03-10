from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

from ...models.system_structure import (
    SCHEMA_VERSION,
    EventData,
    InstanceData,
    PortData,
    SystemStructurePayload,
)

if TYPE_CHECKING:
    from .instances import Instance


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


def serialize_port(port) -> PortData:
    data = {
        "unique_id": port.unique_id,
        "name": port.name,
        "msg_type": port.msg_type,
        "namespace": port.namespace,
        "topic": port.topic,
        "is_global": port.is_global,
        "port_path": port.port_path,
        "event": serialize_event(port.event),
    }

    # Add connected_ids for graph traversal
    connected_ids = []
    if hasattr(port, "servers"):  # InPort
        connected_ids = [p.unique_id for p in port.servers]
    elif hasattr(port, "users"):  # OutPort
        connected_ids = [p.unique_id for p in port.users]
    data["connected_ids"] = connected_ids

    return data


def serialize_parameter_type(param_type) -> str:
    if hasattr(param_type, "name"):
        return param_type.name
    return str(param_type)


def collect_launcher_data(instance: "Instance") -> Dict[str, Any]:
    """Collect node data required for launcher generation."""
    if instance.entity_type != "node" or not instance.configuration:
        return {}

    if getattr(instance, "launch_manager", None) is not None:
        return instance.launch_manager.get_launcher_data(instance)

    return {}


def collect_instance_data(instance: "Instance") -> InstanceData:
    data = {
        "name": instance.name,
        "unique_id": instance.unique_id,
        "entity_type": instance.entity_type,
        "namespace": instance.namespace,
        "namespace_str": instance.namespace_str,
        "compute_unit": instance.compute_unit,
        "vis_guide": instance.vis_guide,
        "in_ports": [serialize_port(p) for p in instance.link_manager.get_all_in_ports()],
        "out_ports": [serialize_port(p) for p in instance.link_manager.get_all_out_ports()],
        "children": (
            [collect_instance_data(child) for child in instance.children.values()]
            if hasattr(instance, "children")
            else []
        ),
        "links": (
            [
                {
                    "unique_id": link.unique_id,
                    "from_port": serialize_port(link.from_port),
                    "to_port": serialize_port(link.to_port),
                    "msg_type": link.msg_type,
                    "topic": link.topic,
                }
                for link in instance.link_manager.get_all_links()
            ]
            if hasattr(instance.link_manager, "links")
            else []
        ),
        "events": [serialize_event(e) for e in instance.event_manager.get_all_events()],
        "parameters": [
            {
                "name": p.name,
                "value": p.value,
                "type": p.data_type,
                "parameter_type": serialize_parameter_type(p.parameter_type),
            }
            for p in instance.parameter_manager.get_all_parameters()
        ],
    }

    if instance.entity_type == "node":
        data["package"] = (
            instance.launch_manager.package_name
            if getattr(instance, "launch_manager", None) is not None
            else (instance.configuration.package_name or "")
        )
        data["param_files_all"] = [
            {
                "name": pf.name,
                "path": pf.path,
                "allow_substs": pf.allow_substs,
                "is_override": pf.is_override,
                "parameter_type": serialize_parameter_type(pf.parameter_type),
            }
            for pf in instance.parameter_manager.get_all_parameter_files()
        ]
        data["launcher"] = collect_launcher_data(instance)

    return data


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
