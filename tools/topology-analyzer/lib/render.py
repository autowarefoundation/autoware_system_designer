#!/usr/bin/env python3
# Report rendering and export.
# render_single / render_diff return List[str] (lines).
# write_report handles file I/O; pass '-' as out_path to write to stdout.

import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .common import COMMON_TOPICS, basename, signature_from_node, signature_id, topic_index
from .diff import (
    diff_component_info,
    diff_maps,
    diff_process_info,
    edge_set,
    remap_old_edges,
)
from .filters import is_param_svc_rename
from .matching import param_info, param_value_info
from .process import build_container_map, build_process_groups

# ---------- Shared helpers ----------


def namespace_prefix(fq: str, depth: int = 2) -> str:
    parts = [p for p in fq.strip("/").split("/") if p]
    return "/" + "/".join(parts[:depth]) if parts else fq


def namespace_summary(
    added: List[str],
    removed: List[str],
    changed: List[Tuple[str, str, object]],
) -> List[str]:
    stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"added": 0, "removed": 0, "changed": 0})
    for fq in added:
        stats[namespace_prefix(fq)]["added"] += 1
    for fq in removed:
        stats[namespace_prefix(fq)]["removed"] += 1
    for ofq, _nfq, _d in changed:
        stats[namespace_prefix(ofq)]["changed"] += 1
    lines: List[str] = []
    for ns in sorted(stats):
        s = stats[ns]
        parts = []
        if s["added"]:
            parts.append(f"+{s['added']} added")
        if s["removed"]:
            parts.append(f"-{s['removed']} removed")
        if s["changed"]:
            parts.append(f"~{s['changed']} changed")
        lines.append(f"- {ns}: {', '.join(parts)}")
    return lines


def classify_node_change(ofq: str, nfq: str, diffs: Dict) -> str:
    tags = []
    if any(diffs[k][0] or diffs[k][1] for k in ("publishers", "subscribers", "services", "clients")):
        tags.append("structural")
    if (ofq != nfq) or any(diffs[k][3] for k in ("publishers", "subscribers", "services", "clients")):
        tags.append("remapped")
    param_diff = diffs.get("parameters")
    value_diff = diffs.get("parameter_values")
    if param_diff and (param_diff.get("removed") or param_diff.get("added")):
        tags.append("param-name")
    if value_diff and value_diff.get("changed"):
        tags.append("param-value")
    if diffs.get("component"):
        tags.append("container")
    if diffs.get("process"):
        tags.append("process")
    return "[" + ", ".join(tags) + "]" if tags else "[changed]"


# ---------- Export ----------


def write_report(lines: List[str], out_path: str) -> None:
    """Write report to *out_path*.  Pass '-' to write to stdout instead of a file."""
    content = "\n".join(lines) + "\n"
    if out_path == "-":
        sys.stdout.write(content)
    else:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(content)


# ---------- Single-snapshot report ----------


