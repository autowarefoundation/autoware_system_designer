#!/usr/bin/env python3

import argparse
import itertools
import json
import os
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple

from functions.ros2_topology_common import Signature as NodeSignature
from functions.ros2_topology_common import (
    freeze_map,
    jaccard,
    signature_from_node,
    signature_id,
)

_IGNORED_TOPICS: Set[str] = set()
_RENAME_SIM_THRESHOLD = 0.70
_RENAME_SIM_MARGIN = 0.12

# Standard ROS 2 parameter management service suffixes — derivative of node name, suppress as rename noise.
_PARAM_SVC_SUFFIXES: Set[str] = {
    "describe_parameters",
    "get_parameter_types",
    "get_parameters",
    "list_parameters",
    "set_parameters",
    "set_parameters_atomically",
}

# Tool-internal nodes that should be excluded from system comparisons by default.
_TOOL_NODE_RE = re.compile(r"^/graph_snapshot$|^/launch_ros_\d+$")

# Topics present on virtually every ROS 2 node — hide from single-report display by default.
_COMMON_TOPICS: Set[str] = {"/rosout", "/clock", "/parameter_events"}


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
        for ty in types or []:
            tokens.add(f"PT:{ty}")
    for _, types in (node.get("subscribers") or {}).items():
        for ty in types or []:
            tokens.add(f"ST:{ty}")
    for _, types in (node.get("services") or {}).items():
        for ty in types or []:
            tokens.add(f"SVT:{ty}")
    for _, types in (node.get("clients") or {}).items():
        for ty in types or []:
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
    old_param: Set[str],
    new_param: Set[str],
) -> float:
    type_sim = jaccard(old_type, new_type)
    name_sim = _name_similarity(old_fq, new_fq)
    param_sim = jaccard(old_param, new_param) if (old_param or new_param) else 0.0

    # Interface type composition is the primary identity signal: a node's role is
    # defined by the message types it publishes/subscribes/serves, not by topic names.
    # Topic names change freely with namespace remapping or system reconfiguration;
    # container assignment reflects deployment config, not functional identity.
    w_type = 0.75
    w_name = 0.20
    w_param = 0.05 if (old_param or new_param) else 0.0
    w_sum = w_type + w_name + w_param
    if w_sum == 0:
        return 0.0
    return (w_type * type_sim + w_name * name_sim + w_param * param_sim) / w_sum


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
            evidence.append(
                (
                    ofq,
                    nfq,
                    jaccard(_node_type_tokens(old_by_fq[ofq]), _node_type_tokens(new_by_fq[nfq])),
                )
            )

    # 1.7) Type-composition matching: same direction+type set, any topic names.
    # Catches nodes whose topics were renamed (namespace move, topic remapping)
    # but whose functional interface (message type composition) is identical.
    # Uses a frozenset of direction-qualified type tokens as the grouping key.
    type_to_old: Dict[frozenset, List[str]] = {}
    type_to_new: Dict[frozenset, List[str]] = {}
    for fq, n in old_by_fq.items():
        if fq in mapping:
            continue
        key = frozenset(_node_type_tokens(n))
        if key:  # skip nodes with no type info (e.g. bare parameter servers)
            type_to_old.setdefault(key, []).append(fq)
    for fq, n in new_by_fq.items():
        if fq in matched_new:
            continue
        key = frozenset(_node_type_tokens(n))
        if key:
            type_to_new.setdefault(key, []).append(fq)
    for key, olds in type_to_old.items():
        news = type_to_new.get(key, [])
        if len(olds) == 1 and len(news) == 1:
            ofq, nfq = olds[0], news[0]
            if ofq in mapping or nfq in matched_new:
                continue
            mapping[ofq] = nfq
            matched_new.add(nfq)
            evidence.append(
                (
                    ofq,
                    nfq,
                    _match_score(
                        ofq,
                        nfq,
                        old_type=_node_type_tokens(old_by_fq[ofq]),
                        new_type=_node_type_tokens(new_by_fq[nfq]),
                        old_param=_param_tokens(old_params, ofq),
                        new_param=_param_tokens(new_params, nfq),
                    ),
                )
            )

    # 1.8) Same-type-composition groups, M:N disambiguation by name.
    # Step 1.7 resolved 1:1 groups. Here we handle groups where M old and N new
    # nodes share the same type token frozenset (e.g. multiple identical-driver
    # LiDAR nodes renamed across snapshots). Within such a group type_sim=1.0
    # for every pair, so name similarity is the only discriminator.
    # A small margin (0.01) is kept as a sanity check; the mutual-best constraint
    # is the primary guard against false matches.
    _GROUP_NAME_MARGIN = 0.01
    for key, type_olds in type_to_old.items():
        type_news = type_to_new.get(key, [])
        if len(type_olds) == 1 and len(type_news) == 1:
            continue  # already handled by Step 1.7
        olds_rem = [fq for fq in type_olds if fq not in mapping]
        news_rem = [fq for fq in type_news if fq not in matched_new]
        if not olds_rem or not news_rem:
            continue
        # Build mutual-best name-similarity tables within this type group.
        old_best_g: Dict[str, Tuple[Optional[str], float, float]] = {}
        for ofq in olds_rem:
            g_best_n: Optional[str] = None
            g_best_s = -1.0
            g_second_s = -1.0
            for nfq in news_rem:
                s = _name_similarity(ofq, nfq)
                if s > g_best_s:
                    g_second_s = g_best_s
                    g_best_s = s
                    g_best_n = nfq
                elif s > g_second_s:
                    g_second_s = s
            old_best_g[ofq] = (g_best_n, g_best_s, g_second_s)
        new_best_g: Dict[str, Tuple[Optional[str], float]] = {}
        for nfq in news_rem:
            g_best_o: Optional[str] = None
            g_best_s = -1.0
            for ofq in olds_rem:
                s = _name_similarity(ofq, nfq)
                if s > g_best_s:
                    g_best_s = s
                    g_best_o = ofq
            new_best_g[nfq] = (g_best_o, g_best_s)
        group_cands: List[Tuple[float, str, str]] = []
        for ofq, (g_nfq, g_s, g_s2) in old_best_g.items():
            if g_nfq is None:
                continue
            g_second = g_s2 if g_s2 >= 0 else 0.0
            if (g_s - g_second) < _GROUP_NAME_MARGIN:
                continue
            bo, _ = new_best_g.get(g_nfq, (None, -1.0))
            if bo != ofq:
                continue
            group_cands.append((g_s, ofq, g_nfq))
        group_cands.sort(reverse=True, key=lambda x: x[0])
        used_old_g: Set[str] = set()
        used_new_g: Set[str] = set()
        for _, ofq, nfq in group_cands:
            if ofq in used_old_g or nfq in used_new_g:
                continue
            mapping[ofq] = nfq
            matched_new.add(nfq)
            used_old_g.add(ofq)
            used_new_g.add(nfq)
            evidence.append(
                (
                    ofq,
                    nfq,
                    _match_score(
                        ofq,
                        nfq,
                        old_type=_node_type_tokens(old_by_fq[ofq]),
                        new_type=_node_type_tokens(new_by_fq[nfq]),
                        old_param=_param_tokens(old_params, ofq),
                        new_param=_param_tokens(new_params, nfq),
                    ),
                )
            )

    rem_old = [fq for fq in old_by_fq.keys() if fq not in mapping]
    rem_new = [fq for fq in new_by_fq.keys() if fq not in matched_new]

    # Fuzzy matching uses blended similarity (types, name, parameters).
    # Topic-name endpoint similarity is intentionally excluded: topic names change
    # freely across namespace remappings and do not define node identity.
    old_type = {fq: _node_type_tokens(old_by_fq[fq]) for fq in rem_old}
    new_type = {fq: _node_type_tokens(new_by_fq[fq]) for fq in rem_new}
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
    for base, tys in a_norm.keys():
        a_by_base.setdefault(base, set()).add(tys)
    for base, tys in b_norm.keys():
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


