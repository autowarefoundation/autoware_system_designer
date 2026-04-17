"""Serialize namespace tree into autoware_system_design_format YAML strings."""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .connection_resolver import (
    ModuleInterface,
    PortRef,
    TopicConnection,
    _is_infra,
    _resolved_topic,
    extract_module_interfaces,
)
from .grouper import ComponentGroup
from .launch_parser import ContainerRecord, NodeRecord
from .namespace_tree import NamespaceNode

DESIGN_FORMAT = "0.3.1"


# ---------------------------------------------------------------------------
# Helpers shared by both flat (ComponentGroup) and tree (NamespaceNode) paths
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Recursive module emitter (NamespaceNode-based)
# ---------------------------------------------------------------------------

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
    # namespaces that are direct members (self + children)
    member_ns: set[str] = {my_ns}
    for child in ns_node.children.values():
        member_ns.add(child.namespace)

    external_pub: list[tuple[str, str]] = []
    external_sub: list[tuple[str, str]] = []
    seen_pub: set[str] = set()
    seen_sub: set[str] = set()

    # Iterate direct nodes of this level to find cross-boundary ports
    for node in ns_node.direct_nodes:
        for remap in node.remaps:
            if remap.direction == "unknown":
                continue
            port = remap.port_name(node.namespace, group_namespace=my_ns)
            if not port:
                continue
            topic = _resolved_topic(node, remap)
            if _is_infra(topic):
                continue
            if remap.direction == "output":
                consumers = [r for r in all_sub.get(topic, []) if r.component != my_ns]
                if consumers and port not in seen_pub:
                    seen_pub.add(port)
                    external_pub.append((port, topic))
            else:
                producers = [r for r in all_pub.get(topic, []) if r.component != my_ns]
                if producers and port not in seen_sub:
                    seen_sub.add(port)
                    external_sub.append((port, topic))

    # Also check child boundary ports
    for child in ns_node.children.values():
        child_ns = child.namespace
        for node in child.all_nodes:
            for remap in node.remaps:
                if remap.direction == "unknown":
                    continue
                port = remap.port_name(node.namespace, group_namespace=child_ns)
                if not port:
                    continue
                topic = _resolved_topic(node, remap)
                if _is_infra(topic):
                    continue
                if remap.direction == "output":
                    # External if consumed outside this ns_node entirely
                    consumers = [r for r in all_sub.get(topic, [])
                                 if not r.component.startswith(my_ns)]
                    if consumers and port not in seen_pub:
                        seen_pub.add(port)
                        external_pub.append((port, topic))
                else:
                    producers = [r for r in all_pub.get(topic, [])
                                 if not r.component.startswith(my_ns)]
                    if producers and port not in seen_sub:
                        seen_sub.add(port)
                        external_sub.append((port, topic))

    # Internal connections at this level (cross-child or direct↔child)
    internal: list[tuple[str, str, str, str]] = []
    seen_internal: set[tuple] = set()

    # Build name maps for direct nodes
    direct_name_map = _build_instance_name_map(ns_node.direct_nodes)

    for topic in set(list(all_pub.keys()) + list(all_sub.keys())):
        # publishers within this level's scope (direct nodes + children)
        pubs = [r for r in all_pub.get(topic, [])
                if r.component == my_ns or r.component.startswith(my_ns + "/")]
        subs = [r for r in all_sub.get(topic, [])
                if r.component == my_ns or r.component.startswith(my_ns + "/")]
        for pub_ref in pubs:
            for sub_ref in subs:
                if pub_ref.node_path == sub_ref.node_path:
                    continue
                if pub_ref.component == sub_ref.component:
                    continue  # same child handles it internally
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

    # instances: child sub-modules first, then direct nodes
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

    # subscribers
    lines.append("subscribers:")
    if iface.subscribers:
        for port, topic in sorted(iface.subscribers):
            lines.append(f"  - name: {port}  # {topic}")
    else:
        lines.append("  []")
    lines.append("")

    # publishers
    lines.append("publishers:")
    if iface.publishers:
        for port, topic in sorted(iface.publishers):
            lines.append(f"  - name: {port}  # {topic}")
    else:
        lines.append("  []")
    lines.append("")

    # connections
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
    # Belongs to a child namespace — find which child
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
    sub_ports = {port for port, _ in iface.subscribers}
    pub_ports = {port for port, _ in iface.publishers}

    seen_ext_in: set[tuple] = set()
    seen_ext_out: set[tuple] = set()
    seen_internal: set[tuple] = set()

    # External input → direct node subscriber
    for node in ns_node.direct_nodes:
        inst = direct_name_map[node.full_path]
        for remap in node.remaps:
            if remap.direction != "input":
                continue
            port = remap.port_name(node.namespace, group_namespace=ns_node.namespace)
            if port and port in sub_ports:
                key = (port, inst)
                if key not in seen_ext_in:
                    seen_ext_in.add(key)
                    lines.append(f"  - - subscriber.{port}")
                    lines.append(f"    - {inst}.subscriber.{port}")

    # External input → child sub-module subscriber (passthrough)
    for child in ns_node.children.values():
        for node in child.all_nodes:
            for remap in node.remaps:
                if remap.direction != "input":
                    continue
                port = remap.port_name(node.namespace, group_namespace=child.namespace)
                if port and port in sub_ports:
                    key = (port, child.name)
                    if key not in seen_ext_in:
                        seen_ext_in.add(key)
                        lines.append(f"  - - subscriber.{port}")
                        lines.append(f"    - {child.name}.subscriber.{port}")

    # Direct node publisher → external output
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

    # Child sub-module publisher → external output (passthrough)
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

    # Internal cross-instance connections
    for pub_path, pub_port, sub_path, sub_port in iface.internal_connections:
        pub_inst = _inst_ref(pub_path, ns_node, direct_name_map)
        sub_inst = _inst_ref(sub_path, ns_node, direct_name_map)
        key = (pub_inst, pub_port, sub_inst, sub_port)
        if key not in seen_internal:
            seen_internal.add(key)
            lines.append(f"  - - {pub_inst}.publisher.{pub_port}")
            lines.append(f"    - {sub_inst}.subscriber.{sub_port}")

    return lines


