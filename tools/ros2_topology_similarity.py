#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class Sig:
    pubs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    subs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    srvs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    clis: Tuple[Tuple[str, Tuple[str, ...]], ...]


def _freeze_map(m: Dict[str, List[str]]) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    items: List[Tuple[str, Tuple[str, ...]]] = []
    for k, v in (m or {}).items():
        items.append((k, tuple(sorted(v or []))))
    items.sort(key=lambda x: x[0])
    return tuple(items)


def _sig(node: Dict) -> Sig:
    return Sig(
        pubs=_freeze_map(node.get("publishers", {})),
        subs=_freeze_map(node.get("subscribers", {})),
        srvs=_freeze_map(node.get("services", {})),
        clis=_freeze_map(node.get("clients", {})),
    )


def _sid(sig: Sig) -> str:
    b = json.dumps(
        {"pubs": sig.pubs, "subs": sig.subs, "srvs": sig.srvs, "clis": sig.clis},
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(b).hexdigest()[:12]


def _iter_items(sig: Sig, include_types: bool) -> Iterable[str]:
    def emit(prefix: str, items: Sequence[Tuple[str, Tuple[str, ...]]]):
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


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 1.0


def _build_groups(graph: Dict) -> Tuple[Dict[str, int], Dict[str, List[str]], Dict[str, Sig]]:
    nodes: List[Dict] = graph.get("nodes", [])
    counts: Dict[str, int] = {}
    examples: Dict[str, List[str]] = {}
    sigs: Dict[str, Sig] = {}
    for n in nodes:
        s = _sig(n)
        sid = _sid(s)
        counts[sid] = counts.get(sid, 0) + 1
        sigs.setdefault(sid, s)
        ex = examples.setdefault(sid, [])
        if len(ex) < 8:
            ex.append(n.get("fq_name", ""))
    return counts, examples, sigs


def _format_list(xs: Sequence[str], max_items: int) -> str:
    xs = [x for x in xs if x]
    if len(xs) <= max_items:
        return ", ".join(xs)
    return ", ".join(xs[:max_items]) + f" (+{len(xs) - max_items} more)"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Find similar-but-not-identical node signatures between two ROS2 graph snapshots. "
            "Useful when node names differ but topology should match."
        )
    )
    ap.add_argument("old_graph_json")
    ap.add_argument("new_graph_json")
    ap.add_argument(
        "--out",
        default=None,
        help="Output Markdown path. Default: alongside new graph as topology_similarity.md",
    )
    ap.add_argument(
        "--include-types",
        action="store_true",
        help="Include message/service types in similarity comparison (stricter).",
    )
    ap.add_argument(
        "--min-similarity",
        type=float,
        default=0.85,
        help="Minimum Jaccard similarity to report.",
    )
    ap.add_argument(
        "--max-pairs",
        type=int,
        default=60,
        help="Maximum similar signature pairs to report per direction.",
    )
    ap.add_argument(
        "--max-diff-items",
        type=int,
        default=25,
        help="Maximum added/removed items to list per pair.",
    )

    args = ap.parse_args()

    with open(args.old_graph_json, "r", encoding="utf-8") as f:
        old = json.load(f)
    with open(args.new_graph_json, "r", encoding="utf-8") as f:
        new = json.load(f)

    old_counts, old_examples, old_sigs = _build_groups(old)
    new_counts, new_examples, new_sigs = _build_groups(new)

    old_item_sets: Dict[str, Set[str]] = {
        sid: set(_iter_items(sig, include_types=args.include_types)) for sid, sig in old_sigs.items()
    }
    new_item_sets: Dict[str, Set[str]] = {
        sid: set(_iter_items(sig, include_types=args.include_types)) for sid, sig in new_sigs.items()
    }

    def best_match(source_sid: str, source_items: Set[str], target_item_sets: Dict[str, Set[str]]):
        best: Optional[Tuple[str, float]] = None
        for tid, titems in target_item_sets.items():
            if tid == source_sid:
                continue
            sim = _jaccard(source_items, titems)
            if best is None or sim > best[1]:
                best = (tid, sim)
        return best

    # Focus on signatures that changed in count or are missing.
    all_sids = set(old_counts) | set(new_counts)
    changed = [
        sid
        for sid in all_sids
        if old_counts.get(sid, 0) != new_counts.get(sid, 0)
    ]

    # Old -> New near-matches
    old_pairs = []
    for sid in changed:
        if sid not in old_sigs:
            continue
        bm = best_match(sid, old_item_sets[sid], new_item_sets)
        if not bm:
            continue
        tid, sim = bm
        if sim >= args.min_similarity:
            old_pairs.append((sim, sid, tid))
    old_pairs.sort(key=lambda x: (-x[0], x[1], x[2]))

    # New -> Old near-matches
    new_pairs = []
    for sid in changed:
        if sid not in new_sigs:
            continue
        bm = best_match(sid, new_item_sets[sid], old_item_sets)
        if not bm:
            continue
        tid, sim = bm
        if sim >= args.min_similarity:
            new_pairs.append((sim, sid, tid))
    new_pairs.sort(key=lambda x: (-x[0], x[1], x[2]))

    out_path = args.out
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(args.new_graph_json)), "topology_similarity.md")

    lines: List[str] = []
    lines.append("# ROS 2 Topology Similarity Report\n")
    lines.append(f"- Old: {os.path.abspath(args.old_graph_json)}")
    lines.append(f"- New: {os.path.abspath(args.new_graph_json)}")
    lines.append(f"- Old timestamp: {old.get('timestamp','')}")
    lines.append(f"- New timestamp: {new.get('timestamp','')}")
    lines.append(f"- include_types: {bool(args.include_types)}")
    lines.append(f"- min_similarity: {args.min_similarity}")
    lines.append(f"- signatures changed: {len(changed)}")
    lines.append("")

    def emit_pair(sim: float, a: str, b: str, a_label: str, b_label: str):
        a_items = old_item_sets[a] if a_label == "old" else new_item_sets[a]
        b_items = new_item_sets[b] if b_label == "new" else old_item_sets[b]
        removed = sorted(a_items - b_items)
        added = sorted(b_items - a_items)

        a_count = old_counts.get(a, 0) if a_label == "old" else new_counts.get(a, 0)
        b_count = new_counts.get(b, 0) if b_label == "new" else old_counts.get(b, 0)
        a_ex = old_examples.get(a, []) if a_label == "old" else new_examples.get(a, [])
        b_ex = new_examples.get(b, []) if b_label == "new" else old_examples.get(b, [])

        lines.append(f"### sim={sim:.3f}  {a_label}:{a} (count={a_count})  ~  {b_label}:{b} (count={b_count})")
        lines.append(f"- {a_label} examples: {_format_list(a_ex, 6)}")
        lines.append(f"- {b_label} examples: {_format_list(b_ex, 6)}")
        if removed:
            lines.append(f"- removed from {a_label} -> {b_label} (up to {args.max_diff_items}):")
            for x in removed[: args.max_diff_items]:
                lines.append(f"  - {x}")
        if added:
            lines.append(f"- added in {b_label} vs {a_label} (up to {args.max_diff_items}):")
            for x in added[: args.max_diff_items]:
                lines.append(f"  - {x}")
        lines.append("")

    lines.append("## Old signatures that most closely match a different new signature\n")
    if not old_pairs:
        lines.append("No near-matches found above threshold.")
        lines.append("")
    else:
        for sim, sid, tid in old_pairs[: args.max_pairs]:
            emit_pair(sim, sid, tid, a_label="old", b_label="new")
        if len(old_pairs) > args.max_pairs:
            lines.append(f"(Truncated: showing {args.max_pairs}/{len(old_pairs)} pairs)")
            lines.append("")

    lines.append("## New signatures that most closely match a different old signature\n")
    if not new_pairs:
        lines.append("No near-matches found above threshold.")
        lines.append("")
    else:
        for sim, sid, tid in new_pairs[: args.max_pairs]:
            emit_pair(sim, sid, tid, a_label="new", b_label="old")
        if len(new_pairs) > args.max_pairs:
            lines.append(f"(Truncated: showing {args.max_pairs}/{len(new_pairs)} pairs)")
            lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
