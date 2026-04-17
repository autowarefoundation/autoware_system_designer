"""Generate *.node.yaml definitions for undeclared ROS 2 nodes."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from .connection_resolver import _resolved_topic
from .emitter import DESIGN_FORMAT, _node_to_instance_entity

if TYPE_CHECKING:
    from .graph_parser import GraphNodeInfo
    from .launch_parser import NodeRecord
    from .namespace_tree import NamespaceNode


# ---------------------------------------------------------------------------
# Package map
# ---------------------------------------------------------------------------

def load_package_map(path: Path) -> dict[str, str]:
    """Load _package_map.yaml → {pkg_name: share_dir_path}."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("package_map", {})


def find_package_map() -> Optional[Path]:
    """Locate _package_map.yaml via ament_index. Returns None if not found."""
    try:
        from ament_index_python.packages import get_package_share_path
        share = get_package_share_path("autoware_system_designer")
        p = Path(share) / "resource" / "_package_map.yaml"
        if p.exists():
            return p
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Node entity collection
# ---------------------------------------------------------------------------

def collect_nodes_by_entity(
    ns_nodes: list[NamespaceNode],
) -> dict[str, list[NodeRecord]]:
    """Walk namespace tree, collect all NodeRecords grouped by entity name."""
    result: dict[str, list[NodeRecord]] = defaultdict(list)

    def _walk(ns_node: NamespaceNode) -> None:
        for node in ns_node.direct_nodes:
            entity = _node_to_instance_entity(node)
            result[entity].append(node)
        for child in ns_node.children.values():
            _walk(child)

    for ns_node in ns_nodes:
        _walk(ns_node)
    return dict(result)


def collect_nodes_by_entity_flat(
    groups: list,  # list[ComponentGroup]
) -> dict[str, list[NodeRecord]]:
    """Collect NodeRecords grouped by entity name from flat ComponentGroups."""
    result: dict[str, list[NodeRecord]] = defaultdict(list)
    for group in groups:
        for node in group.nodes:
            entity = _node_to_instance_entity(node)
            result[entity].append(node)
    return dict(result)


def _common_namespace(namespaces: list[str]) -> str:
    """Return the longest common namespace prefix across the given namespaces."""
    if not namespaces:
        return ""
    parts_list = [ns.strip("/").split("/") for ns in namespaces if ns.strip("/")]
    if not parts_list:
        return ""
    common: list[str] = []
    for level in zip(*parts_list):
        if len(set(level)) == 1:
            common.append(level[0])
        else:
            break
    return "/".join(common)


def namespace_for_entity(nodes: list[NodeRecord]) -> str:
    """Return the common namespace path for a set of NodeRecords.

    Used to determine the subdirectory under node/ where the generated
    *.node.yaml should be placed.
    """
    namespaces = [n.namespace for n in nodes if n.namespace]
    return _common_namespace(namespaces)


# ---------------------------------------------------------------------------
# Existing definition lookup
# ---------------------------------------------------------------------------

def find_defined_node_entities(
    entity_names: set[str],
    package_map: dict[str, str],
    extra_search_dirs: Optional[list[Path]] = None,
) -> set[str]:
    """Return entity names that already have .node.yaml files in known locations.

    Searches each package's share directory under design/node/ and any
    additional directories provided via extra_search_dirs.
    """
    defined: set[str] = set()
    search_dirs: list[Path] = list(extra_search_dirs or [])

    for share_dir in package_map.values():
        candidate = Path(share_dir) / "design" / "node"
        if candidate.exists():
            search_dirs.append(candidate)

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for yaml_file in search_dir.rglob("*.node.yaml"):
            # SomeNode.node.yaml → stem = "SomeNode.node"
            entity = yaml_file.name[: -len(".yaml")]
            if entity in entity_names:
                defined.add(entity)

    return defined


# ---------------------------------------------------------------------------
# Node YAML emitter
# ---------------------------------------------------------------------------

def _get_msg_type(
    node: NodeRecord,
    topic: str,
    direction: str,
    graph: Optional[dict[str, GraphNodeInfo]],
) -> str:
    """Look up message type from graph snapshot; return empty string if unknown."""
    if graph is None:
        return ""
    info = graph.get(node.full_path)
    if info is None:
        return ""
    types = info.publishers.get(topic, []) if direction == "output" else info.subscribers.get(topic, [])
    return types[0] if types else ""


def emit_node_yaml(
    entity_name: str,
    nodes: list[NodeRecord],
    graph: Optional[dict[str, GraphNodeInfo]] = None,
) -> str:
    """Generate a *.node.yaml for an entity represented by one or more NodeRecords.

    When multiple NodeRecords share the same entity (different instances of the
    same plugin), their ports are merged so the definition captures the full
    interface of the node type.
    """
    canon = nodes[0]

    # Merge ports across all instances; prefer entries that have a message type
    sub_ports: dict[str, str] = {}  # port_name → msg_type
    pub_ports: dict[str, str] = {}

    for node in nodes:
        for remap in node.remaps:
            if remap.direction == "unknown":
                continue
            port = remap.port_name(node.namespace)
            if not port:
                continue
            topic = _resolved_topic(node, remap)
            msg = _get_msg_type(node, topic, remap.direction, graph)
            if remap.direction == "input":
                if port not in sub_ports or (not sub_ports[port] and msg):
                    sub_ports[port] = msg
            else:
                if port not in pub_ports or (not pub_ports[port] and msg):
                    pub_ports[port] = msg

    # Deduplicate param files across instances (preserve order of first appearance)
    seen_pf: set[str] = set()
    pf_entries: list[tuple[str, str]] = []
    for node in nodes:
        for pf in node.param_files:
            if pf not in seen_pf:
                seen_pf.add(pf)
                pf_entries.append((Path(pf).stem, pf))

    pkg_name = canon.pkg or ""
    if pkg_name.startswith("autoware"):
        provider = "autoware"
    elif pkg_name.startswith("tier4"):
        provider = "tier4"
    else:
        provider = ""

    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {entity_name}")
    lines.append("")

    lines.append("package:")
    lines.append(f"  name: {pkg_name}")
    if provider:
        lines.append(f"  provider: {provider}")
    lines.append("")

    lines.append("launch:")
    if canon.plugin:
        lines.append(f"  plugin: {canon.plugin}")
    exec_name = canon.exec or (canon.name if canon.name and canon.name.lower() != "none" else "")
    if exec_name:
        lines.append(f"  executable: {exec_name}")
    lines.append("")

    lines.append("subscribers:")
    if sub_ports:
        for port in sorted(sub_ports):
            msg = sub_ports[port]
            lines.append(f"  - name: {port}")
            if msg:
                lines.append(f"    message_type: {msg}")
    else:
        lines.append("  []")
    lines.append("")

    lines.append("publishers:")
    if pub_ports:
        for port in sorted(pub_ports):
            msg = pub_ports[port]
            lines.append(f"  - name: {port}")
            if msg:
                lines.append(f"    message_type: {msg}")
    else:
        lines.append("  []")
    lines.append("")

    lines.append("param_files:")
    if pf_entries:
        for name, path in pf_entries:
            lines.append(f"  - name: {name}")
            lines.append(f"    default: {path}")
    else:
        lines.append("  []")
    lines.append("")

    lines.append("param_values: []")
    lines.append("")
    lines.append("processes: []")

    return "\n".join(lines) + "\n"
