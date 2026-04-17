"""Group parsed nodes into ComponentGroups by top-level namespace."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .launch_parser import ContainerRecord, NodeRecord


@dataclass
class ComponentGroup:
    name: str           # system.yaml component name  (e.g. "sensing")
    namespace: str      # top-level namespace          (e.g. "/sensing")
    entity_name: str    # module entity name           (e.g. "Sensing")
    nodes: list[NodeRecord] = field(default_factory=list)
    containers: list[ContainerRecord] = field(default_factory=list)


def _top_ns(namespace: str, depth: int) -> str:
    parts = [p for p in namespace.strip("/").split("/") if p]
    if not parts:
        return "(root)"
    return "/" + "/".join(parts[:depth])


def _ns_to_name(namespace: str) -> str:
    return namespace.strip("/").replace("/", "_") or "root"


def _name_to_pascal(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


def group_nodes(
    nodes: list[NodeRecord],
    containers: list[ContainerRecord],
    depth: int = 1,
    overrides: Optional[dict] = None,
) -> list[ComponentGroup]:
    """Group nodes and containers into ComponentGroups.

    Args:
        nodes: all NodeRecords from parse_launch_xml
        containers: all ContainerRecords from parse_launch_xml
        depth: namespace depth used for grouping (1 = top-level)
        overrides: dict mapping namespace → {"name": ..., "entity_name": ...}
    """
    overrides = overrides or {}
    groups: dict[str, ComponentGroup] = {}

    def get_or_create(ns_key: str) -> ComponentGroup:
        if ns_key in groups:
            return groups[ns_key]
        if ns_key in overrides:
            g = ComponentGroup(
                name=overrides[ns_key].get("name", _ns_to_name(ns_key)),
                namespace=ns_key,
                entity_name=overrides[ns_key].get("entity_name", _name_to_pascal(_ns_to_name(ns_key))),
            )
        else:
            name = _ns_to_name(ns_key)
            g = ComponentGroup(
                name=name,
                namespace=ns_key,
                entity_name=_name_to_pascal(name),
            )
        groups[ns_key] = g
        return g

    for node in nodes:
        key = _top_ns(node.namespace, depth)
        get_or_create(key).nodes.append(node)

    for container in containers:
        key = _top_ns(container.namespace, depth)
        if key in groups:
            get_or_create(key).containers.append(container)
        else:
            # Root-level containers (e.g. /pointcloud_container) – attach to
            # the group that matches most of the member nodes' namespaces.
            # We defer this to node_groups resolution in the emitter.
            get_or_create(key).containers.append(container)

    # Stable sort by namespace for deterministic output
    return sorted(groups.values(), key=lambda g: g.namespace)
