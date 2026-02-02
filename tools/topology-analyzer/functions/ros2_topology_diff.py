#!/usr/bin/env python3

import argparse
import json
import os
import re
from typing import Dict, List, Optional, Tuple

from ros2_topology_common import Signature, signature_from_node, signature_id


def _topic_counts(nodes: List[Dict]) -> Dict[str, Tuple[int, int]]:
    # topic -> (pub_count, sub_count)
    counts: Dict[str, List[int]] = {}
    for n in nodes:
        for t in (n.get("publishers") or {}).keys():
            counts.setdefault(t, [0, 0])[0] += 1
        for t in (n.get("subscribers") or {}).keys():
            counts.setdefault(t, [0, 0])[1] += 1
    return {k: (v[0], v[1]) for k, v in counts.items()}


def _compile(pattern: Optional[str]) -> Optional[re.Pattern]:
    if not pattern:
        return None
    return re.compile(pattern)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Diff two ros2_graph_snapshot.py outputs in a topology-first, name-agnostic way. "
            "Compares node signature multisets and topic pub/sub counts."
        )
    )
    ap.add_argument("old_graph_json")
    ap.add_argument("new_graph_json")
    ap.add_argument(
        "--out",
        default=None,
        help="Output Markdown path. Default: alongside new graph as topology_diff.md",
    )
    ap.add_argument(
        "--max-sig-changes",
        type=int,
        default=80,
        help="Max signature diffs to print in detail.",
    )
    ap.add_argument(
        "--max-topics",
        type=int,
        default=200,
        help="Max topic pub/sub count diffs to print.",
    )
    ap.add_argument(
        "--topic-filter",
        default=None,
        help="Regex to include only matching topics in topic diff section.",
    )

    args = ap.parse_args()

    with open(args.old_graph_json, "r", encoding="utf-8") as f:
        old = json.load(f)
    with open(args.new_graph_json, "r", encoding="utf-8") as f:
        new = json.load(f)

    old_nodes: List[Dict] = old.get("nodes", [])
    new_nodes: List[Dict] = new.get("nodes", [])

    # Signature multisets and examples.
    def build(nodes: List[Dict]):
        counts: Dict[str, int] = {}
        examples: Dict[str, List[str]] = {}
        sigs: Dict[str, Signature] = {}
        for n in nodes:
            s = signature_from_node(n)
            sid = signature_id(s)
            counts[sid] = counts.get(sid, 0) + 1
            sigs.setdefault(sid, s)
            ex = examples.setdefault(sid, [])
            if len(ex) < 6:
                ex.append(n.get("fq_name", ""))
        return counts, examples, sigs

    old_counts, old_examples, old_sigs = build(old_nodes)
    new_counts, new_examples, new_sigs = build(new_nodes)

    all_sids = sorted(set(old_counts) | set(new_counts))
    sig_diffs = []
    for sid in all_sids:
        oc = old_counts.get(sid, 0)
        nc = new_counts.get(sid, 0)
        if oc != nc:
            sig_diffs.append((sid, oc, nc))

    # Topic pub/sub count diffs.
    topic_filter = _compile(args.topic_filter)
    old_t = _topic_counts(old_nodes)
    new_t = _topic_counts(new_nodes)
    all_topics = sorted(set(old_t) | set(new_t))
    topic_diffs = []
    for t in all_topics:
        if topic_filter and not topic_filter.search(t):
            continue
        op, os_ = old_t.get(t, (0, 0))
        np, ns_ = new_t.get(t, (0, 0))
        if (op, os_) != (np, ns_):
            topic_diffs.append((t, op, os_, np, ns_))

    # Sort topic diffs by magnitude, then name.
    def topic_score(x):
        _, op, os_, np, ns_ = x
        return (-(abs(np - op) + abs(ns_ - os_)), x[0])

    topic_diffs.sort(key=topic_score)

    out_path = args.out
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(args.new_graph_json)), "topology_diff.md")

    lines: List[str] = []
    lines.append("# ROS 2 Topology Diff\n")
    lines.append(f"- Old: {os.path.abspath(args.old_graph_json)}")
    lines.append(f"- New: {os.path.abspath(args.new_graph_json)}")
    lines.append(f"- Old timestamp: {old.get('timestamp','')}")
    lines.append(f"- New timestamp: {new.get('timestamp','')}")
    lines.append(f"- Old nodes: {len(old_nodes)}")
    lines.append(f"- New nodes: {len(new_nodes)}")
    lines.append(f"- Signature diffs: {len(sig_diffs)}")
    lines.append(f"- Topic pub/sub diffs: {len(topic_diffs)}")
    lines.append("")

    lines.append("## Signature (node-level) differences\n")
    if not sig_diffs:
        lines.append("No signature count differences detected.")
    else:
        # Prefer largest count deltas first.
        sig_diffs.sort(key=lambda x: (-abs(x[2] - x[1]), x[0]))
        show = sig_diffs[: args.max_sig_changes]
        for sid, oc, nc in show:
            lines.append(f"### {sid}: {oc} -> {nc}")
            if oc:
                lines.append(f"- old examples: {', '.join(old_examples.get(sid, [])[:6])}")
            if nc:
                lines.append(f"- new examples: {', '.join(new_examples.get(sid, [])[:6])}")
            sig = new_sigs.get(sid) or old_sigs.get(sid)
            if sig:
                lines.append(f"- pubs={len(sig.pubs)} subs={len(sig.subs)} srvs={len(sig.srvs)} clis={len(sig.clis)}")
                if sig.pubs:
                    lines.append("- publish topics (up to 20):")
                    for t, types in sig.pubs[:20]:
                        ty = ", ".join(types) if types else "<unknown>"
                        lines.append(f"  - {t} :: {ty}")
                if sig.subs:
                    lines.append("- subscribe topics (up to 20):")
                    for t, types in sig.subs[:20]:
                        ty = ", ".join(types) if types else "<unknown>"
                        lines.append(f"  - {t} :: {ty}")
            lines.append("")

        if len(sig_diffs) > len(show):
            lines.append(f"(Truncated: showing {len(show)}/{len(sig_diffs)} signature diffs)")
            lines.append("")

    lines.append("## Topic pub/sub count differences\n")
    if not topic_diffs:
        lines.append("No topic pub/sub count differences detected.")
    else:
        show_t = topic_diffs[: args.max_topics]
        for t, op, os_, np, ns_ in show_t:
            lines.append(f"- {t}: pubs {op}->{np}, subs {os_}->{ns_}")
        if len(topic_diffs) > len(show_t):
            lines.append(f"(Truncated: showing {len(show_t)}/{len(topic_diffs)} topic diffs)")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
