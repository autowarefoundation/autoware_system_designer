#!/usr/bin/env python3
# Diff computation: per-node endpoint diffs, edge-level diffs, component/process diffs.

import itertools
import os
from typing import Dict, List, Optional, Set, Tuple

from .common import basename, name_similarity

_RENAME_SIM_THRESHOLD = 0.70
_RENAME_SIM_MARGIN = 0.12


def diff_maps(
    a: Dict[str, List[str]],
    b: Dict[str, List[str]],
    *,
    ignored_topics: Set[str] = frozenset(),
) -> Tuple[Set[str], Set[str], Set[str], List[Tuple[str, str]]]:
    """Compare two endpoint maps (publishers/subscribers/services/clients).

    Returns (removed, added, type_changed, renames).
    Suppresses pure namespace/path renames by comparing on basename+type-set first.
    """
    a_map = {k: v or [] for k, v in (a or {}).items() if k not in ignored_topics}
    b_map = {k: v or [] for k, v in (b or {}).items() if k not in ignored_topics}

    def key(full: str, types: List[str]) -> Tuple[str, Tuple[str, ...]]:
        return (basename(full), tuple(sorted(types or [])))

    a_norm: Dict[Tuple[str, Tuple[str, ...]], Set[str]] = {}
    b_norm: Dict[Tuple[str, Tuple[str, ...]], Set[str]] = {}
    for full, types in a_map.items():
        a_norm.setdefault(key(full, types), set()).add(full)
    for full, types in b_map.items():
        b_norm.setdefault(key(full, types), set()).add(full)

    removed: Set[str] = set()
    for k in a_norm.keys() - b_norm.keys():
        removed |= a_norm[k]
    added: Set[str] = set()
    for k in b_norm.keys() - a_norm.keys():
        added |= b_norm[k]

    # Type-changed: same basename exists but type set differs.
    a_by_base: Dict[str, Set[Tuple[str, ...]]] = {}
    b_by_base: Dict[str, Set[Tuple[str, ...]]] = {}
    for base, tys in a_norm.keys():
        a_by_base.setdefault(base, set()).add(tys)
    for base, tys in b_norm.keys():
        b_by_base.setdefault(base, set()).add(tys)
    changed: Set[str] = {base for base in set(a_by_base) & set(b_by_base) if a_by_base[base] != b_by_base[base]}

    # Rename detection: same type-set, similar name, mutual-best + margin.
    renames: List[Tuple[str, str]] = []
    a_by_type: Dict[Tuple[str, ...], List[str]] = {}
    b_by_type: Dict[Tuple[str, ...], List[str]] = {}
    for full, types in a_map.items():
        a_by_type.setdefault(tuple(sorted(types or [])), []).append(full)
    for full, types in b_map.items():
        b_by_type.setdefault(tuple(sorted(types or [])), []).append(full)

    for tys in set(a_by_type) & set(b_by_type):
        a_items = a_by_type[tys]
        b_items = b_by_type[tys]
        a_only = [n for n in a_items if n not in b_items]
        b_only = [n for n in b_items if n not in a_items]
        if not a_only or not b_only:
            continue

        # For small sets, compute the best total pairing to avoid swap artifacts.
        if len(a_only) <= 6 and len(b_only) <= 6:
            best_pairs: List[Tuple[str, str]] = []
            best_score = -1.0
            shorter, longer = (a_only, b_only) if len(a_only) <= len(b_only) else (b_only, a_only)
            for subset in itertools.permutations(longer, len(shorter)):
                score = 0.0
                pairs: List[Tuple[str, str]] = []
                ok = True
                for x, y in zip(shorter, subset):
                    old_name, new_name = (x, y) if len(a_only) <= len(b_only) else (y, x)
                    sim = name_similarity(old_name, new_name)
                    if sim < _RENAME_SIM_THRESHOLD:
                        ok = False
                        break
                    score += sim
                    pairs.append((old_name, new_name))
                if ok and score > best_score:
                    best_score = score
                    best_pairs = pairs
            renames.extend(best_pairs)
            continue

        best_for_old: Dict[str, Tuple[Optional[str], float, float]] = {}
        for old_name in a_only:
            best_new: Optional[str] = None
            best_s = -1.0
            second_s = -1.0
            for new_name in b_only:
                sim = name_similarity(old_name, new_name)
                if sim > best_s:
                    second_s = best_s
                    best_s = sim
                    best_new = new_name
                elif sim > second_s:
                    second_s = sim
            best_for_old[old_name] = (best_new, best_s, second_s)

        best_for_new: Dict[str, Tuple[Optional[str], float]] = {}
        for new_name in b_only:
            best_old: Optional[str] = None
            best_s = -1.0
            for old_name in a_only:
                sim = name_similarity(old_name, new_name)
                if sim > best_s:
                    best_s = sim
                    best_old = old_name
            best_for_new[new_name] = (best_old, best_s)

        rename_cands: List[Tuple[float, str, str]] = []
        for old_name, (new_name, best_s, second_s) in best_for_old.items():
            if not new_name or best_s < _RENAME_SIM_THRESHOLD:
                continue
            second = second_s if second_s >= 0 else 0.0
            if (best_s - second) < _RENAME_SIM_MARGIN:
                continue
            bo, _ = best_for_new.get(new_name, (None, -1.0))
            if bo != old_name:
                continue
            rename_cands.append((best_s, old_name, new_name))

        rename_cands.sort(reverse=True, key=lambda x: x[0])
        used_old: Set[str] = set()
        used_new: Set[str] = set()
        for sim, old_name, new_name in rename_cands:
            if old_name in used_old or new_name in used_new:
                continue
            renames.append((old_name, new_name))
            used_old.add(old_name)
            used_new.add(new_name)

    # Remove renamed items from added/removed.
    removed -= {o for o, _ in renames}
    added -= {n for _, n in renames}

    return removed, added, changed, renames


