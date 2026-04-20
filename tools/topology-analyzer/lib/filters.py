#!/usr/bin/env python3
# Node and topic filtering helpers.

from typing import Dict, List

from .common import PARAM_SVC_SUFFIXES, TOOL_NODE_RE, basename


def filter_transform_listener(nodes: List[Dict], *, include: bool) -> List[Dict]:
    if include:
        return nodes
    return [n for n in nodes if "transform_listener" not in (n.get("fq_name", "") or "")]


def filter_tool_nodes(nodes: List[Dict], *, include: bool) -> List[Dict]:
    """Remove /graph_snapshot and /launch_ros_* nodes (tool artifacts, not system nodes)."""
    if include:
        return nodes
    return [n for n in nodes if not TOOL_NODE_RE.match(n.get("fq_name", "") or "")]


def is_param_svc_rename(old_name: str, new_name: str) -> bool:
    """True when a service rename is purely a node-name-prefix change on a std ROS 2 param service."""
    old_base = basename(old_name)
    new_base = basename(new_name)
    return old_base == new_base and old_base in PARAM_SVC_SUFFIXES
