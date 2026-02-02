#!/usr/bin/env python3

import argparse
import json
import os
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple
import itertools

from ros2_topology_common import (
    Signature as NodeSignature,
    freeze_map,
    jaccard,
    signature_from_node,
    signature_id,
)


_IGNORED_TOPICS: Set[str] = set()
_RENAME_SIM_THRESHOLD = 0.70
_RENAME_SIM_MARGIN = 0.12


def _basename(name: str) -> str:
    # Keep only the last path segment for namespace-insensitive comparisons.
    # Examples:
    # - /simulation/simple_planning_simulator -> simple_planning_simulator
    # - /perception/occupancy_grid_map/.../laserscan -> laserscan
    if not name:
        return ""
    s = name.rstrip("/")
    if "/" not in s:
        return s
    return s.rsplit("/", 1)[-1]


def _name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a_base = _basename(a)
    b_base = _basename(b)
    return max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a_base, b_base).ratio(),
    )


def _signature(node: Dict) -> NodeSignature:
    return signature_from_node(node)


def _sig_id(sig: NodeSignature) -> str:
    # Stable hash for grouping; short for readability.
    return signature_id(sig)


def _topic_index(nodes: List[Dict]) -> Dict[str, Dict[str, List[str]]]:
    # topic -> {"publishers": [fq...], "subscribers": [fq...]}
    idx: Dict[str, Dict[str, List[str]]] = {}
    for n in nodes:
        fq = n.get("fq_name", "")
        for t in (n.get("publishers") or {}).keys():
            idx.setdefault(t, {"publishers": [], "subscribers": []})["publishers"].append(fq)
        for t in (n.get("subscribers") or {}).keys():
            idx.setdefault(t, {"publishers": [], "subscribers": []})["subscribers"].append(fq)
    return idx


def _node_endpoints(node: Dict) -> Set[str]:
    # Endpoint tokens for similarity matching.
    # Focus is pub/sub topic sets; include type when available.
    tokens: Set[str] = set()
    for topic, types in (node.get("publishers") or {}).items():
        if topic in _IGNORED_TOPICS:
            continue
        b = _basename(topic)
        if types:
            for ty in types:
                tokens.add(f"P:{topic}:{ty}")
                tokens.add(f"P_B:{b}:{ty}")
        else:
            tokens.add(f"P:{topic}")
            tokens.add(f"P_B:{b}")
    for topic, types in (node.get("subscribers") or {}).items():
        if topic in _IGNORED_TOPICS:
            continue
        b = _basename(topic)
        if types:
            for ty in types:
                tokens.add(f"S:{topic}:{ty}")
                tokens.add(f"S_B:{b}:{ty}")
        else:
            tokens.add(f"S:{topic}")
            tokens.add(f"S_B:{b}")

    # Services/clients are usually less central to topology, but help disambiguate.
    for srv, types in (node.get("services") or {}).items():
        b = _basename(srv)
        if types:
            for ty in types:
                tokens.add(f"SV:{srv}:{ty}")
                tokens.add(f"SV_B:{b}:{ty}")
        else:
            tokens.add(f"SV:{srv}")
            tokens.add(f"SV_B:{b}")
    for cli, types in (node.get("clients") or {}).items():
        b = _basename(cli)
        if types:
            for ty in types:
                tokens.add(f"CL:{cli}:{ty}")
                tokens.add(f"CL_B:{b}:{ty}")
        else:
            tokens.add(f"CL:{cli}")
            tokens.add(f"CL_B:{b}")
    return tokens


def _normalized_signature(node: Dict) -> NodeSignature:
    # Name-insensitive signature: compare by endpoint basename + type list.
    def norm_map(m: Dict[str, List[str]]) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for k, v in (m or {}).items():
            if k in _IGNORED_TOPICS:
                continue
            out[_basename(k)] = v or []
        return out

    return NodeSignature(
        pubs=freeze_map(norm_map(node.get("publishers", {}))),
        subs=freeze_map(norm_map(node.get("subscribers", {}))),
        srvs=freeze_map(norm_map(node.get("services", {}))),
        clis=freeze_map(norm_map(node.get("clients", {}))),
    )


