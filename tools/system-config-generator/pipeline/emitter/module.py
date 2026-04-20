"""Tree-mode module YAML emitter."""

from __future__ import annotations

from collections import defaultdict

from ..connection_resolver import (
    ModuleInterface,
    PortRef,
    _is_infra,
    _resolved_topic,
)
from ..launch_parser import NodeRecord
from ..namespace_tree import NamespaceNode

DESIGN_FORMAT = "0.3.1"


def _build_instance_name_map(nodes: list[NodeRecord]) -> dict[str, str]:
    """Return {node.full_path: deduplicated_instance_name}."""
    used: dict[str, int] = {}
    result: dict[str, str] = {}
    for node in nodes:
        base = node.instance_name
        if base in used:
            used[base] += 1
            name = f"{base}_{used[base]}"
        else:
            used[base] = 1
            name = base
        result[node.full_path] = name
    return result


def _node_to_instance_entity(node: NodeRecord) -> str:
    if node.plugin:
        short = node.plugin.split("::")[-1]
        return f"{short}.node"
    raw = node.exec or node.name
    pascal = "".join(p.capitalize() for p in raw.replace("-", "_").split("_"))
    return f"{pascal}.node"


def _collect_all_pub_sub(
    root_nodes: list[NamespaceNode],
) -> tuple[dict[str, list[PortRef]], dict[str, list[PortRef]]]:
    """Walk all NamespaceNodes and collect per-topic publishers/subscribers."""
    all_pub: dict[str, list[PortRef]] = defaultdict(list)
    all_sub: dict[str, list[PortRef]] = defaultdict(list)

    def _walk(ns_node: NamespaceNode) -> None:
        for node in ns_node.direct_nodes:
            for remap in node.remaps:
                if remap.direction == "unknown":
                    continue
                port = remap.port_name(node.namespace, group_namespace=ns_node.namespace)
                if not port:
                    continue
                topic = _resolved_topic(node, remap)
                if _is_infra(topic):
                    continue
                ref = PortRef(component=ns_node.namespace, port=port, node_path=node.full_path)
                if remap.direction == "output":
                    all_pub[topic].append(ref)
                else:
                    all_sub[topic].append(ref)
        for child in ns_node.children.values():
            _walk(child)

    for ns_node in root_nodes:
        _walk(ns_node)
    return dict(all_pub), dict(all_sub)


def _extract_ns_module_interface(
    ns_node: NamespaceNode,
    all_pub: dict[str, list[PortRef]],
    all_sub: dict[str, list[PortRef]],
) -> ModuleInterface:
    """Compute external ports and internal connections for one NamespaceNode level."""
    my_ns = ns_node.namespace

    external_pub: list[tuple[str, str]] = []
    external_sub: list[tuple[str, str]] = []
    seen_pub: set[str] = set()
    seen_sub_topics: set[str] = set()

    for node in ns_node.direct_nodes:
        for remap in node.remaps:
            if remap.direction == "unknown":
                continue
            topic = _resolved_topic(node, remap)
            if _is_infra(topic):
                continue
            if remap.direction == "output":
                port = remap.port_name(node.namespace, group_namespace=my_ns)
                if not port:
                    continue
                consumers = [r for r in all_sub.get(topic, [])
                             if r.component != my_ns and not r.component.startswith(my_ns + "/")]
                if consumers and port not in seen_pub:
                    seen_pub.add(port)
                    external_pub.append((port, topic))
            else:
                producers = [r for r in all_pub.get(topic, [])
                             if r.component != my_ns and not r.component.startswith(my_ns + "/")]
                if producers and topic not in seen_sub_topics:
                    seen_sub_topics.add(topic)
                    external_sub.append((topic.lstrip("/"), topic))

    for child in ns_node.children.values():
        child_ns = child.namespace
        for node in child.all_nodes:
            for remap in node.remaps:
                if remap.direction == "unknown":
                    continue
                topic = _resolved_topic(node, remap)
                if _is_infra(topic):
                    continue
                if remap.direction == "output":
                    port = remap.port_name(node.namespace, group_namespace=child_ns)
                    if not port:
                        continue
                    consumers = [r for r in all_sub.get(topic, [])
                                 if not r.component.startswith(my_ns)]
                    if consumers and port not in seen_pub:
                        seen_pub.add(port)
                        external_pub.append((port, topic))
                else:
                    producers = [r for r in all_pub.get(topic, [])
                                 if not r.component.startswith(my_ns)]
                    if producers and topic not in seen_sub_topics:
                        seen_sub_topics.add(topic)
                        external_sub.append((topic.lstrip("/"), topic))

    internal: list[tuple[str, str, str, str]] = []
    seen_internal: set[tuple] = set()

    for topic in set(list(all_pub.keys()) + list(all_sub.keys())):
        pubs = [r for r in all_pub.get(topic, [])
                if r.component == my_ns or r.component.startswith(my_ns + "/")]
        subs = [r for r in all_sub.get(topic, [])
                if r.component == my_ns or r.component.startswith(my_ns + "/")]
        for pub_ref in pubs:
            for sub_ref in subs:
                if pub_ref.node_path == sub_ref.node_path:
                    continue
                if pub_ref.component == sub_ref.component:
                    continue
                key = (pub_ref.node_path, pub_ref.port, sub_ref.node_path, sub_ref.port)
                if key in seen_internal:
                    continue
                seen_internal.add(key)
                internal.append((
                    pub_ref.node_path, pub_ref.port,
                    sub_ref.node_path, sub_ref.port,
                ))

    return ModuleInterface(
        publishers=external_pub,
        subscribers=external_sub,
        internal_connections=internal,
    )


