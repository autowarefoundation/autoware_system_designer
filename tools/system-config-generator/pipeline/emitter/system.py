"""Tree-mode system YAML emitter."""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..connection_resolver import TopicConnection
from ..launch_parser import ContainerRecord
from ..namespace_tree import NamespaceNode
from .module import DESIGN_FORMAT


def emit_system_yaml_from_tree(
    system_name: str,
    top_nodes: dict[str, NamespaceNode],
    all_containers: list[ContainerRecord],
    connections: list[TopicConnection],
    compute_unit: str = "main_ecu",
    parameter_sets: Optional[list[str]] = None,
) -> str:
    """Generate a *.system.yaml using the namespace tree."""
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