def _filter_tool_nodes(nodes: List[Dict], *, include: bool) -> List[Dict]:
    """Remove /graph_snapshot and /launch_ros_* nodes (tool artifacts, not system nodes)."""
    if include:
        return nodes
    return [n for n in nodes if not _TOOL_NODE_RE.match(n.get("fq_name", "") or "")]


def _is_param_svc_rename(old_name: str, new_name: str) -> bool:
    """True when a service rename is purely a node-name-prefix change on a std ROS 2 param service."""
    old_base = _basename(old_name)
    new_base = _basename(new_name)
    return old_base == new_base and old_base in _PARAM_SVC_SUFFIXES


def _namespace_prefix(fq: str, depth: int = 2) -> str:
    parts = [p for p in fq.strip("/").split("/") if p]
    return "/" + "/".join(parts[:depth]) if parts else fq


def _namespace_summary(
    added: List[str],
    removed: List[str],
    changed: List[Tuple[str, str, object]],
) -> List[str]:
    stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"added": 0, "removed": 0, "changed": 0})
    for fq in added:
        stats[_namespace_prefix(fq)]["added"] += 1
    for fq in removed:
        stats[_namespace_prefix(fq)]["removed"] += 1
    for ofq, _nfq, _d in changed:
        stats[_namespace_prefix(ofq)]["changed"] += 1
    lines: List[str] = []
    for ns in sorted(stats.keys()):
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