def emit_module_yaml_from_tree(
    ns_node: NamespaceNode,
    all_pub: dict[str, list[PortRef]],
    all_sub: dict[str, list[PortRef]],
) -> str:
    """Generate a *.module.yaml for a NamespaceNode (recursive-aware)."""
    iface = _extract_ns_module_interface(ns_node, all_pub, all_sub)
    direct_name_map = _build_instance_name_map(ns_node.direct_nodes)
    entity_name = ns_node.entity_name

    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {entity_name}.module")
    lines.append("")

    lines.append("instances:")
    has_instances = ns_node.children or ns_node.direct_nodes
    if has_instances:
        for child_name, child in sorted(ns_node.children.items()):
            lines.append(f"  - name: {child.name}")
            lines.append(f"    entity: {child.entity_name}.module")
        for node in ns_node.direct_nodes:
            inst = direct_name_map[node.full_path]
            entity = _node_to_instance_entity(node)
            lines.append(f"  - name: {inst}")
            lines.append(f"    entity: {entity}")
    else:
        lines.append("  []")
    lines.append("")

    lines.append("subscribers:")
    if iface.subscribers:
        for port, topic in sorted(iface.subscribers):
            lines.append(f"  - name: {port}  # {topic}")
    else:
        lines.append("  []")
    lines.append("")

    lines.append("publishers:")
    if iface.publishers:
        for port, topic in sorted(iface.publishers):
            lines.append(f"  - name: {port}  # {topic}")
    else:
        lines.append("  []")
    lines.append("")

    lines.append("connections:")
    conn_lines = _build_tree_module_connections(ns_node, iface, direct_name_map, all_pub, all_sub)
    if conn_lines:
        lines.extend(conn_lines)
    else:
        lines.append("  []")

    return "\n".join(lines) + "\n"


def _inst_ref(node_path: str, ns_node: NamespaceNode, direct_name_map: dict[str, str]) -> str:
    """Return the instance reference string for a node path within this ns_node."""
    if node_path in direct_name_map:
        return direct_name_map[node_path]
    for child in ns_node.children.values():
        all_child_paths = {n.full_path for n in child.all_nodes}
        if node_path in all_child_paths:
            return child.name
    return node_path  # fallback


def _build_tree_module_connections(
    ns_node: NamespaceNode,
    iface: ModuleInterface,
    direct_name_map: dict[str, str],
    all_pub: dict[str, list[PortRef]],
    all_sub: dict[str, list[PortRef]],
) -> list[str]:
    lines: list[str] = []
    topic_to_sub_canonical: dict[str, str] = {topic: port for port, topic in iface.subscribers}
    pub_ports = {port for port, _ in iface.publishers}

    seen_ext_in: set[tuple] = set()
    seen_ext_out: set[tuple] = set()
    seen_internal: set[tuple] = set()

    for node in ns_node.direct_nodes:
        inst = direct_name_map[node.full_path]
        for remap in node.remaps:
            if remap.direction != "input":
                continue
            topic = _resolved_topic(node, remap)
            canonical = topic_to_sub_canonical.get(topic)
            if not canonical:
                continue
            node_port = remap.port_name(node.namespace, group_namespace=ns_node.namespace)
            if not node_port:
                continue
            key = (canonical, inst)
            if key not in seen_ext_in:
                seen_ext_in.add(key)
                lines.append(f"  - - subscriber.{canonical}")
                lines.append(f"    - {inst}.subscriber.{node_port}")

    for child in ns_node.children.values():
        for node in child.all_nodes:
            for remap in node.remaps:
                if remap.direction != "input":
                    continue
                topic = _resolved_topic(node, remap)
                canonical = topic_to_sub_canonical.get(topic)
                if not canonical:
                    continue
                key = (canonical, child.name)
                if key not in seen_ext_in:
                    seen_ext_in.add(key)
                    lines.append(f"  - - subscriber.{canonical}")
                    lines.append(f"    - {child.name}.subscriber.{canonical}")

    for node in ns_node.direct_nodes:
        inst = direct_name_map[node.full_path]
        for remap in node.remaps:
            if remap.direction != "output":
                continue
            port = remap.port_name(node.namespace, group_namespace=ns_node.namespace)
            if port and port in pub_ports:
                key = (inst, port)
                if key not in seen_ext_out:
                    seen_ext_out.add(key)
                    lines.append(f"  - - {inst}.publisher.{port}")
                    lines.append(f"    - publisher.{port}")

    for child in ns_node.children.values():
        for node in child.all_nodes:
            for remap in node.remaps:
                if remap.direction != "output":
                    continue
                port = remap.port_name(node.namespace, group_namespace=child.namespace)
                if port and port in pub_ports:
                    key = (child.name, port)
                    if key not in seen_ext_out:
                        seen_ext_out.add(key)
                        lines.append(f"  - - {child.name}.publisher.{port}")
                        lines.append(f"    - publisher.{port}")

    for pub_path, pub_port, sub_path, sub_port in iface.internal_connections:
        pub_inst = _inst_ref(pub_path, ns_node, direct_name_map)
        sub_inst = _inst_ref(sub_path, ns_node, direct_name_map)
        key = (pub_inst, pub_port, sub_inst, sub_port)
        if key not in seen_internal:
            seen_internal.add(key)
            lines.append(f"  - - {pub_inst}.publisher.{pub_port}")
            lines.append(f"    - {sub_inst}.subscriber.{sub_port}")

    return lines
