"""Resolve inter-component topic connections from remap entries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .grouper import ComponentGroup
from .launch_parser import NodeRecord, RemapEntry

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

_INFRA_PREFIXES = (
    "/diagnostics",
)


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
    component: str   # component name in system.yaml
    port: str        # port name (relative to the component's module)
    node_path: str   # full ROS node path


@dataclass
class TopicConnection:
    publisher: PortRef
    subscriber: PortRef
    topic: str       # resolved ROS topic

    def as_system_yaml_pair(self) -> tuple[str, str]:
        return (
            f"{self.publisher.component}.publisher.{self.publisher.port}",
            f"{self.subscriber.component}.subscriber.{self.subscriber.port}",
        )


def _resolved_topic(node: NodeRecord, remap: RemapEntry) -> str:
    """Return the resolved absolute topic from a remap entry."""
    topic = remap.to_topic
    if not topic.startswith("/"):
        # Relative topic – resolve against node namespace
        ns = node.namespace.rstrip("/")
        topic = f"{ns}/{topic}"
    return topic


def resolve_connections(groups: list[ComponentGroup]) -> list[TopicConnection]:
    """Find all cross-component topic connections.

    For each node remap, we record which component publishes or subscribes to
    each absolute topic.  Topics that are published by one component and
    subscribed by a different component become system-level connections.
    """
    # topic → list of (PortRef, direction)
    publishers: dict[str, list[PortRef]] = defaultdict(list)
    subscribers: dict[str, list[PortRef]] = defaultdict(list)

    for group in groups:
        for node in group.nodes:
            for remap in node.remaps:
                direction = remap.direction
                if direction == "unknown":
                    continue
                port = remap.port_name(node.namespace)
                if not port:
                    continue
                topic = _resolved_topic(node, remap)
                if _is_infra(topic):
                    continue

                ref = PortRef(
                    component=group.name,
                    port=port,
                    node_path=node.full_path,
                )
                if direction == "output":
                    publishers[topic].append(ref)
                else:
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
                connections.append(
                    TopicConnection(publisher=pub, subscriber=sub, topic=topic)
                )

    return connections


# ---------------------------------------------------------------------------
# Per-module interface extraction
# ---------------------------------------------------------------------------

@dataclass
class ModuleInterface:
    """External ports and internal connections for a single ComponentGroup."""
    publishers: list[tuple[str, str]]     # (port_name, topic)
    subscribers: list[tuple[str, str]]    # (port_name, topic)
    internal_connections: list[tuple[str, str, str, str]]  # (pub_node, pub_port, sub_node, sub_port)


def extract_module_interfaces(
    group: ComponentGroup,
    all_groups: list[ComponentGroup],
) -> ModuleInterface:
    """Compute the external and internal interface for a ComponentGroup."""
    # Build the set of topics that cross group boundaries
    all_pub: dict[str, list[PortRef]] = defaultdict(list)  # topic → publishers
    all_sub: dict[str, list[PortRef]] = defaultdict(list)  # topic → subscribers

    for g in all_groups:
        for node in g.nodes:
            for remap in node.remaps:
                if remap.direction == "unknown":
                    continue
                port = remap.port_name(node.namespace)
                if not port:
                    continue
                topic = _resolved_topic(node, remap)
                if _is_infra(topic):
                    continue
                ref = PortRef(component=g.name, port=port, node_path=node.full_path)
                if remap.direction == "output":
                    all_pub[topic].append(ref)
                else:
                    all_sub[topic].append(ref)

    external_pub_ports: list[tuple[str, str]] = []   # (port, topic)
    external_sub_ports: list[tuple[str, str]] = []

    seen_pub: set[str] = set()
    seen_sub: set[str] = set()

    for node in group.nodes:
        for remap in node.remaps:
            if remap.direction == "unknown":
                continue
            port = remap.port_name(node.namespace)
            if not port:
                continue
            topic = _resolved_topic(node, remap)
            if _is_infra(topic):
                continue

            if remap.direction == "output":
                # Is this topic consumed by any OTHER group?
                consumers = [r for r in all_sub.get(topic, []) if r.component != group.name]
                if consumers and port not in seen_pub:
                    seen_pub.add(port)
                    external_pub_ports.append((port, topic))
            else:
                # Is this topic produced by any OTHER group?
                producers = [r for r in all_pub.get(topic, []) if r.component != group.name]
                if producers and port not in seen_sub:
                    seen_sub.add(port)
                    external_sub_ports.append((port, topic))

    # Internal connections: same-group publisher → same-group subscriber
    internal: list[tuple[str, str, str, str]] = []
    seen_internal: set[tuple] = set()

    node_by_path = {n.full_path: n for n in group.nodes}

    for topic in set(list(all_pub.keys()) + list(all_sub.keys())):
        pub_refs = [r for r in all_pub.get(topic, []) if r.component == group.name]
        sub_refs = [r for r in all_sub.get(topic, []) if r.component == group.name]
        for pub_ref in pub_refs:
            for sub_ref in sub_refs:
                if pub_ref.node_path == sub_ref.node_path:
                    continue
                key = (pub_ref.node_path, pub_ref.port, sub_ref.node_path, sub_ref.port)
                if key in seen_internal:
                    continue
                seen_internal.add(key)
                pub_node = node_by_path.get(pub_ref.node_path)
                sub_node = node_by_path.get(sub_ref.node_path)
                if pub_node and sub_node:
                    internal.append((
                        pub_node.instance_name, pub_ref.port,
                        sub_node.instance_name, sub_ref.port,
                    ))

    return ModuleInterface(
        publishers=external_pub_ports,
        subscribers=external_sub_ports,
        internal_connections=internal,
    )