def _classify_node_change(ofq: str, nfq: str, diffs: Dict) -> str:
    tags = []
    has_structural = any(
        diffs[k][0] or diffs[k][1]
        for k in ("publishers", "subscribers", "services", "clients")
    )
    has_remapped = (ofq != nfq) or any(
        diffs[k][3]
        for k in ("publishers", "subscribers", "services", "clients")
    )
    param_diff = diffs.get("parameters")
    value_diff = diffs.get("parameter_values")
    has_param_name = param_diff and (param_diff.get("removed") or param_diff.get("added"))
    has_param_value = value_diff and value_diff.get("changed")
    has_container = bool(diffs.get("component"))
    has_process = bool(diffs.get("process"))
    if has_structural:
        tags.append("structural")
    if has_remapped:
        tags.append("remapped")
    if has_param_name:
        tags.append("param-name")
    if has_param_value:
        tags.append("param-value")
    if has_container:
        tags.append("container")
    if has_process:
        tags.append("process")
    return "[" + ", ".join(tags) + "]" if tags else "[changed]"


def _diff_component_info(
    old_info: Optional[Dict],
    new_info: Optional[Dict],
) -> Optional[Dict]:
    """Return a diff dict when component_info changed, else None."""
    if old_info == new_info:
        return None
    old_container = (old_info or {}).get("container")
    new_container = (new_info or {}).get("container")
    old_id = (old_info or {}).get("component_id")
    new_id = (new_info or {}).get("component_id")
    return {
        "old_container": old_container,
        "new_container": new_container,
        "old_id": old_id,
        "new_id": new_id,
    }


def _build_container_map(nodes: List[Dict]) -> Dict[str, List[str]]:
    """Return {container_fq: sorted [composable_node_fq, ...]} for all composable nodes."""
    result: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        comp = n.get("component_info")
        if comp and comp.get("container"):
            result[comp["container"]].append(n.get("fq_name", ""))
    return {k: sorted(v) for k, v in result.items()}