def edge_set(nodes: List[Dict], *, ignored_topics: Set[str] = frozenset()) -> Set[Tuple[str, str, str]]:
    """Build the set of (publisher_fq, subscriber_fq, topic) edges."""
    publishers_by_topic: Dict[str, Set[str]] = {}
    subscribers_by_topic: Dict[str, Set[str]] = {}
    for n in nodes:
        fq = n.get("fq_name", "")
        for t in (n.get("publishers") or {}).keys():
            if t not in ignored_topics:
                publishers_by_topic.setdefault(t, set()).add(fq)
        for t in (n.get("subscribers") or {}).keys():
            if t not in ignored_topics:
                subscribers_by_topic.setdefault(t, set()).add(fq)

    edges: Set[Tuple[str, str, str]] = set()
    for topic, pubs in publishers_by_topic.items():
        subs = subscribers_by_topic.get(topic, set())
        for p in pubs:
            for s in subs:
                edges.add((p, s, topic))
    return edges


def remap_old_edges(edges: Set[Tuple[str, str, str]], mapping: Dict[str, str]) -> Set[Tuple[str, str, str]]:
    """Translate old node names in edges via the node mapping."""
    return {(mapping.get(p, p), mapping.get(s, s), t) for p, s, t in edges}


def diff_component_info(
    old_info: Optional[Dict],
    new_info: Optional[Dict],
) -> Optional[Dict]:
    """Return a diff dict when component_info changed, else None."""
    if old_info == new_info:
        return None
    return {
        "old_container": (old_info or {}).get("container"),
        "new_container": (new_info or {}).get("container"),
        "old_id": (old_info or {}).get("component_id"),
        "new_id": (new_info or {}).get("component_id"),
    }


def diff_process_info(
    old_proc: Optional[Dict],
    new_proc: Optional[Dict],
) -> Optional[Dict]:
    """Return a diff dict when process info changed in a meaningful way.

    Ephemeral fields (PID, cmdline, ros_libraries) are intentionally ignored
    because they will always differ between snapshots.
    Returns None when nothing significant changed.
    """
    if old_proc is None and new_proc is None:
        return None
    if old_proc is None:
        return {
            "gained": True,
            "executor_type": new_proc.get("executor_type"),
            "package": new_proc.get("package"),
        }
    if new_proc is None:
        return {"lost": True}

    diff: Dict = {}
    for field in ("executor_type", "package"):
        ov, nv = old_proc.get(field), new_proc.get(field)
        if ov != nv:
            diff[field] = {"old": ov, "new": nv}
    # Compare exe by basename only — install prefix may legitimately differ.
    old_exe_base = os.path.basename(old_proc.get("exe") or "")
    new_exe_base = os.path.basename(new_proc.get("exe") or "")
    if old_exe_base != new_exe_base:
        diff["exe"] = {"old": old_exe_base, "new": new_exe_base}
    return diff if diff else None
