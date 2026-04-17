#!/usr/bin/env python3
# Composable container and OS process group helpers.

from collections import defaultdict
from typing import Dict, List, Tuple


def build_container_map(nodes: List[Dict]) -> Dict[str, List[str]]:
    """Return {container_fq: sorted [composable_node_fq, ...]} for all composable nodes."""
    result: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        comp = n.get("component_info")
        if comp and comp.get("container"):
            result[comp["container"]].append(n.get("fq_name", ""))
    return {k: sorted(v) for k, v in result.items()}


def build_process_groups(
    nodes: List[Dict],
) -> Tuple[Dict[int, Dict], List[str]]:
    """Group nodes by OS PID.

    Returns:
      groups         — {pid: {"pid", "exe", "package", "executor_type",
                              "component_classes", "nodes": [fq, ...]}}
      no_process_fqs — sorted list of fq_names that had no process info
    """
    groups: Dict[int, Dict] = {}
    no_process: List[str] = []
    for n in nodes:
        proc = n.get("process")
        fq = n.get("fq_name", "")
        if not proc:
            no_process.append(fq)
            continue
        pid = proc.get("pid")
        if pid is None:
            no_process.append(fq)
            continue
        if pid not in groups:
            groups[pid] = {
                "pid": pid,
                "exe": proc.get("exe"),
                "package": proc.get("package"),
                "executor_type": proc.get("executor_type"),
                "component_classes": proc.get("component_classes"),
                "nodes": [],
            }
        groups[pid]["nodes"].append(fq)
    for g in groups.values():
        g["nodes"].sort()
    return groups, sorted(no_process)