def _build_process_groups(
    nodes: List[Dict],
) -> Tuple[Dict[int, Dict], List[str]]:
    """
    Group nodes by OS PID.

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


def _diff_process_info(
    old_proc: Optional[Dict],
    new_proc: Optional[Dict],
) -> Optional[Dict]:
    """
    Return a diff dict when process info changed in a meaningful way.
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
    ap.add_argument(
        "--include-tool-nodes",
        action="store_true",
        help="Include /graph_snapshot and /launch_ros_* nodes (default: ignored).",
    )
    ap.add_argument(
        "--include-common-topics",
        action="store_true",
        help="Show /rosout, /clock, /parameter_events in single-report group display (default: hidden).",
    )
    ap.add_argument(
        "--max-match-summary",
        type=int,
        default=100,
        help="Max matched pairs shown in diff summary (0 = no limit).",
    )
    ap.add_argument(
        "--max-changed-nodes",
        type=int,
        default=200,
        help="Max changed node entries shown in diff (0 = no limit).",
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
        nodes = _filter_tool_nodes(nodes, include=args.include_tool_nodes)

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
                    "containers": set(),
                },
            )
            g["count"] += 1
            if len(g["examples"]) < max(1, args.max_nodes_per_group):
                g["examples"].append(n.get("fq_name", ""))
            comp = n.get("component_info")
            if comp and comp.get("container"):
                g["containers"].add(comp["container"])

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
        if not args.include_tool_nodes:
            lines.append("- Filter: ignored tool nodes (/graph_snapshot, /launch_ros_*)")
        if not args.include_parameter_events:
            lines.append("- Filter: ignored topic '/parameter_events'")
        if not args.include_common_topics:
            lines.append("- Display: common topics (/rosout, /clock, /parameter_events) hidden in groups")
        has_component_data = any(n.get("component_info") is not None for n in nodes)
        lines.append(f"- Component data: {'yes' if has_component_data else 'no (snapshot taken without composition_interfaces or no composable nodes)'}")
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
            if g.get("containers"):
                containers_str = ", ".join(sorted(g["containers"]))
                lines.append(f"- container(s): {containers_str}")
            lines.append(
                f"- pubs: {len(sig.pubs)}  subs: {len(sig.subs)}  srvs: {len(sig.srvs)}  clis: {len(sig.clis)}"
            )
            if sig.pubs:
                pub_items = [
                    (t, types) for t, types in sig.pubs
                    if args.include_common_topics or t not in _COMMON_TOPICS
                ]
                if pub_items:
                    lines.append("- publish topics:")
                    for t, types in pub_items:
                        ty = ", ".join(types) if types else "<unknown>"
                        lines.append(f"  - {t} :: {ty}")
            if sig.subs:
                sub_items = [
                    (t, types) for t, types in sig.subs
                    if args.include_common_topics or t not in _COMMON_TOPICS
                ]
                if sub_items:
                    lines.append("- subscribe topics:")
                    for t, types in sub_items:
                        ty = ", ".join(types) if types else "<unknown>"
                        lines.append(f"  - {t} :: {ty}")
            lines.append("")

        container_map = _build_container_map(nodes)
        if container_map:
            standalone_count = sum(
                1 for n in nodes if not (n.get("component_info") or {}).get("container")
            )
            lines.append("## Composable Node Containers\n")
            lines.append(f"- Standalone nodes: {standalone_count}")
            lines.append(f"- Containers: {len(container_map)}")
            lines.append("")
            for cname in sorted(container_map.keys()):
                members = container_map[cname]
                lines.append(f"### {cname} ({len(members)} composable nodes)\n")
                for fq in members:
                    lines.append(f"- {fq}")
                lines.append("")

        process_groups, no_process_fqs = _build_process_groups(nodes)
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

            # Sort by (executor_type, package, pid) so related processes cluster together.
            sorted_pids = sorted(
                process_groups.keys(),
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
                    preview_cls = ", ".join(cls[: args.max_nodes_per_group])
                    if len(cls) > args.max_nodes_per_group:
                        preview_cls += f", +{len(cls) - args.max_nodes_per_group} more"
                    lines.append(f"- component classes: {preview_cls}")
                preview = ", ".join(node_list[: args.max_nodes_per_group])
                if len(node_list) > args.max_nodes_per_group:
                    preview += f", +{len(node_list) - args.max_nodes_per_group} more"
                lines.append(f"- nodes ({len(node_list)}): {preview}")
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
    old_nodes = _filter_tool_nodes(old_nodes, include=args.include_tool_nodes)
    new_nodes = _filter_tool_nodes(new_nodes, include=args.include_tool_nodes)

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

        component_diff = _diff_component_info(
            o.get("component_info"),
            n.get("component_info"),
        )

        process_diff = _diff_process_info(
            o.get("process"),
            n.get("process"),
        )

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
            or (
                param_diff
                and (
                    param_diff["removed"]
                    or param_diff["added"]
                    or param_diff["old_status"] != param_diff["new_status"]
                )
            )
            or (
                value_diff
                and (
                    value_diff["changed"]
                    or value_diff["old_status"] != value_diff["new_status"]
                )
            )
            or component_diff
            or process_diff
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
                        "component": component_diff,
                        "process": process_diff,
                    },
                )
            )

    old_edges = _remap_old_edges(_edge_set(old_nodes), mapping)
    new_edges = _edge_set(new_nodes)
    raw_removed = old_edges - new_edges
    raw_added = new_edges - old_edges

    # Detect edge renames: same (pub, sub) endpoints after mapping, only the topic name changed.
    removed_by_ep: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    added_by_ep: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for p, s, t in raw_removed:
        removed_by_ep[(p, s)].append(t)
    for p, s, t in raw_added:
        added_by_ep[(p, s)].append(t)

    renamed_edges: List[Tuple[str, str, str, str]] = []  # (pub, sub, old_topic, new_topic)
    consumed_removed: Set[Tuple[str, str, str]] = set()
    consumed_added: Set[Tuple[str, str, str]] = set()
    for (p, s), old_topics in removed_by_ep.items():
        new_topics = added_by_ep.get((p, s), [])
        if len(old_topics) == 1 and len(new_topics) == 1:
            ot, nt = old_topics[0], new_topics[0]
            if _basename(ot) == _basename(nt):
                renamed_edges.append((p, s, ot, nt))
                consumed_removed.add((p, s, ot))
                consumed_added.add((p, s, nt))

    removed_edges = sorted(raw_removed - consumed_removed)
    added_edges = sorted(raw_added - consumed_added)
    renamed_edges.sort()

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
    if not args.include_tool_nodes:
        lines.append("- Filter: ignored tool nodes (/graph_snapshot, /launch_ros_*)")
    if not args.include_parameter_events:
        lines.append("- Filter: ignored topic '/parameter_events'")
    component_enabled = any(n.get("component_info") is not None for n in old_nodes + new_nodes)
    process_enabled = any(n.get("process") is not None for n in old_nodes + new_nodes)
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
    ns_summary = _namespace_summary(added_nodes, removed_nodes, changed_nodes)
    if ns_summary:
        lines.extend(ns_summary)
    else:
        lines.append("- (no differences)")
    lines.append("")

    old_container_map = _build_container_map(old_nodes)
    new_container_map = _build_container_map(new_nodes)
    if old_container_map or new_container_map:
        old_standalone = sum(1 for n in old_nodes if not (n.get("component_info") or {}).get("container"))
        new_standalone = sum(1 for n in new_nodes if not (n.get("component_info") or {}).get("container"))
        lines.append("## Container Changes\n")
        lines.append(f"- Standalone nodes: {old_standalone} -> {new_standalone}")
        lines.append(f"- Containers: {len(old_container_map)} -> {len(new_container_map)}")
        lines.append("")

        added_containers = sorted(set(new_container_map.keys()) - set(old_container_map.keys()))
        removed_containers = sorted(set(old_container_map.keys()) - set(new_container_map.keys()))
        common_containers = sorted(set(old_container_map.keys()) & set(new_container_map.keys()))

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

        # For common containers: diff membership using node mapping.
        reverse_mapping = {v: k for k, v in mapping.items()}
        changed_container_list = []
        for c in common_containers:
            old_members = set(old_container_map[c])
            new_members = set(new_container_map[c])
            # Old members whose matched counterpart is still in this container.
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
                        if om_old_container:
                            lines.append(f"- joined: {nm}  [from {om_old_container}]")
                        else:
                            lines.append(f"- joined: {nm}  [was standalone]")
                for om in left_old:
                    nm = mapping.get(om)
                    if nm is None:
                        lines.append(f"- left:   {om}  [node removed]")
                    else:
                        nm_new_container = (new_by_fq.get(nm, {}).get("component_info") or {}).get("container")
                        if nm_new_container:
                            lines.append(f"- left:   {om}  [now in {nm_new_container}]")
                        else:
                            lines.append(f"- left:   {om}  [now standalone]")
                lines.append("")

    # Collect process-level changes across all matched nodes for a dedicated summary.
    proc_exec_changes: List[Tuple[str, str, str, str]] = []  # (ofq, nfq, old_et, new_et)
    proc_pkg_changes: List[Tuple[str, str, str, str]] = []   # (ofq, nfq, old_pkg, new_pkg)
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
    max_match = args.max_match_summary if args.max_match_summary > 0 else len(evidence_sorted)
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
        max_changed = args.max_changed_nodes if args.max_changed_nodes > 0 else len(changed_nodes)
        for ofq, nfq, diffs in sorted(changed_nodes, key=lambda x: x[0])[:max_changed]:
            change_tag = _classify_node_change(ofq, nfq, diffs)
            lines.append(f"### {ofq} -> {nfq} {change_tag}")
            node_was_renamed = ofq != nfq
            for kind in ("publishers", "subscribers", "services", "clients"):
                removed, added, changed, renamed = diffs[kind]
                # Suppress standard ROS 2 param service renames that are purely derived
                # from the node itself being renamed — they add no information.
                if node_was_renamed and kind == "services":
                    renamed = [(o, n) for o, n in renamed if not _is_param_svc_rename(o, n)]
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
            comp_diff = diffs.get("component") if isinstance(diffs, dict) else None
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
            proc_diff = diffs.get("process") if isinstance(diffs, dict) else None
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

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