def _node_type_tokens(node: Dict) -> Set[str]:
    """Direction-aware message-type tokens.

    This is intentionally topic-name agnostic to pair nodes whose topics were renamed
    (e.g., namespace move) but which still publish/subscribe the same types.
    """
    tokens: Set[str] = set()
    for _, types in (node.get("publishers") or {}).items():
        for ty in (types or []):
            tokens.add(f"PT:{ty}")
    for _, types in (node.get("subscribers") or {}).items():
        for ty in (types or []):
            tokens.add(f"ST:{ty}")
    for _, types in (node.get("services") or {}).items():
        for ty in (types or []):
            tokens.add(f"SVT:{ty}")
    for _, types in (node.get("clients") or {}).items():
        for ty in (types or []):
            tokens.add(f"CLT:{ty}")
    return tokens


def _param_info(param_map: Dict[str, List[str]], fq: str) -> Tuple[Set[str], Optional[str]]:
    if not param_map:
        return set(), None
    vals = param_map.get(fq)
    if vals is None:
        return set(), "no param entry"
    if vals and vals[0].startswith("<"):
        return set(), vals[0]
    return set(vals), None


def _param_tokens(param_map: Dict[str, List[str]], fq: str) -> Set[str]:
    names, status = _param_info(param_map, fq)
    if status:
        return set()
    return {f"PRM:{n}" for n in names}


def _param_value_info(param_values: Dict[str, Dict[str, str]], fq: str) -> Tuple[Dict[str, str], Optional[str]]:
    if not param_values:
        return {}, None
    vals = param_values.get(fq)
    if vals is None:
        return {}, "no param values entry"
    if vals:
        for k in vals.keys():
            if k.startswith("<"):
                return {}, k
    return vals, None


def _match_score(
    old_fq: str,
    new_fq: str,
    *,
    old_type: Set[str],
    new_type: Set[str],
    old_end: Set[str],
    new_end: Set[str],
    old_param: Set[str],
    new_param: Set[str],
) -> float:
    type_sim = jaccard(old_type, new_type)
    end_sim = jaccard(old_end, new_end)
    name_sim = _name_similarity(old_fq, new_fq)
    param_sim = jaccard(old_param, new_param) if (old_param or new_param) else 0.0

    # Weighted blend: keep topology dominant, but allow name/params to disambiguate.
    w_type = 0.50
    w_end = 0.30
    w_name = 0.15
    w_param = 0.05 if (old_param or new_param) else 0.0
    w_sum = w_type + w_end + w_name + w_param
    if w_sum == 0:
        return 0.0
    return (w_type * type_sim + w_end * end_sim + w_name * name_sim + w_param * param_sim) / w_sum