def render_single(
    data: Dict,
    nodes: List[Dict],
    *,
    ignored_topics: Set[str],
    src_path: str,
    include_transform_listener: bool,
    include_tool_nodes: bool,
    include_parameter_events: bool,
    include_common_topics: bool,
    max_groups: int,
    max_nodes_per_group: int,
    topic_focus: Optional[str] = None,
) -> List[str]:
    """Build lines for a single-snapshot topology report."""
    groups: Dict[str, Dict] = {}
    for n in nodes:
        sig = signature_from_node(n)
        sid = signature_id(sig)
        g = groups.setdefault(sid, {"count": 0, "sig": sig, "examples": [], "containers": set()})
        g["count"] += 1
        if len(g["examples"]) < max(1, max_nodes_per_group):
            g["examples"].append(n.get("fq_name", ""))
        comp = n.get("component_info")
        if comp and comp.get("container"):
            g["containers"].add(comp["container"])

    sorted_groups = sorted(groups.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    t_idx = topic_index(nodes)

    lines: List[str] = []
    lines.append("# ROS 2 Topology Report\n")
    lines.append(f"- Source: {os.path.abspath(src_path)}")
    lines.append(f"- Timestamp: {data.get('timestamp', '')}")
    lines.append(f"- Nodes (processed): {len(nodes)}")
    if not include_transform_listener:
        lines.append("- Filter: ignored nodes containing 'transform_listener'")
    if not include_tool_nodes:
        lines.append("- Filter: ignored tool nodes (/graph_snapshot, /launch_ros_*)")
    if not include_parameter_events:
        lines.append("- Filter: ignored topic '/parameter_events'")
    if not include_common_topics:
        lines.append("- Display: common topics (/rosout, /clock, /parameter_events) hidden in groups")
    has_component_data = any(n.get("component_info") is not None for n in nodes)
    lines.append(
        f"- Component data: {'yes' if has_component_data else 'no (snapshot taken without composition_interfaces or no composable nodes)'}"
    )
    lines.append(f"- Signature groups: {len(sorted_groups)}")
    dup = data.get("duplicates", []) or []
    lines.append(f"- Duplicate node names: {len(dup)}")
    if dup:
        lines.append("  - Examples:")
        for d in dup[:10]:
            lines.append(f"    - {d}")
    lines.append("")

    lines.append("## Signature Groups (name-agnostic)\n")
    show_groups = sorted_groups[:max_groups] if max_groups > 0 else sorted_groups
    for sid, g in show_groups:
        sig = g["sig"]
        lines.append(f"### {sid} (count={g['count']})")
        if g["examples"]:
            lines.append(f"- example nodes: {', '.join(g['examples'][:max_nodes_per_group])}")
        if g.get("containers"):
            lines.append(f"- container(s): {', '.join(sorted(g['containers']))}")
        lines.append(f"- pubs: {len(sig.pubs)}  subs: {len(sig.subs)}  srvs: {len(sig.srvs)}  clis: {len(sig.clis)}")
        if sig.pubs:
            pub_items = [(t, types) for t, types in sig.pubs if include_common_topics or t not in COMMON_TOPICS]
            if pub_items:
                lines.append("- publish topics:")
                for t, types in pub_items:
                    lines.append(f"  - {t} :: {', '.join(types) if types else '<unknown>'}")
        if sig.subs:
            sub_items = [(t, types) for t, types in sig.subs if include_common_topics or t not in COMMON_TOPICS]
            if sub_items:
                lines.append("- subscribe topics:")
                for t, types in sub_items:
                    lines.append(f"  - {t} :: {', '.join(types) if types else '<unknown>'}")
        lines.append("")

    container_map = build_container_map(nodes)
    if container_map:
        standalone_count = sum(1 for n in nodes if not (n.get("component_info") or {}).get("container"))
        lines.append("## Composable Node Containers\n")
        lines.append(f"- Standalone nodes: {standalone_count}")
        lines.append(f"- Containers: {len(container_map)}")
        lines.append("")
        for cname in sorted(container_map):
            members = container_map[cname]
            lines.append(f"### {cname} ({len(members)} composable nodes)\n")
            for fq in members:
                lines.append(f"- {fq}")
            lines.append("")

    process_groups, no_process_fqs = build_process_groups(nodes)
    if process_groups:
        lines.append("## Process / Executor Summary\n")
        lines.append(f"- Unique processes: {len(process_groups)}")
        lines.append(f"- Nodes without process info: {len(no_process_fqs)}")
        exec_counts: Dict[str, int] = defaultdict(int)
        for g in process_groups.values():
            exec_counts[g["executor_type"] or "standalone"] += 1
        lines.append("- Executor type breakdown:")
        for et, cnt in sorted(exec_counts.items()):
            lines.append(f"  - {et}: {cnt} process(es)")
        lines.append("")
        sorted_pids = sorted(
            process_groups,
            key=lambda p: (
                process_groups[p]["executor_type"] or "standalone",
                process_groups[p]["package"] or "",
                p,
            ),
        )
        for pid in sorted_pids:
            g = process_groups[pid]
            et = g["executor_type"] or "standalone"
            pkg = g["package"] or "<unknown>"
            node_list = g["nodes"]
            lines.append(f"### PID {pid}  [{et}]  {pkg}")
            lines.append(f"- exe: {g['exe'] or '<unknown>'}")
            if g.get("component_classes"):
                cls = g["component_classes"]
                preview_cls = ", ".join(cls[:max_nodes_per_group])
                if len(cls) > max_nodes_per_group:
                    preview_cls += f", +{len(cls) - max_nodes_per_group} more"
                lines.append(f"- component classes: {preview_cls}")
            preview = ", ".join(node_list[:max_nodes_per_group])
            if len(node_list) > max_nodes_per_group:
                preview += f", +{len(node_list) - max_nodes_per_group} more"
            lines.append(f"- nodes ({len(node_list)}): {preview}")
            lines.append("")

    lines.append("## Topic Index (publishers/subscribers counts)\n")
    if topic_focus is not None:
        lines.append(f"- Filter: topic regex '{topic_focus}'")
        t_idx = {tp: ps for tp, ps in t_idx.items() if re.search(topic_focus, tp)}
    for tp, ps in sorted(t_idx.items()):
        pubs = ps.get("publishers", [])
        subs = ps.get("subscribers", [])
        lines.append(f"- {tp}: pubs={len(pubs)} subs={len(subs)}")

    return lines


# ---------- Diff report ----------


def render_diff(
    old_path: str,
    new_path: str,
    old_data: Dict,
    new_data: Dict,
    old_nodes: List[Dict],
    new_nodes: List[Dict],
    *,
    mapping: Dict[str, str],
    evidence: List[Tuple[str, str, float]],
    old_params: Dict[str, List[str]],
    new_params: Dict[str, List[str]],
    old_param_values: Dict[str, Dict[str, str]],
    new_param_values: Dict[str, Dict[str, str]],
    ignored_topics: Set[str],
    include_transform_listener: bool,
    include_tool_nodes: bool,
    include_parameter_events: bool,
    max_match_summary: int,
    max_changed_nodes: int,
    max_nodes_per_group: int,
) -> List[str]:
    """Build lines for a two-snapshot diff report."""
    old_by_fq = {n.get("fq_name", ""): n for n in old_nodes}
    new_by_fq = {n.get("fq_name", ""): n for n in new_nodes}
    matched_old = set(mapping.keys())
    matched_new = set(mapping.values())
    removed_nodes = sorted(set(old_by_fq.keys()) - matched_old)
    added_nodes = sorted(set(new_by_fq.keys()) - matched_new)

    param_enabled = bool(old_params or new_params)
    param_values_enabled = bool(old_param_values or new_param_values)
    component_enabled = any(n.get("component_info") is not None for n in old_nodes + new_nodes)
    process_enabled = any(n.get("process") is not None for n in old_nodes + new_nodes)

    # --- Compute per-node diffs ---
    changed_nodes: List[Tuple[str, str, Dict]] = []
    for ofq, nfq in mapping.items():
        o = old_by_fq.get(ofq, {})
        n = new_by_fq.get(nfq, {})
        pubs = diff_maps(o.get("publishers", {}), n.get("publishers", {}), ignored_topics=ignored_topics)
        subs = diff_maps(o.get("subscribers", {}), n.get("subscribers", {}), ignored_topics=ignored_topics)
        srvs = diff_maps(o.get("services", {}), n.get("services", {}), ignored_topics=ignored_topics)
        clis = diff_maps(o.get("clients", {}), n.get("clients", {}), ignored_topics=ignored_topics)

        param_diff = None
        if param_enabled:
            o_params, o_param_status = param_info(old_params, ofq)
            n_params, n_param_status = param_info(new_params, nfq)
            if not o_param_status and not n_param_status:
                p_removed = sorted(o_params - n_params)
                p_added = sorted(n_params - o_params)
            else:
                p_removed = []
                p_added = []
            param_diff = {
                "removed": p_removed,
                "added": p_added,
                "old_status": o_param_status,
                "new_status": n_param_status,
            }

        value_diff = None
        if param_values_enabled:
            o_vals, o_val_status = param_value_info(old_param_values, ofq)
            n_vals, n_val_status = param_value_info(new_param_values, nfq)
            if not o_val_status and not n_val_status:
                val_changed = [
                    (k, o_vals.get(k), n_vals.get(k))
                    for k in sorted(set(o_vals) | set(n_vals))
                    if o_vals.get(k) != n_vals.get(k)
                ]
            else:
                val_changed = []
            value_diff = {
                "changed": val_changed,
                "old_status": o_val_status,
                "new_status": n_val_status,
            }

        component_diff = diff_component_info(o.get("component_info"), n.get("component_info"))
        process_diff = diff_process_info(o.get("process"), n.get("process"))

        has_diff = (
            any(pubs[i] or subs[i] or srvs[i] or clis[i] for i in range(4))
            or (
                param_diff
                and (
                    param_diff["removed"] or param_diff["added"] or param_diff["old_status"] != param_diff["new_status"]
                )
            )
            or (value_diff and (value_diff["changed"] or value_diff["old_status"] != value_diff["new_status"]))
            or component_diff
            or process_diff
        )
        if has_diff:
            changed_nodes.append(
                (
                    ofq,
                    nfq,
                    {
                        "publishers": pubs,
                        "subscribers": subs,
                        "services": srvs,
                        "clients": clis,
                        "parameters": param_diff,
                        "parameter_values": value_diff,
                        "component": component_diff,
                        "process": process_diff,
                    },
                )
            )

    # --- Compute edge-level diffs ---
    old_edges = remap_old_edges(edge_set(old_nodes, ignored_topics=ignored_topics), mapping)
    new_edges = edge_set(new_nodes, ignored_topics=ignored_topics)
    raw_removed = old_edges - new_edges
    raw_added = new_edges - old_edges

    removed_by_ep: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    added_by_ep: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for p, s, t in raw_removed:
        removed_by_ep[(p, s)].append(t)
    for p, s, t in raw_added:
        added_by_ep[(p, s)].append(t)

    renamed_edges: List[Tuple[str, str, str, str]] = []
    consumed_removed: Set[Tuple[str, str, str]] = set()
    consumed_added: Set[Tuple[str, str, str]] = set()
    for (p, s), old_topics in removed_by_ep.items():
        new_topics = added_by_ep.get((p, s), [])
        if len(old_topics) == 1 and len(new_topics) == 1:
            ot, nt = old_topics[0], new_topics[0]
            if basename(ot) == basename(nt):
                renamed_edges.append((p, s, ot, nt))
                consumed_removed.add((p, s, ot))
                consumed_added.add((p, s, nt))

    removed_edges = sorted(raw_removed - consumed_removed)
    added_edges = sorted(raw_added - consumed_added)
    renamed_edges.sort()

    # --- Build report lines ---
    lines: List[str] = []
    lines.append("# ROS 2 Topology Diff (name-agnostic)\n")
    lines.append(f"- Old: {os.path.abspath(old_path)}")
    lines.append(f"- New: {os.path.abspath(new_path)}")
    lines.append(f"- Old timestamp: {old_data.get('timestamp', '')}")
    lines.append(f"- New timestamp: {new_data.get('timestamp', '')}")
    lines.append(f"- Old nodes: {len(old_nodes)}")
    lines.append(f"- New nodes: {len(new_nodes)}")
    if not include_transform_listener:
        lines.append("- Filter: ignored nodes containing 'transform_listener'")
    if not include_tool_nodes:
        lines.append("- Filter: ignored tool nodes (/graph_snapshot, /launch_ros_*)")
    if not include_parameter_events:
        lines.append("- Filter: ignored topic '/parameter_events'")
    if param_enabled:
        lines.append("- Parameters: compared by name (param_names)")
    if param_values_enabled:
        lines.append("- Parameters: compared by value (param_values)")
    if component_enabled:
        lines.append("- Component containers: compared")
    if process_enabled:
        lines.append("- Process info: compared (executor_type, package, exe)")
    lines.append(f"- Matched node pairs: {len(mapping)}")
    lines.append(f"- Added nodes (unmatched): {len(added_nodes)}")
    lines.append(f"- Removed nodes (unmatched): {len(removed_nodes)}")
    lines.append("")

    lines.append("## Namespace Summary\n")
    ns_summary = namespace_summary(added_nodes, removed_nodes, changed_nodes)
    lines.extend(ns_summary if ns_summary else ["- (no differences)"])
    lines.append("")

    # Container changes
    old_container_map = build_container_map(old_nodes)
    new_container_map = build_container_map(new_nodes)
    if old_container_map or new_container_map:
        old_standalone = sum(1 for n in old_nodes if not (n.get("component_info") or {}).get("container"))
        new_standalone = sum(1 for n in new_nodes if not (n.get("component_info") or {}).get("container"))
        lines.append("## Container Changes\n")
        lines.append(f"- Standalone nodes: {old_standalone} -> {new_standalone}")
        lines.append(f"- Containers: {len(old_container_map)} -> {len(new_container_map)}")
        lines.append("")

        added_containers = sorted(set(new_container_map) - set(old_container_map))
        removed_containers = sorted(set(old_container_map) - set(new_container_map))
        common_containers = sorted(set(old_container_map) & set(new_container_map))

        if added_containers:
            lines.append("### Added containers\n")
            for c in added_containers:
                members = new_container_map[c]
                preview = ", ".join(members[:5])
                suffix = f", +{len(members) - 5} more" if len(members) > 5 else ""
                lines.append(f"- {c} ({len(members)} nodes): {preview}{suffix}")
            lines.append("")

        if removed_containers:
            lines.append("### Removed containers\n")
            for c in removed_containers:
                members = old_container_map[c]
                preview = ", ".join(members[:5])
                suffix = f", +{len(members) - 5} more" if len(members) > 5 else ""
                lines.append(f"- {c} ({len(members)} nodes): {preview}{suffix}")
            lines.append("")

        reverse_mapping = {v: k for k, v in mapping.items()}
        changed_container_list = []
        for c in common_containers:
            old_members = set(old_container_map[c])
            new_members = set(new_container_map[c])
            staying_new = {mapping[om] for om in old_members if mapping.get(om) in new_members}
            left_old = sorted(old_members - {om for om in old_members if mapping.get(om) in staying_new})
            joined_new = sorted(new_members - staying_new)
            if left_old or joined_new:
                changed_container_list.append((c, left_old, joined_new))

        if changed_container_list:
            lines.append("### Changed containers (membership differs)\n")
            for c, left_old, joined_new in changed_container_list:
                parts = []
                if joined_new:
                    parts.append(f"+{len(joined_new)} joined")
                if left_old:
                    parts.append(f"-{len(left_old)} left")
                lines.append(f"#### {c} ({', '.join(parts)})\n")
                for nm in joined_new:
                    om = reverse_mapping.get(nm)
                    if om is None:
                        lines.append(f"- joined: {nm}  [new node]")
                    else:
                        om_old_container = (old_by_fq.get(om, {}).get("component_info") or {}).get("container")
                        lines.append(
                            f"- joined: {nm}  [from {om_old_container}]"
                            if om_old_container
                            else f"- joined: {nm}  [was standalone]"
                        )
                for om in left_old:
                    nm = mapping.get(om)
                    if nm is None:
                        lines.append(f"- left:   {om}  [node removed]")
                    else:
                        nm_new_container = (new_by_fq.get(nm, {}).get("component_info") or {}).get("container")
                        lines.append(
                            f"- left:   {om}  [now in {nm_new_container}]"
                            if nm_new_container
                            else f"- left:   {om}  [now standalone]"
                        )
                lines.append("")

    # Process changes
    proc_exec_changes: List[Tuple] = []
    proc_pkg_changes: List[Tuple] = []
    proc_gained: List[str] = []
    proc_lost: List[str] = []
    for ofq, nfq, diffs in changed_nodes:
        pd = diffs.get("process")
        if not pd:
            continue
        if pd.get("gained"):
            proc_gained.append(nfq)
        elif pd.get("lost"):
            proc_lost.append(ofq)
        else:
            if "executor_type" in pd:
                proc_exec_changes.append(
                    (ofq, nfq, pd["executor_type"]["old"] or "none", pd["executor_type"]["new"] or "none")
                )
            if "package" in pd:
                proc_pkg_changes.append(
                    (ofq, nfq, pd["package"]["old"] or "<unknown>", pd["package"]["new"] or "<unknown>")
                )

    if process_enabled and (proc_exec_changes or proc_pkg_changes or proc_gained or proc_lost):
        lines.append("## Process Changes\n")
        lines.append(f"- Executor type changes: {len(proc_exec_changes)}")
        lines.append(f"- Package changes: {len(proc_pkg_changes)}")
        lines.append(f"- Nodes gaining process info: {len(proc_gained)}")
        lines.append(f"- Nodes losing process info: {len(proc_lost)}")
        lines.append("")
        if proc_exec_changes:
            lines.append("### Executor type changes\n")
            for ofq, nfq, old_et, new_et in sorted(proc_exec_changes, key=lambda x: x[0]):
                label = f"{ofq}" if ofq == nfq else f"{ofq} -> {nfq}"
                lines.append(f"- {label}  [{old_et} -> {new_et}]")
            lines.append("")
        if proc_pkg_changes:
            lines.append("### Package changes\n")
            for ofq, nfq, old_pkg, new_pkg in sorted(proc_pkg_changes, key=lambda x: x[0]):
                label = f"{ofq}" if ofq == nfq else f"{ofq} -> {nfq}"
                lines.append(f"- {label}  [{old_pkg} -> {new_pkg}]")
            lines.append("")
        if proc_gained:
            lines.append("### Gained process info\n")
            for fq in sorted(proc_gained):
                lines.append(f"- {fq}")
            lines.append("")
        if proc_lost:
            lines.append("### Lost process info\n")
            for fq in sorted(proc_lost):
                lines.append(f"- {fq}")
            lines.append("")

    lines.append("## Matching summary\n")
    evidence_sorted = sorted(evidence, key=lambda x: (-x[2], x[0], x[1]))
    max_match = max_match_summary if max_match_summary > 0 else len(evidence_sorted)
    for ofq, nfq, s in evidence_sorted[:max_match]:
        suffix = "" if ofq == nfq else ", renamed"
        lines.append(f"- {ofq} -> {nfq} (sim={s:.2f}{suffix})")
    if len(evidence_sorted) > max_match:
        lines.append(f"- ... {len(evidence_sorted) - max_match} more matched pairs")
    lines.append("")

    if added_nodes:
        lines.append("## Added nodes (unmatched)\n")
        for fq in added_nodes[:100]:
            lines.append(f"- {fq}")
        if len(added_nodes) > 100:
            lines.append(f"- ... {len(added_nodes) - 100} more")
        lines.append("")

    if removed_nodes:
        lines.append("## Removed nodes (unmatched)\n")
        for fq in removed_nodes[:100]:
            lines.append(f"- {fq}")
        if len(removed_nodes) > 100:
            lines.append(f"- ... {len(removed_nodes) - 100} more")
        lines.append("")

    if changed_nodes:
        lines.append("## Changed nodes (matched but endpoints differ)\n")
        max_changed = max_changed_nodes if max_changed_nodes > 0 else len(changed_nodes)
        for ofq, nfq, diffs in sorted(changed_nodes, key=lambda x: x[0])[:max_changed]:
            change_tag = classify_node_change(ofq, nfq, diffs)
            lines.append(f"### {ofq} -> {nfq} {change_tag}")
            node_was_renamed = ofq != nfq
            for kind in ("publishers", "subscribers", "services", "clients"):
                removed, added, changed, renamed = diffs[kind]
                # Suppress std ROS 2 param service renames derived purely from node rename.
                if node_was_renamed and kind == "services":
                    renamed = [(o, n) for o, n in renamed if not is_param_svc_rename(o, n)]
                if not (removed or added or changed or renamed):
                    continue
                lines.append(f"- {kind}:")
                for t in sorted(removed):
                    lines.append(f"  - removed: {t}")
                for t in sorted(added):
                    lines.append(f"  - added: {t}")
                for t in sorted(changed):
                    lines.append(f"  - type-changed: {t}")
                for old_name, new_name in sorted(renamed):
                    lines.append(f"  - renamed: {old_name} -> {new_name}")
            param_diff = diffs.get("parameters")
            if param_diff:
                lines.append("- parameters:")
                if param_diff.get("old_status"):
                    lines.append(f"  - old: {param_diff['old_status']}")
                if param_diff.get("new_status"):
                    lines.append(f"  - new: {param_diff['new_status']}")
                for p in param_diff.get("removed", [])[:30]:
                    lines.append(f"  - removed: {p}")
                for p in param_diff.get("added", [])[:30]:
                    lines.append(f"  - added: {p}")
            value_diff = diffs.get("parameter_values")
            if value_diff:
                lines.append("- parameter values:")
                if value_diff.get("old_status"):
                    lines.append(f"  - old: {value_diff['old_status']}")
                if value_diff.get("new_status"):
                    lines.append(f"  - new: {value_diff['new_status']}")
                for k, ov, nv in value_diff.get("changed", [])[:30]:
                    lines.append(
                        f"  - changed: {k} :: {'<unset>' if ov is None else ov} -> {'<unset>' if nv is None else nv}"
                    )
            comp_diff = diffs.get("component")
            if comp_diff:
                lines.append("- component:")
                old_c = comp_diff.get("old_container")
                new_c = comp_diff.get("new_container")
                old_id = comp_diff.get("old_id")
                new_id = comp_diff.get("new_id")
                if old_c is None and new_c is not None:
                    lines.append(f"  - standalone -> composable in {new_c} (id={new_id})")
                elif old_c is not None and new_c is None:
                    lines.append(f"  - composable in {old_c} (id={old_id}) -> standalone")
                else:
                    lines.append(f"  - container: {old_c} -> {new_c}")
                    if old_id != new_id:
                        lines.append(f"  - component_id: {old_id} -> {new_id}")
            proc_diff = diffs.get("process")
            if proc_diff:
                lines.append("- process:")
                if proc_diff.get("gained"):
                    et = proc_diff.get("executor_type") or "none"
                    pkg = proc_diff.get("package") or "<unknown>"
                    lines.append(f"  - gained process info  [executor_type={et}, package={pkg}]")
                elif proc_diff.get("lost"):
                    lines.append("  - lost process info")
                else:
                    for field in ("executor_type", "package", "exe"):
                        if field in proc_diff:
                            ov = proc_diff[field]["old"] or "none"
                            nv = proc_diff[field]["new"] or "none"
                            lines.append(f"  - {field}: {ov} -> {nv}")
            lines.append("")
        if len(changed_nodes) > max_changed:
            lines.append(f"- ... {len(changed_nodes) - max_changed} more changed matched nodes")
        lines.append("")

    lines.append("## Edge-level changes (pub -> sub on topic)\n")
    lines.append(f"- Added edges: {len(added_edges)}")
    lines.append(f"- Removed edges: {len(removed_edges)}")
    lines.append(f"- Renamed edges (same endpoints, topic renamed): {len(renamed_edges)}")
    lines.append("")
    if renamed_edges:
        lines.append("### Renamed edges\n")
        for p, s, ot, nt in renamed_edges[:80]:
            lines.append(f"- ~ {p} -> {s} : {ot} -> {nt}")
        if len(renamed_edges) > 80:
            lines.append(f"- ... {len(renamed_edges) - 80} more renamed edges")
        lines.append("")
    if added_edges:
        lines.append("### Added edges\n")
        for p, s, t in added_edges[:80]:
            lines.append(f"- + {p} -> {s} : {t}")
        if len(added_edges) > 80:
            lines.append(f"- ... {len(added_edges) - 80} more added edges")
        lines.append("")
    if removed_edges:
        lines.append("### Removed edges\n")
        for p, s, t in removed_edges[:80]:
            lines.append(f"- - {p} -> {s} : {t}")
        if len(removed_edges) > 80:
            lines.append(f"- ... {len(removed_edges) - 80} more removed edges")

    return lines