# ---------------------------------------------------------------------------
# Parameter set emitter
# ---------------------------------------------------------------------------

def emit_parameter_set_yaml(system_name: str, component_name: str, ns_node: NamespaceNode) -> str:
    """Generate a *.parameter_set.yaml for one top-level component."""
    ps_name = f"{system_name}_{component_name}.parameter_set"
    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {ps_name}")
    lines.append("")
    lines.append("parameters:")

    all_nodes = ns_node.all_nodes
    has_params = False
    for node in all_nodes:
        if not node.param_files:
            continue
        has_params = True
        lines.append(f"  - node: {node.full_path}")
        lines.append("    param_files:")
        for pf in node.param_files:
            lines.append(f"      - param_file: {pf}")
        lines.append("    param_values: []")

    if not has_params:
        lines.append("  []")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Flat ComponentGroup module emitter (kept for backward compat / --no-tree)
# ---------------------------------------------------------------------------

def emit_module_yaml(
    group: ComponentGroup,
    all_groups: list[ComponentGroup],
) -> str:
    """Generate a *.module.yaml for the given ComponentGroup (flat mode)."""
    name_map = _build_instance_name_map(group.nodes)
    iface = extract_module_interfaces(group, all_groups)

    entity_name = group.entity_name if group.namespace != "(root)" else "RosSystem"

    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {entity_name}.module")
    lines.append("")

    lines.append("instances:")
    if group.nodes:
        for node in group.nodes:
            inst = name_map[node.full_path]
            entity = _node_to_instance_entity(node)
            lines.append(f"  - name: {inst}")
            lines.append(f"    entity: {entity}")
            if node.is_composable and node.container:
                lines.append(f"    # container: {node.container}")
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
    conn_lines = _build_module_connections(group, iface, name_map)
    if conn_lines:
        lines.extend(conn_lines)
    else:
        lines.append("  []")

    return "\n".join(lines) + "\n"