def _load_graph(path: str) -> Tuple[Dict, List[Dict]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data, data.get("nodes", []) or []


def _match_nodes(
    old_nodes: List[Dict],
    new_nodes: List[Dict],
    *,
    min_similarity: float,
    min_margin: float,
    old_params: Dict[str, List[str]],
    new_params: Dict[str, List[str]],
) -> Tuple[Dict[str, str], List[Tuple[str, str, float]]]:
    """Return mapping old_fq_name -> new_fq_name.

    Matching rules:
    - Exact pub/sub/srv/cli signature match pairs immediately (name-agnostic).
    - Otherwise, match by endpoint-set Jaccard similarity.
    - Uniqueness constraint: mutual best match and margin to 2nd-best >= min_margin.
    """

    old_by_fq = {n.get("fq_name", ""): n for n in old_nodes}
    new_by_fq = {n.get("fq_name", ""): n for n in new_nodes}

    sid_to_old: Dict[str, List[str]] = {}
    sid_to_new: Dict[str, List[str]] = {}
    nsid_to_old: Dict[str, List[str]] = {}
    nsid_to_new: Dict[str, List[str]] = {}
    for fq, n in old_by_fq.items():
        sid_to_old.setdefault(_sig_id(_signature(n)), []).append(fq)
        nsid_to_old.setdefault(_sig_id(_normalized_signature(n)), []).append(fq)
    for fq, n in new_by_fq.items():
        sid_to_new.setdefault(_sig_id(_signature(n)), []).append(fq)
        nsid_to_new.setdefault(_sig_id(_normalized_signature(n)), []).append(fq)

    mapping: Dict[str, str] = {}
    matched_new: Set[str] = set()
    evidence: List[Tuple[str, str, float]] = []

    # 0) Same fq_name pairs (fast-path; keeps diffs focused on topology changes).
    for fq in sorted(set(old_by_fq.keys()) & set(new_by_fq.keys())):
        mapping[fq] = fq
        matched_new.add(fq)
        evidence.append(
            (
                fq,
                fq,
                _match_score(
                    fq,
                    fq,
                    old_type=_node_type_tokens(old_by_fq[fq]),
                    new_type=_node_type_tokens(new_by_fq[fq]),
                    old_end=_node_endpoints(old_by_fq[fq]),
                    new_end=_node_endpoints(new_by_fq[fq]),
                    old_param=_param_tokens(old_params, fq),
                    new_param=_param_tokens(new_params, fq),
                ),
            )
        )

    # 1) Exact signature pairing where unambiguous.
    for sid, olds in sid_to_old.items():
        news = sid_to_new.get(sid, [])
        if len(olds) == 1 and len(news) == 1:
            ofq = olds[0]
            nfq = news[0]
            if ofq in mapping or nfq in matched_new:
                continue
            mapping[ofq] = nfq
            matched_new.add(nfq)
            evidence.append((ofq, nfq, 1.0))

    # 1.5) Normalized signature pairing (basename+types) where unambiguous.
    for nsid, olds in nsid_to_old.items():
        news = nsid_to_new.get(nsid, [])
        if len(olds) == 1 and len(news) == 1:
            ofq = olds[0]
            nfq = news[0]
            if ofq in mapping or nfq in matched_new:
                continue
            mapping[ofq] = nfq
            matched_new.add(nfq)
            evidence.append((ofq, nfq, jaccard(_node_endpoints(old_by_fq[ofq]), _node_endpoints(new_by_fq[nfq]))))

    rem_old = [fq for fq in old_by_fq.keys() if fq not in mapping]
    rem_new = [fq for fq in new_by_fq.keys() if fq not in matched_new]

    # Fuzzy matching uses blended similarity (types, endpoints, name, parameters).
    old_type = {fq: _node_type_tokens(old_by_fq[fq]) for fq in rem_old}
    new_type = {fq: _node_type_tokens(new_by_fq[fq]) for fq in rem_new}
    old_end = {fq: _node_endpoints(old_by_fq[fq]) for fq in rem_old}
    new_end = {fq: _node_endpoints(new_by_fq[fq]) for fq in rem_new}
    old_param = {fq: _param_tokens(old_params, fq) for fq in rem_old}
    new_param = {fq: _param_tokens(new_params, fq) for fq in rem_new}

    # 2) Similarity-based mutual best matching.
    old_best: Dict[str, Tuple[Optional[str], float, float]] = {}
    for ofq in rem_old:
        best_n: Optional[str] = None
        best_s = -1.0
        second_s = -1.0
        ot = old_type[ofq]
        for nfq in rem_new:
            s = _match_score(
                ofq,
                nfq,
                old_type=ot,
                new_type=new_type[nfq],
                old_end=old_end[ofq],
                new_end=new_end[nfq],
                old_param=old_param[ofq],
                new_param=new_param[nfq],
            )
            if s > best_s:
                second_s = best_s
                best_s = s
                best_n = nfq
            elif s > second_s:
                second_s = s
        old_best[ofq] = (best_n, best_s, second_s)

    new_best: Dict[str, Tuple[Optional[str], float]] = {}
    for nfq in rem_new:
        best_o: Optional[str] = None
        best_s = -1.0
        nt = new_type[nfq]
        for ofq in rem_old:
            s = _match_score(
                ofq,
                nfq,
                old_type=old_type[ofq],
                new_type=nt,
                old_end=old_end[ofq],
                new_end=new_end[nfq],
                old_param=old_param[ofq],
                new_param=new_param[nfq],
            )
            if s > best_s:
                best_s = s
                best_o = ofq
        new_best[nfq] = (best_o, best_s)

    candidates: List[Tuple[float, str, str]] = []
    for ofq, (nfq, s, s2) in old_best.items():
        if nfq is None:
            continue
        if s < min_similarity:
            continue
        second = s2 if s2 >= 0 else 0.0
        if (s - second) < min_margin:
            continue
        bo, _ = new_best.get(nfq, (None, -1.0))
        if bo != ofq:
            continue
        candidates.append((s, ofq, nfq))

    candidates.sort(reverse=True, key=lambda x: x[0])
    used_old: Set[str] = set(mapping.keys())
    used_new: Set[str] = set(matched_new)
    for s, ofq, nfq in candidates:
        if ofq in used_old or nfq in used_new:
            continue
        mapping[ofq] = nfq
        used_old.add(ofq)
        used_new.add(nfq)
        evidence.append((ofq, nfq, s))

    return mapping, evidence


def _diff_maps(
    a: Dict[str, List[str]],
    b: Dict[str, List[str]],
) -> Tuple[Set[str], Set[str], Set[str], List[Tuple[str, str]]]:
    # Compare by basename+type-set first to suppress pure namespace/path renames.
    a_map = {k: (a or {}).get(k) or [] for k in ((a or {}).keys()) if k not in _IGNORED_TOPICS}
    b_map = {k: (b or {}).get(k) or [] for k in ((b or {}).keys()) if k not in _IGNORED_TOPICS}

    def key(full: str, types: List[str]) -> Tuple[str, Tuple[str, ...]]:
        return (_basename(full), tuple(sorted(types or [])))

    a_norm: Dict[Tuple[str, Tuple[str, ...]], Set[str]] = {}
    b_norm: Dict[Tuple[str, Tuple[str, ...]], Set[str]] = {}
    for full, types in a_map.items():
        a_norm.setdefault(key(full, types), set()).add(full)
    for full, types in b_map.items():
        b_norm.setdefault(key(full, types), set()).add(full)

    a_keys = set(a_norm.keys())
    b_keys = set(b_norm.keys())
    removed_norm = a_keys - b_keys
    added_norm = b_keys - a_keys

    # Expose removed/added as representative full names, but suppress when equivalent exists.
    removed: Set[str] = set()
    for k in removed_norm:
        removed |= a_norm.get(k, set())
    added: Set[str] = set()
    for k in added_norm:
        added |= b_norm.get(k, set())

    # Type-changed: same basename exists, but type set differs.
    a_by_base: Dict[str, Set[Tuple[str, ...]]] = {}
    b_by_base: Dict[str, Set[Tuple[str, ...]]] = {}
    for (base, tys) in a_norm.keys():
        a_by_base.setdefault(base, set()).add(tys)
    for (base, tys) in b_norm.keys():
        b_by_base.setdefault(base, set()).add(tys)

    changed: Set[str] = set()
    for base in set(a_by_base.keys()) & set(b_by_base.keys()):
        if a_by_base[base] != b_by_base[base]:
            changed.add(base)

    # Rename detection for same type-set and similar name, with mutual-best + margin.
    renames: List[Tuple[str, str]] = []
    a_by_type: Dict[Tuple[str, ...], List[str]] = {}
    b_by_type: Dict[Tuple[str, ...], List[str]] = {}
    for full, types in a_map.items():
        a_by_type.setdefault(tuple(sorted(types or [])), []).append(full)
    for full, types in b_map.items():
        b_by_type.setdefault(tuple(sorted(types or [])), []).append(full)

    for tys in set(a_by_type.keys()) & set(b_by_type.keys()):
        a_items = a_by_type[tys]
        b_items = b_by_type[tys]
        # If the same full names exist on both sides, they are not renames.
        a_only = [n for n in a_items if n not in b_items]
        b_only = [n for n in b_items if n not in a_items]
        if not a_only or not b_only:
            continue
        if not a_items or not b_items:
            continue

        # For small sets, compute the best total pairing to avoid swap artifacts.
        if len(a_only) <= 6 and len(b_only) <= 6:
            best_pairs: List[Tuple[str, str]] = []
            best_score = -1.0
            if len(a_only) <= len(b_only):
                for subset in itertools.permutations(b_only, len(a_only)):
                    score = 0.0
                    pairs: List[Tuple[str, str]] = []
                    ok = True
                    for old_name, new_name in zip(a_only, subset):
                        sim = _name_similarity(old_name, new_name)
                        if sim < _RENAME_SIM_THRESHOLD:
                            ok = False
                            break
                        score += sim
                        pairs.append((old_name, new_name))
                    if ok and score > best_score:
                        best_score = score
                        best_pairs = pairs
            else:
                for subset in itertools.permutations(a_only, len(b_only)):
                    score = 0.0
                    pairs = []
                    ok = True
                    for old_name, new_name in zip(subset, b_only):
                        sim = _name_similarity(old_name, new_name)
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
                sim = _name_similarity(old_name, new_name)
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
                sim = _name_similarity(old_name, new_name)
                if sim > best_s:
                    best_s = sim
                    best_old = old_name
            best_for_new[new_name] = (best_old, best_s)

        candidates: List[Tuple[float, str, str]] = []
        for old_name, (new_name, best_s, second_s) in best_for_old.items():
            if not new_name:
                continue
            if best_s < _RENAME_SIM_THRESHOLD:
                continue
            second = second_s if second_s >= 0 else 0.0
            if (best_s - second) < _RENAME_SIM_MARGIN:
                continue
            bo, _ = best_for_new.get(new_name, (None, -1.0))
            if bo != old_name:
                continue
            candidates.append((best_s, old_name, new_name))

        candidates.sort(reverse=True, key=lambda x: x[0])
        used_old: Set[str] = set()
        used_new: Set[str] = set()
        for sim, old_name, new_name in candidates:
            if old_name in used_old or new_name in used_new:
                continue
            renames.append((old_name, new_name))
            used_old.add(old_name)
            used_new.add(new_name)

    # Remove renamed items from added/removed.
    renamed_old = {o for o, _ in renames}
    renamed_new = {n for _, n in renames}
    removed -= renamed_old
    added -= renamed_new

    return removed, added, changed, renames


def _edge_set(nodes: List[Dict]) -> Set[Tuple[str, str, str]]:
    # (pub_node_fq, sub_node_fq, topic)
    publishers_by_topic: Dict[str, Set[str]] = {}
    subscribers_by_topic: Dict[str, Set[str]] = {}
    for n in nodes:
        fq = n.get("fq_name", "")
        for t in (n.get("publishers") or {}).keys():
            if t in _IGNORED_TOPICS:
                continue
            publishers_by_topic.setdefault(t, set()).add(fq)
        for t in (n.get("subscribers") or {}).keys():
            if t in _IGNORED_TOPICS:
                continue
            subscribers_by_topic.setdefault(t, set()).add(fq)

    edges: Set[Tuple[str, str, str]] = set()
    for topic, pubs in publishers_by_topic.items():
        subs = subscribers_by_topic.get(topic, set())
        if not subs:
            continue
        for p in pubs:
            for s in subs:
                edges.add((p, s, topic))
    return edges


def _remap_old_edges(edges: Set[Tuple[str, str, str]], mapping: Dict[str, str]) -> Set[Tuple[str, str, str]]:
    remapped: Set[Tuple[str, str, str]] = set()
    for p, s, t in edges:
        remapped.add((mapping.get(p, p), mapping.get(s, s), t))
    return remapped


def _filter_transform_listener(nodes: List[Dict], *, include: bool) -> List[Dict]:
    if include:
        return nodes
    return [n for n in nodes if "transform_listener" not in (n.get("fq_name", "") or "")]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Generate a topology-oriented report from ros2_graph_snapshot.py output. "
            "Groups nodes by pub/sub signature to compare systems even when node names differ."
        )
    )
    ap.add_argument(
        "graph_json",
        nargs="+",
        help="Path(s) to graph.json generated by ros2_graph_snapshot.py (1=report, 2=diff)",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output report path. Default: alongside graph_json as topology.md (or topology_diff.md)",
    )
    ap.add_argument(
        "--max-groups",
        type=int,
        default=50,
        help="Max signature groups to print in detail (0 = no limit).",
    )
    ap.add_argument(
        "--max-nodes-per-group",
        type=int,
        default=10,
        help="Max node fq_names to show per signature group.",
    )
    ap.add_argument(
        "--topic-focus",
        default=None,
        help="Regex filter for topics to include in the topic index section.",
    )
    ap.add_argument(
        "--min-similarity",
        type=float,
        default=0.70,
        help="Min Jaccard similarity for fuzzy node matching (diff mode).",
    )
    ap.add_argument(
        "--min-margin",
        type=float,
        default=0.10,
        help="Min margin vs 2nd-best candidate for uniqueness (diff mode).",
    )
    ap.add_argument(
        "--include-transform-listener",
        action="store_true",
        help="Include transform_listener* nodes (default: ignored).",
    )
    ap.add_argument(
        "--include-parameter-events",
        action="store_true",
        help="Include /parameter_events in matching and diffs (default: ignored).",
    )

    args = ap.parse_args()

    global _IGNORED_TOPICS
    _IGNORED_TOPICS = set() if args.include_parameter_events else {"/parameter_events"}

    if len(args.graph_json) not in (1, 2):
        raise SystemExit("Expected 1 (report) or 2 (diff) graph_json paths")

    # Single-snapshot report mode.
    if len(args.graph_json) == 1:
        data, nodes = _load_graph(args.graph_json[0])
        nodes = _filter_transform_listener(nodes, include=args.include_transform_listener)

        # Group nodes by signature.
        groups: Dict[str, Dict] = {}
        for n in nodes:
            sig = _signature(n)
            sid = _sig_id(sig)
            g = groups.setdefault(
                sid,
                {
                    "count": 0,
                    "sig": sig,
                    "examples": [],
                },
            )
            g["count"] += 1
            if len(g["examples"]) < max(1, args.max_nodes_per_group):
                g["examples"].append(n.get("fq_name", ""))

        # Sort groups by size desc, then id.
        sorted_groups = sorted(groups.items(), key=lambda kv: (-kv[1]["count"], kv[0]))

        # Topic index (useful to compare pub/sub structure).
        t_idx = _topic_index(nodes)
        topic_items = sorted(t_idx.items(), key=lambda kv: kv[0])

        out_path = args.out
        if out_path is None:
            out_path = os.path.join(os.path.dirname(os.path.abspath(args.graph_json[0])), "topology.md")

        lines: List[str] = []
        lines.append(f"# ROS 2 Topology Report\n")
        lines.append(f"- Source: {os.path.abspath(args.graph_json[0])}")
        lines.append(f"- Timestamp: {data.get('timestamp', '')}")
        lines.append(f"- Nodes (processed): {len(nodes)}")
        if not args.include_transform_listener:
            lines.append("- Filter: ignored nodes containing 'transform_listener'")
        if not args.include_parameter_events:
            lines.append("- Filter: ignored topic '/parameter_events'")
        lines.append(f"- Signature groups: {len(sorted_groups)}")
        dup = data.get("duplicates", []) or []
        lines.append(f"- Duplicate node names: {len(dup)}")
        if dup:
            lines.append("  - Examples:")
            for d in dup[:10]:
                lines.append(f"    - {d}")
        lines.append("")

        lines.append("## Signature Groups (name-agnostic)\n")
        if args.max_groups and args.max_groups > 0:
            show_groups = sorted_groups[: args.max_groups]
        else:
            show_groups = sorted_groups

        for sid, g in show_groups:
            sig: NodeSignature = g["sig"]
            lines.append(f"### {sid} (count={g['count']})")
            if g["examples"]:
                ex = ", ".join(g["examples"][: args.max_nodes_per_group])
                lines.append(f"- example nodes: {ex}")
            lines.append(
                f"- pubs: {len(sig.pubs)}  subs: {len(sig.subs)}  srvs: {len(sig.srvs)}  clis: {len(sig.clis)}"
            )
            if sig.pubs:
                lines.append("- publish topics:")
                for t, types in sig.pubs[:30]:
                    ty = ", ".join(types) if types else "<unknown>"
                    lines.append(f"  - {t} :: {ty}")
            if sig.subs:
                lines.append("- subscribe topics:")
                for t, types in sig.subs[:30]:
                    ty = ", ".join(types) if types else "<unknown>"
                    lines.append(f"  - {t} :: {ty}")
            lines.append("")

        lines.append("## Topic Index (publishers/subscribers counts)\n")
        for topic, ps in topic_items:
            pubs = ps.get("publishers", [])
            subs = ps.get("subscribers", [])
            lines.append(f"- {topic}: pubs={len(pubs)} subs={len(subs)}")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        print(out_path)
        return 0

    # Two-snapshot diff mode.
    old_path, new_path = args.graph_json[0], args.graph_json[1]
    old_data, old_nodes = _load_graph(old_path)
    new_data, new_nodes = _load_graph(new_path)
    old_nodes = _filter_transform_listener(old_nodes, include=args.include_transform_listener)
    new_nodes = _filter_transform_listener(new_nodes, include=args.include_transform_listener)

    old_params = old_data.get("param_names", {}) or {}
    new_params = new_data.get("param_names", {}) or {}
    old_param_values = old_data.get("param_values", {}) or {}
    new_param_values = new_data.get("param_values", {}) or {}

    param_enabled = bool(old_params or new_params)
    param_values_enabled = bool(old_param_values or new_param_values)

    mapping, evidence = _match_nodes(
        old_nodes,
        new_nodes,
        min_similarity=args.min_similarity,
        min_margin=args.min_margin,
        old_params=old_params,
        new_params=new_params,
    )

    old_by_fq = {n.get("fq_name", ""): n for n in old_nodes}
    new_by_fq = {n.get("fq_name", ""): n for n in new_nodes}
    matched_old = set(mapping.keys())
    matched_new = set(mapping.values())
    removed_nodes = sorted(set(old_by_fq.keys()) - matched_old)
    added_nodes = sorted(set(new_by_fq.keys()) - matched_new)

    changed_nodes: List[Tuple[str, str, Dict[str, object]]] = []
    for ofq, nfq in mapping.items():
        o = old_by_fq.get(ofq, {})
        n = new_by_fq.get(nfq, {})
        pubs = _diff_maps(o.get("publishers", {}), n.get("publishers", {}))
        subs = _diff_maps(o.get("subscribers", {}), n.get("subscribers", {}))
        srvs = _diff_maps(o.get("services", {}), n.get("services", {}))
        clis = _diff_maps(o.get("clients", {}), n.get("clients", {}))

        param_diff = None
        if param_enabled:
            o_params, o_param_status = _param_info(old_params, ofq)
            n_params, n_param_status = _param_info(new_params, nfq)
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
            o_vals, o_val_status = _param_value_info(old_param_values, ofq)
            n_vals, n_val_status = _param_value_info(new_param_values, nfq)
            if not o_val_status and not n_val_status:
                changed = []
                for k in sorted(set(o_vals.keys()) | set(n_vals.keys())):
                    if o_vals.get(k) != n_vals.get(k):
                        changed.append((k, o_vals.get(k), n_vals.get(k)))
            else:
                changed = []
            value_diff = {
                "changed": changed,
                "old_status": o_val_status,
                "new_status": n_val_status,
            }

        if (
            pubs[0]
            or pubs[1]
            or pubs[2]
            or pubs[3]
            or subs[0]
            or subs[1]
            or subs[2]
            or subs[3]
            or srvs[0]
            or srvs[1]
            or srvs[2]
            or srvs[3]
            or clis[0]
            or clis[1]
            or clis[2]
            or clis[3]
            or (param_diff and (param_diff["removed"] or param_diff["added"] or param_diff["old_status"] or param_diff["new_status"]))
            or (value_diff and (value_diff["changed"] or value_diff["old_status"] or value_diff["new_status"]))
        ):
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
                    },
                )
            )

    old_edges = _remap_old_edges(_edge_set(old_nodes), mapping)
    new_edges = _edge_set(new_nodes)
    removed_edges = sorted(old_edges - new_edges)
    added_edges = sorted(new_edges - old_edges)

    out_path = args.out
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(new_path)), "topology_diff.md")

    lines: List[str] = []
    lines.append("# ROS 2 Topology Diff (name-agnostic)\n")
    lines.append(f"- Old: {os.path.abspath(old_path)}")
    lines.append(f"- New: {os.path.abspath(new_path)}")
    lines.append(f"- Old timestamp: {old_data.get('timestamp', '')}")
    lines.append(f"- New timestamp: {new_data.get('timestamp', '')}")
    lines.append(f"- Old nodes: {len(old_nodes)}")
    lines.append(f"- New nodes: {len(new_nodes)}")
    if not args.include_transform_listener:
        lines.append("- Filter: ignored nodes containing 'transform_listener'")
    if not args.include_parameter_events:
        lines.append("- Filter: ignored topic '/parameter_events'")
    if param_enabled:
        lines.append("- Parameters: compared by name (param_names)")
    if param_values_enabled:
        lines.append("- Parameters: compared by value (param_values)")
    lines.append(f"- Matched node pairs: {len(mapping)}")
    lines.append(f"- Added nodes (unmatched): {len(added_nodes)}")
    lines.append(f"- Removed nodes (unmatched): {len(removed_nodes)}")
    lines.append("")

    lines.append("## Matching summary\n")
    evidence_sorted = sorted(evidence, key=lambda x: (-x[2], x[0], x[1]))
    for ofq, nfq, s in evidence_sorted[:50]:
        suffix = "" if ofq == nfq else ", renamed"
        lines.append(f"- {ofq} -> {nfq} (sim={s:.2f}{suffix})")
    if len(evidence_sorted) > 50:
        lines.append(f"- ... {len(evidence_sorted) - 50} more matched pairs")
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
        for ofq, nfq, diffs in sorted(changed_nodes, key=lambda x: x[0])[:80]:
            lines.append(f"### {ofq} -> {nfq}")
            for kind in ("publishers", "subscribers", "services", "clients"):
                removed, added, changed, renamed = diffs[kind]
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
            param_diff = diffs.get("parameters") if isinstance(diffs, dict) else None
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
            value_diff = diffs.get("parameter_values") if isinstance(diffs, dict) else None
            if value_diff:
                lines.append("- parameter values:")
                if value_diff.get("old_status"):
                    lines.append(f"  - old: {value_diff['old_status']}")
                if value_diff.get("new_status"):
                    lines.append(f"  - new: {value_diff['new_status']}")
                for k, ov, nv in value_diff.get("changed", [])[:30]:
                    o_str = "<unset>" if ov is None else ov
                    n_str = "<unset>" if nv is None else nv
                    lines.append(f"  - changed: {k} :: {o_str} -> {n_str}")
            lines.append("")
        if len(changed_nodes) > 80:
            lines.append(f"- ... {len(changed_nodes) - 80} more changed matched nodes")
        lines.append("")

    lines.append("## Edge-level changes (pub -> sub on topic)\n")
    lines.append(f"- Added edges: {len(added_edges)}")
    lines.append(f"- Removed edges: {len(removed_edges)}")
    lines.append("")
    for p, s, t in added_edges[:80]:
        lines.append(f"- + {p} -> {s} : {t}")
    if len(added_edges) > 80:
        lines.append(f"- ... {len(added_edges) - 80} more added edges")
    lines.append("")
    for p, s, t in removed_edges[:80]:
        lines.append(f"- - {p} -> {s} : {t}")
    if len(removed_edges) > 80:
        lines.append(f"- ... {len(removed_edges) - 80} more removed edges")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
