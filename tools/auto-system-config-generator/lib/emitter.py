"""Serialize ComponentGroups into autoware_system_design_format YAML strings."""

from __future__ import annotations

from typing import Optional

from .connection_resolver import ModuleInterface, TopicConnection, extract_module_interfaces
from .grouper import ComponentGroup
from .launch_parser import ContainerRecord, NodeRecord

DESIGN_FORMAT = "0.3.1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _indent(text: str, n: int = 2) -> str:
    return textwrap.indent(text, " " * n)


def _node_to_instance_entity(node: NodeRecord) -> str:
    """Best-effort entity name for a node (PascalCase.node)."""
    if node.plugin:
        # e.g. "autoware::localization::NdtScanMatcher" → "NdtScanMatcher.node"
        short = node.plugin.split("::")[-1]
        return f"{short}.node"
    # Fallback: convert exec name to PascalCase
    raw = node.exec or node.name
    pascal = "".join(p.capitalize() for p in raw.replace("-", "_").split("_"))
    return f"{pascal}.node"


# ---------------------------------------------------------------------------
# module.yaml emitter
# ---------------------------------------------------------------------------

def emit_module_yaml(
    group: ComponentGroup,
    all_groups: list[ComponentGroup],
) -> str:
    """Generate a *.module.yaml for the given ComponentGroup."""
    iface = extract_module_interfaces(group, all_groups)

    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {group.entity_name}.module")
    lines.append("")

    # instances (deduplicate names by suffixing collisions)
    lines.append("instances:")
    if group.nodes:
        used_inst_names: dict[str, int] = {}
        for node in group.nodes:
            entity = _node_to_instance_entity(node)
            inst = node.instance_name
            if inst in used_inst_names:
                used_inst_names[inst] += 1
                inst = f"{inst}_{used_inst_names[inst]}"
            else:
                used_inst_names[inst] = 1
            lines.append(f"  - name: {inst}")
            lines.append(f"    entity: {entity}")
            if node.is_composable and node.container:
                lines.append(f"    # container: {node.container}")
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
    conn_lines = _build_module_connections(group, iface)
    if conn_lines:
        lines.extend(conn_lines)
    else:
        lines.append("  []")

    return "\n".join(lines) + "\n"


def _build_module_connections(group: ComponentGroup, iface: ModuleInterface) -> list[str]:
    lines: list[str] = []

    sub_ports = {port for port, _ in iface.subscribers}
    pub_ports = {port for port, _ in iface.publishers}

    # External input → internal node subscriber
    for node in group.nodes:
        for remap in node.remaps:
            if remap.direction != "input":
                continue
            port = remap.port_name
            if port and port in sub_ports:
                lines.append(f"  - - subscriber.{port}")
                lines.append(f"    - {node.instance_name}.subscriber.{port}")

    # Internal node publisher → external output
    for node in group.nodes:
        for remap in node.remaps:
            if remap.direction != "output":
                continue
            port = remap.port_name
            if port and port in pub_ports:
                lines.append(f"  - - {node.instance_name}.publisher.{port}")
                lines.append(f"    - publisher.{port}")

    # Internal node-to-node connections
    for pub_inst, pub_port, sub_inst, sub_port in iface.internal_connections:
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
) -> str:
    """Generate a *.system.yaml for the full launch."""
    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {system_name}.system")
    lines.append("")

    # variables
    lines.append("variables: []")
    lines.append("")

    # modes
    lines.append("modes:")
    lines.append("  - name: Runtime")
    lines.append("    description: on-vehicle runtime mode")
    lines.append("    default: true")
    lines.append("  - name: LoggingSimulation")
    lines.append("    description: Logged data replay simulation mode")
    lines.append("")

    lines.append("parameter_sets: []")
    lines.append("")

    # components
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

    # node_groups
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

    # connections
    lines.append("connections:")
    if connections:
        for conn in connections:
            pub_str, sub_str = conn.as_system_yaml_pair()
            lines.append(f"  - - {pub_str}")
            lines.append(f"    - {sub_str}")
    else:
        lines.append("  []")

    return "\n".join(lines) + "\n"


def _build_node_groups(
    groups: list[ComponentGroup],
    all_containers: list[ContainerRecord],
) -> list[dict]:
    """Build node_group entries mapping containers to the nodes they host."""
    # container full_path → ContainerRecord
    container_map = {c.full_path: c for c in all_containers}

    # container full_path → list of node full_paths
    from collections import defaultdict
    container_nodes: dict[str, list[str]] = defaultdict(list)

    for group in groups:
        for node in group.nodes:
            if node.container:
                container_nodes[node.container].append(node.full_path)

    result = []
    used_names: set[str] = set()

    for container_path, node_paths in sorted(container_nodes.items()):
        rec = container_map.get(container_path)
        if not rec:
            group_type = "ros2_component_container_mt" if "mt" in container_path else "ros2_component_container"
        else:
            group_type = rec.group_type

        # Build a unique name from the full container path to avoid collisions.
        # Use only the last two meaningful path segments so names stay readable.
        path_parts = [p for p in container_path.strip("/").split("/") if p]
        if len(path_parts) >= 2:
            candidate = "_".join(path_parts[-2:])
        elif path_parts:
            candidate = path_parts[-1]
        else:
            candidate = "container"

        # Ensure uniqueness
        name = candidate
        suffix = 2
        while name in used_names:
            name = f"{candidate}_{suffix}"
            suffix += 1
        used_names.add(name)

        # Deduplicate nodes, keep deterministic order
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