def _build_module_connections(
    group: ComponentGroup,
    iface: ModuleInterface,
    name_map: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    sub_ports = {port for port, _ in iface.subscribers}
    pub_ports = {port for port, _ in iface.publishers}

    seen_ext_in: set[tuple] = set()
    for node in group.nodes:
        inst = name_map[node.full_path]
        for remap in node.remaps:
            if remap.direction != "input":
                continue
            port = remap.port_name(node.namespace, group_namespace=group.namespace)
            if port and port in sub_ports:
                key = (port, inst)
                if key not in seen_ext_in:
                    seen_ext_in.add(key)
                    lines.append(f"  - - subscriber.{port}")
                    lines.append(f"    - {inst}.subscriber.{port}")

    seen_ext_out: set[tuple] = set()
    for node in group.nodes:
        inst = name_map[node.full_path]
        for remap in node.remaps:
            if remap.direction != "output":
                continue
            port = remap.port_name(node.namespace, group_namespace=group.namespace)
            if port and port in pub_ports:
                key = (inst, port)
                if key not in seen_ext_out:
                    seen_ext_out.add(key)
                    lines.append(f"  - - {inst}.publisher.{port}")
                    lines.append(f"    - publisher.{port}")

    seen_internal: set[tuple] = set()
    for pub_path, pub_port, sub_path, sub_port in iface.internal_connections:
        pub_inst = name_map.get(pub_path, pub_path)
        sub_inst = name_map.get(sub_path, sub_path)
        key = (pub_inst, pub_port, sub_inst, sub_port)
        if key not in seen_internal:
            seen_internal.add(key)
            lines.append(f"  - - {pub_inst}.publisher.{pub_port}")
            lines.append(f"    - {sub_inst}.subscriber.{sub_port}")

    return lines


# ---------------------------------------------------------------------------
# system.yaml emitter
# ---------------------------------------------------------------------------

def emit_system_yaml(
    system_name: str,
    groups: list[ComponentGroup],
    all_containers: list[ContainerRecord],
    connections: list[TopicConnection],
    compute_unit: str = "main_ecu",
    variables: Optional[list[dict]] = None,
    parameter_sets: Optional[list[str]] = None,
) -> str:
    """Generate a *.system.yaml for the full launch (flat mode)."""
    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {system_name}.system")
    lines.append("")
    lines.append("variables: []")
    lines.append("")
    lines.append("modes:")
    lines.append("  - name: Runtime")
    lines.append("    description: on-vehicle runtime mode")
    lines.append("    default: true")
    lines.append("  - name: LoggingSimulation")
    lines.append("    description: Logged data replay simulation mode")
    lines.append("")

    if parameter_sets:
        lines.append("parameter_sets:")
        for ps in parameter_sets:
            lines.append(f"  - {ps}")
    else:
        lines.append("parameter_sets: []")
    lines.append("")

    lines.append("components:")
    for group in groups:
        if group.namespace == "(root)":
            continue
        entity = f"{group.entity_name}.module"
        lines.append(f"  - name: {group.name}")
        lines.append(f"    entity: {entity}")
        ns = group.namespace.lstrip("/")
        if ns:
            lines.append(f"    path: {group.namespace}")
        lines.append(f"    compute_unit: {compute_unit}")
    lines.append("")

    node_groups = _build_node_groups(groups, all_containers)
    if node_groups:
        lines.append("node_groups:")
        for ng in node_groups:
            lines.append(f"  - name: {ng['name']}")
            lines.append(f"    type: {ng['type']}")
            lines.append("    nodes:")
            for path in ng["nodes"]:
                lines.append(f"      - {path}")
        lines.append("")

    lines.append("connections:")
    if connections:
        for conn in connections:
            pub_str, sub_str = conn.as_system_yaml_pair()
            lines.append(f"  - - {pub_str}")
            lines.append(f"    - {sub_str}")
    else:
        lines.append("  []")

    return "\n".join(lines) + "\n"


def emit_system_yaml_from_tree(
    system_name: str,
    top_nodes: dict[str, NamespaceNode],
    all_containers: list[ContainerRecord],
    connections: list[TopicConnection],
    compute_unit: str = "main_ecu",
    parameter_sets: Optional[list[str]] = None,
) -> str:
    """Generate a *.system.yaml using the namespace tree (tree mode)."""
    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {system_name}.system")
    lines.append("")
    lines.append("variables: []")
    lines.append("")
    lines.append("modes:")
    lines.append("  - name: Runtime")
    lines.append("    description: on-vehicle runtime mode")
    lines.append("    default: true")
    lines.append("  - name: LoggingSimulation")
    lines.append("    description: Logged data replay simulation mode")
    lines.append("")

    if parameter_sets:
        lines.append("parameter_sets:")
        for ps in parameter_sets:
            lines.append(f"  - {ps}")
    else:
        lines.append("parameter_sets: []")
    lines.append("")

    lines.append("components:")
    for ns, ns_node in sorted(top_nodes.items()):
        lines.append(f"  - name: {ns_node.name}")
        lines.append(f"    entity: {ns_node.entity_name}.module")
        lines.append(f"    path: {ns_node.namespace}")
        lines.append(f"    compute_unit: {compute_unit}")
        if parameter_sets:
            ps_name = f"{system_name}_{ns_node.name}.parameter_set"
            lines.append(f"    parameter_set: {ps_name}")
    lines.append("")

    # node_groups: collect all containers from the whole tree
    all_ns_nodes = list(top_nodes.values())
    node_groups = _build_node_groups_from_tree(all_ns_nodes, all_containers)
    if node_groups:
        lines.append("node_groups:")
        for ng in node_groups:
            lines.append(f"  - name: {ng['name']}")
            lines.append(f"    type: {ng['type']}")
            lines.append("    nodes:")
            for path in ng["nodes"]:
                lines.append(f"      - {path}")
        lines.append("")

    lines.append("connections:")
    if connections:
        for conn in connections:
            pub_str, sub_str = conn.as_system_yaml_pair()
            lines.append(f"  - - {pub_str}")
            lines.append(f"    - {sub_str}")
    else:
        lines.append("  []")

    return "\n".join(lines) + "\n"


def _build_node_groups_from_tree(
    top_nodes: list[NamespaceNode],
    all_containers: list[ContainerRecord],
) -> list[dict]:
    container_map = {c.full_path: c for c in all_containers}
    container_nodes: dict[str, list[str]] = defaultdict(list)

    for ns_node in top_nodes:
        for node in ns_node.all_nodes:
            if node.container:
                container_nodes[node.container].append(node.full_path)

    return _finalize_node_groups(container_nodes, container_map)


def _build_node_groups(
    groups: list[ComponentGroup],
    all_containers: list[ContainerRecord],
) -> list[dict]:
    container_map = {c.full_path: c for c in all_containers}
    container_nodes: dict[str, list[str]] = defaultdict(list)

    for group in groups:
        for node in group.nodes:
            if node.container:
                container_nodes[node.container].append(node.full_path)

    return _finalize_node_groups(container_nodes, container_map)


def _finalize_node_groups(
    container_nodes: dict[str, list[str]],
    container_map: dict[str, ContainerRecord],
) -> list[dict]:
    result = []
    used_names: set[str] = set()

    for container_path, node_paths in sorted(container_nodes.items()):
        rec = container_map.get(container_path)
        if not rec:
            group_type = "ros2_component_container_mt" if "mt" in container_path else "ros2_component_container"
        else:
            group_type = rec.group_type

        path_parts = [p for p in container_path.strip("/").split("/") if p]
        if len(path_parts) >= 2:
            candidate = "_".join(path_parts[-2:])
        elif path_parts:
            candidate = path_parts[-1]
        else:
            candidate = "container"

        name = candidate
        suffix = 2
        while name in used_names:
            name = f"{candidate}_{suffix}"
            suffix += 1
        used_names.add(name)

        seen: set[str] = set()
        unique_nodes = []
        for p in node_paths:
            if p not in seen:
                seen.add(p)
                unique_nodes.append(p)

        result.append({
            "name": name,
            "type": group_type,
            "nodes": sorted(unique_nodes),
        })

    return result
