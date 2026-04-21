#!/usr/bin/env python3
# Shared types, constants, and utility functions for topology-analyzer.
# This is the single source of truth — functions/ros2_topology_common.py re-exports from here.

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ---------- Constants ----------

# Topics present on virtually every ROS 2 node — hidden in single-report display by default.
COMMON_TOPICS: Set[str] = {"/rosout", "/clock", "/parameter_events"}

# Tool-internal nodes excluded from system comparisons by default.
TOOL_NODE_RE = re.compile(r"^/graph_snapshot$|^/launch_ros_\d+$")

# Standard ROS 2 parameter management service suffixes — derivative of node name, suppress as rename noise.
PARAM_SVC_SUFFIXES: Set[str] = {
    "describe_parameters",
    "get_parameter_types",
    "get_parameters",
    "list_parameters",
    "set_parameters",
    "set_parameters_atomically",
}

# ---------- Signature ----------


@dataclass(frozen=True)
class Signature:
    pubs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    subs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    srvs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    clis: Tuple[Tuple[str, Tuple[str, ...]], ...]


def freeze_map(m: Dict[str, List[str]]) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    items = [(k, tuple(sorted(v or []))) for k, v in (m or {}).items()]
    items.sort(key=lambda x: x[0])
    return tuple(items)


def signature_from_node(node: Dict) -> Signature:
    return Signature(
        pubs=freeze_map(node.get("publishers", {})),
        subs=freeze_map(node.get("subscribers", {})),
        srvs=freeze_map(node.get("services", {})),
        clis=freeze_map(node.get("clients", {})),
    )


def signature_id(sig: Signature) -> str:
    payload = {"pubs": sig.pubs, "subs": sig.subs, "srvs": sig.srvs, "clis": sig.clis}
    b = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(b).hexdigest()[:12]


def iter_type_items(sig: Signature) -> Iterable[str]:
    """Direction-qualified type tokens without topic names.

    Used for type-composition-first matching: two nodes are considered similar when
    they publish/subscribe/serve the same message types, regardless of topic names.
    Robust across namespace changes and topic remappings.
    """
    for _, types in sig.pubs:
        for t in types:
            yield f"PT|{t}"
    for _, types in sig.subs:
        for t in types:
            yield f"ST|{t}"
    for _, types in sig.srvs:
        for t in types:
            yield f"SVT|{t}"
    for _, types in sig.clis:
        for t in types:
            yield f"CLT|{t}"


def iter_signature_items(
    sig: Signature,
    include_types: bool = False,
    type_only: bool = False,
) -> Iterable[str]:
    """Yield comparison tokens for a node signature.

    Args:
        type_only: if True, yield direction-qualified type tokens only (no topic names).
                   Takes priority over *include_types*.  Recommended for cross-system matching.
        include_types: if True (and *type_only* is False), append message types to topic names.
    """
    if type_only:
        yield from iter_type_items(sig)
        return

    def emit(prefix: str, items: Sequence[Tuple[str, Tuple[str, ...]]]) -> Iterable[str]:
        for name, types in items:
            if include_types:
                t = ",".join(types) if types else "<unknown>"
                yield f"{prefix}|{name}|{t}"
            else:
                yield f"{prefix}|{name}"

    yield from emit("P", sig.pubs)
    yield from emit("S", sig.subs)
    yield from emit("SV", sig.srvs)
    yield from emit("C", sig.clis)


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ---------- Name / path helpers ----------


def basename(name: str) -> str:
    """Return the last path segment of a ROS 2 fully-qualified name."""
    if not name:
        return ""
    s = name.rstrip("/")
    if "/" not in s:
        return s
    return s.rsplit("/", 1)[-1]


def name_similarity(a: str, b: str) -> float:
    """Ratio-based name similarity, taking the max of full-path and basename comparisons."""
    if not a or not b:
        return 0.0
    return max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, basename(a), basename(b)).ratio(),
    )


# ---------- I/O ----------


def load_graph(path: str) -> Tuple[Dict, List[Dict]]:
    """Load a graph.json snapshot and return (data, nodes)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data, data.get("nodes", []) or []


def topic_index(nodes: List[Dict]) -> Dict[str, Dict[str, List[str]]]:
    """Build {topic: {"publishers": [fq...], "subscribers": [fq...]}} from node list."""
    idx: Dict[str, Dict[str, List[str]]] = {}
    for n in nodes:
        fq = n.get("fq_name", "")
        for t in (n.get("publishers") or {}).keys():
            idx.setdefault(t, {"publishers": [], "subscribers": []})["publishers"].append(fq)
        for t in (n.get("subscribers") or {}).keys():
            idx.setdefault(t, {"publishers": [], "subscribers": []})["subscribers"].append(fq)
    return idx
