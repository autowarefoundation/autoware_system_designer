"""Parse a ROS 2 graph snapshot JSON and merge its topics into NodeRecord remaps."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .launch_parser import NodeRecord

# Infra topics to skip (mirrors connection_resolver._is_infra)
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
_INFRA_SUFFIXES = ("/rosout", "/parameter_events", "/_supported_types")
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
class GraphNodeInfo:
    fq_name: str
    namespace: str
    publishers: dict[str, list[str]] = field(default_factory=dict)  # topic → [msg_type]
    subscribers: dict[str, list[str]] = field(default_factory=dict)  # topic → [msg_type]
    package: Optional[str] = None


def parse_graph_json(path: str | Path) -> dict[str, GraphNodeInfo]:
    """Parse a ROS 2 graph snapshot JSON. Returns {fq_name: GraphNodeInfo}."""
    with open(path) as f:
        data = json.load(f)

    result: dict[str, GraphNodeInfo] = {}
    for node in data.get("nodes", []):
        fq = node.get("fq_name", "")
        if not fq:
            continue
        ns = node.get("namespace", "")
        proc = node.get("process") or {}
        pkg = proc.get("package") or None
        # rclcpp_components is the container executable, not the component package
        if pkg == "rclcpp_components":
            pkg = None

        pubs = {t: v for t, v in (node.get("publishers") or {}).items() if not _is_infra(t)}
        subs = {t: v for t, v in (node.get("subscribers") or {}).items() if not _is_infra(t)}

        result[fq] = GraphNodeInfo(
            fq_name=fq,
            namespace=ns,
            publishers=pubs,
            subscribers=subs,
            package=pkg,
        )

    return result


def _derive_port_name(topic: str, node_namespace: str) -> str:
    """Derive a port name from an absolute topic and the node's namespace.

    Strips the node namespace prefix when the topic lives under it; otherwise
    returns the topic with the leading slash removed.
    """
    ns = node_namespace.rstrip("/")
    if ns and topic.startswith(ns + "/"):
        return topic[len(ns) + 1 :]
    return topic.lstrip("/")


def merge_graph_topics(
    nodes: list[NodeRecord],
    graph: dict[str, GraphNodeInfo],
) -> int:
    """Supplement each NodeRecord's remaps with hard-coded topics from graph snapshot.

    Topics already covered by an explicit remap entry are skipped.  New topics
    are appended as synthetic ``~/input/<port>`` / ``~/output/<port>`` remap
    entries so the rest of the pipeline can treat them uniformly.

    Returns the number of synthetic remap entries added.
    """
    from .launch_parser import RemapEntry

    added = 0
    for node in nodes:
        info = graph.get(node.full_path)
        if info is None:
            continue

        # Resolve all topics already tracked via explicit remaps
        covered: set[str] = set()
        for remap in node.remaps:
            t = remap.to_topic
            if not t.startswith("/"):
                ns = node.namespace.rstrip("/")
                t = f"{ns}/{t}" if ns else f"/{t}"
            covered.add(t)

        for topic in info.publishers:
            if topic in covered:
                continue
            port = _derive_port_name(topic, node.namespace)
            node.remaps.append(RemapEntry(from_topic=f"~/output/{port}", to_topic=topic))
            covered.add(topic)
            added += 1

        for topic in info.subscribers:
            if topic in covered:
                continue
            port = _derive_port_name(topic, node.namespace)
            node.remaps.append(RemapEntry(from_topic=f"~/input/{port}", to_topic=topic))
            covered.add(topic)
            added += 1

    return added
