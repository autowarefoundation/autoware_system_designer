"""Resolve inter-component topic connections from remap entries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .launch_parser import RemapEntry  # noqa: F401 — used by callers via star-import
from .namespace_tree import NamespaceNode

# Topics that carry no domain-level information
_INFRA_EXACT = {
    "/rosout",
    "/parameter_events",
    "/clock",
    "/tf",
    "/tf_static",
    "/diagnostics",
    "/diagnostics_agg",
    "/diagnostics_toplevel_state",
}

_INFRA_SUFFIXES = (
    "/rosout",
    "/parameter_events",
    "/_supported_types",
)

_INFRA_PREFIXES = ("/diagnostics",)


def _is_infra(topic: str) -> bool:
    if topic in _INFRA_EXACT:
        return True
    for s in _INFRA_SUFFIXES:
        if topic.endswith(s):
            return True
    for p in _INFRA_PREFIXES:
        if topic.startswith(p):
            return True
    return False


@dataclass
class PortRef:
    component: str  # component name in system.yaml
    port: str  # port name (relative to the component's module)
    node_path: str  # full ROS node path


@dataclass
class TopicConnection:
    publisher: PortRef
    subscriber: PortRef
    topic: str  # resolved ROS topic

    def as_system_yaml_pair(self) -> tuple[str, str]:
        return (
            f"{self.publisher.component}.publisher.{self.publisher.port}",
            f"{self.subscriber.component}.subscriber.{self.subscriber.port}",
        )


def _resolved_topic(node, remap) -> str:
    """Return the resolved absolute topic from a remap entry."""
    topic = remap.to_topic
    if not topic.startswith("/"):
        ns = node.namespace.rstrip("/")
        topic = f"{ns}/{topic}"
    return topic


def resolve_connections(top_nodes: list[NamespaceNode]) -> list[TopicConnection]:
    """Find all cross-component topic connections between top-level NamespaceNodes.

    For each node remap, records which component publishes or subscribes to
    each absolute topic.  Topics published by one component and subscribed by
    a different component become system-level connections in system.yaml.
    """
    publishers: dict[str, list[PortRef]] = defaultdict(list)
    subscribers: dict[str, list[PortRef]] = defaultdict(list)

    for ns_node in top_nodes:
        for node in ns_node.all_nodes:
            for remap in node.remaps:
                direction = remap.direction
                if direction == "unknown":
                    continue
                topic = _resolved_topic(node, remap)
                if _is_infra(topic):
                    continue

                if direction == "output":
                    port = remap.port_name(node.namespace, group_namespace=ns_node.namespace)
                    if not port:
                        continue
                    ref = PortRef(component=ns_node.name, port=port, node_path=node.full_path)
                    publishers[topic].append(ref)
                else:
                    canonical = topic.lstrip("/")
                    if any(r.component == ns_node.name for r in subscribers[topic]):
                        continue
                    ref = PortRef(component=ns_node.name, port=canonical, node_path=node.full_path)
                    subscribers[topic].append(ref)

    connections: list[TopicConnection] = []
    seen: set[tuple] = set()

    for topic, pubs in publishers.items():
        subs = subscribers.get(topic, [])
        for pub in pubs:
            for sub in subs:
                if pub.component == sub.component:
                    continue  # internal – handled in module.yaml
                key = (pub.component, pub.port, sub.component, sub.port)
                if key in seen:
                    continue
                seen.add(key)
                connections.append(TopicConnection(publisher=pub, subscriber=sub, topic=topic))

    return connections


@dataclass
class ModuleInterface:
    """External ports and internal connections for a single namespace level."""

    publishers: list[tuple[str, str]]  # (port_name, topic)
    subscribers: list[tuple[str, str]]  # (port_name, topic)
    internal_connections: list[tuple[str, str, str, str]]  # (pub_node_path, pub_port, sub_node_path, sub_port)
