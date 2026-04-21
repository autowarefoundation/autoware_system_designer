#!/usr/bin/env python3
# NodeGraphInfo dataclass and small graph-node helpers for ros2_graph_snapshot.py.

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class NodeGraphInfo:
    name: str
    namespace: str
    fq_name: str
    publishers: Dict[str, List[str]]
    subscribers: Dict[str, List[str]]
    services: Dict[str, List[str]]
    clients: Dict[str, List[str]]


def fq_name(name: str, namespace: str) -> str:
    if namespace == "/":
        return f"/{name}" if not name.startswith("/") else name
    namespace = namespace if namespace.startswith("/") else f"/{namespace}"
    namespace = namespace.rstrip("/")
    return f"{namespace}/{name}" if not name.startswith("/") else name


def as_type_map(pairs: List[Tuple[str, List[str]]]) -> Dict[str, List[str]]:
    """Convert rclpy graph API output (List[Tuple[name, List[types]]]) to a plain dict."""
    result: Dict[str, List[str]] = {}
    for topic_or_srv, types in pairs:
        result[topic_or_srv] = list(types)
    return result


def compile_filter(pattern: Optional[str]) -> Optional[re.Pattern]:
    if not pattern:
        return None
    return re.compile(pattern)
