#!/usr/bin/env python3
# Node matching algorithm for topology-aware diffing across two graph snapshots.

from typing import Dict, List, Optional, Set, Tuple

from .common import (
    Signature,
    basename,
    freeze_map,
    jaccard,
    name_similarity,
    signature_from_node,
    signature_id,
)

_RENAME_SIM_THRESHOLD = 0.70
_RENAME_SIM_MARGIN = 0.12


def normalized_signature(node: Dict, *, ignored_topics: Set[str] = frozenset()) -> Signature:
    """Name-insensitive signature: compare by endpoint basename + type list."""

    def norm_map(m: Dict[str, List[str]]) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for k, v in (m or {}).items():
            if k in ignored_topics:
                continue
            out[basename(k)] = v or []
        return out

    return Signature(
        pubs=freeze_map(norm_map(node.get("publishers", {}))),
        subs=freeze_map(norm_map(node.get("subscribers", {}))),
        srvs=freeze_map(norm_map(node.get("services", {}))),
        clis=freeze_map(norm_map(node.get("clients", {}))),
    )


def node_type_tokens(node: Dict) -> Set[str]:
    """Direction-aware message-type tokens (topic-name agnostic).

    Used to pair nodes whose topics were renamed (e.g., namespace move) but which
    still publish/subscribe the same types.
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


def param_info(param_map: Dict[str, List[str]], fq: str) -> Tuple[Set[str], Optional[str]]:
    if not param_map:
        return set(), None
    vals = param_map.get(fq)
    if vals is None:
        return set(), "no param entry"
    if vals and vals[0].startswith("<"):
        return set(), vals[0]
    return set(vals), None


def param_tokens(param_map: Dict[str, List[str]], fq: str) -> Set[str]:
    names, status = param_info(param_map, fq)
    if status:
        return set()
    return {f"PRM:{n}" for n in names}


def param_value_info(param_values: Dict[str, Dict[str, str]], fq: str) -> Tuple[Dict[str, str], Optional[str]]:
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


def match_score(
    old_fq: str,
    new_fq: str,
    *,
    old_type: Set[str],
    new_type: Set[str],
    old_param: Set[str],
    new_param: Set[str],
) -> float:
    type_sim = jaccard(old_type, new_type)
    name_sim = name_similarity(old_fq, new_fq)
    param_sim = jaccard(old_param, new_param) if (old_param or new_param) else 0.0

    # Interface type composition is the primary identity signal: a node's role is
    # defined by the message types it publishes/subscribes/serves, not by topic names.
    w_type = 0.75
    w_name = 0.20
    w_param = 0.05 if (old_param or new_param) else 0.0
    w_sum = w_type + w_name + w_param
    if w_sum == 0:
        return 0.0
    return (w_type * type_sim + w_name * name_sim + w_param * param_sim) / w_sum


def match_nodes(
    old_nodes: List[Dict],
    new_nodes: List[Dict],
    *,
    min_similarity: float,
    min_margin: float,
    old_params: Dict[str, List[str]],
    new_params: Dict[str, List[str]],
) -> Tuple[Dict[str, str], List[Tuple[str, str, float]]]:
    """Return (old_fq -> new_fq mapping, evidence list).

    Matching passes (in priority order):
      0. Same fully-qualified name — fast path.
      1. Exact signature — same pub/sub/srv/cli topic set and types.
      1.5. Normalized signature — same basename+type set (catches namespace moves).
      1.7. Type-composition — identical direction+type frozenset, any topic names.
      1.8. Same-type groups, M:N disambiguation by name similarity.
      2. Fuzzy mutual-best: weighted blend of type Jaccard, name, param similarity.
    """
    old_by_fq = {n.get("fq_name", ""): n for n in old_nodes}
    new_by_fq = {n.get("fq_name", ""): n for n in new_nodes}

    sid_to_old: Dict[str, List[str]] = {}
    sid_to_new: Dict[str, List[str]] = {}
    nsid_to_old: Dict[str, List[str]] = {}
    nsid_to_new: Dict[str, List[str]] = {}
    for fq, n in old_by_fq.items():
        sid_to_old.setdefault(signature_id(signature_from_node(n)), []).append(fq)
        nsid_to_old.setdefault(signature_id(normalized_signature(n)), []).append(fq)
    for fq, n in new_by_fq.items():
        sid_to_new.setdefault(signature_id(signature_from_node(n)), []).append(fq)
        nsid_to_new.setdefault(signature_id(normalized_signature(n)), []).append(fq)

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
                match_score(
                    fq,
                    fq,
                    old_type=node_type_tokens(old_by_fq[fq]),
                    new_type=node_type_tokens(new_by_fq[fq]),
                    old_param=param_tokens(old_params, fq),
                    new_param=param_tokens(new_params, fq),
                ),
            )
        )

    # 1) Exact signature pairing where unambiguous.
    for sid, olds in sid_to_old.items():
        news = sid_to_new.get(sid, [])
        if len(olds) == 1 and len(news) == 1:
            ofq, nfq = olds[0], news[0]
            if ofq in mapping or nfq in matched_new:
                continue
            mapping[ofq] = nfq
            matched_new.add(nfq)
            evidence.append((ofq, nfq, 1.0))

    # 1.5) Normalized signature pairing (basename+types) where unambiguous.
    for nsid, olds in nsid_to_old.items():
        news = nsid_to_new.get(nsid, [])
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
                    jaccard(node_type_tokens(old_by_fq[ofq]), node_type_tokens(new_by_fq[nfq])),
                )
            )

    # 1.7) Type-composition matching: same direction+type set, any topic names.
    # Catches nodes whose topics were renamed (namespace move, topic remapping) but
    # whose functional interface (message type composition) is identical.
    type_to_old: Dict[frozenset, List[str]] = {}
    type_to_new: Dict[frozenset, List[str]] = {}
    for fq, n in old_by_fq.items():
        if fq in mapping:
            continue
        key = frozenset(node_type_tokens(n))
        if key:  # skip nodes with no type info (e.g. bare parameter servers)
            type_to_old.setdefault(key, []).append(fq)
    for fq, n in new_by_fq.items():
        if fq in matched_new:
            continue
        key = frozenset(node_type_tokens(n))
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
                    match_score(
                        ofq,
                        nfq,
                        old_type=node_type_tokens(old_by_fq[ofq]),
                        new_type=node_type_tokens(new_by_fq[nfq]),
                        old_param=param_tokens(old_params, ofq),
                        new_param=param_tokens(new_params, nfq),
                    ),
                )
            )

    # 1.8) Same-type-composition groups, M:N disambiguation by name.
    # Step 1.7 resolved 1:1 groups. Here we handle groups where M old and N new
    # nodes share the same type token frozenset (e.g. multiple identical-driver
    # LiDAR nodes renamed across snapshots). Within such a group type_sim=1.0
    # for every pair, so name similarity is the only discriminator.
    _GROUP_NAME_MARGIN = 0.01
    for key, type_olds in type_to_old.items():
        type_news = type_to_new.get(key, [])
        if len(type_olds) == 1 and len(type_news) == 1:
            continue  # already handled by Step 1.7
        olds_rem = [fq for fq in type_olds if fq not in mapping]
        news_rem = [fq for fq in type_news if fq not in matched_new]
        if not olds_rem or not news_rem:
            continue
        old_best_g: Dict[str, Tuple[Optional[str], float, float]] = {}
        for ofq in olds_rem:
            g_best_n: Optional[str] = None
            g_best_s = -1.0
            g_second_s = -1.0
            for nfq in news_rem:
                s = name_similarity(ofq, nfq)
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
                s = name_similarity(ofq, nfq)
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
                    match_score(
                        ofq,
                        nfq,
                        old_type=node_type_tokens(old_by_fq[ofq]),
                        new_type=node_type_tokens(new_by_fq[nfq]),
                        old_param=param_tokens(old_params, ofq),
                        new_param=param_tokens(new_params, nfq),
                    ),
                )
            )

    rem_old = [fq for fq in old_by_fq.keys() if fq not in mapping]
    rem_new = [fq for fq in new_by_fq.keys() if fq not in matched_new]

    # Fuzzy matching uses blended similarity (types, name, parameters).
    # Topic-name endpoint similarity is intentionally excluded: topic names change
    # freely across namespace remappings and do not define node identity.
    old_type_cache = {fq: node_type_tokens(old_by_fq[fq]) for fq in rem_old}
    new_type_cache = {fq: node_type_tokens(new_by_fq[fq]) for fq in rem_new}
    old_param_cache = {fq: param_tokens(old_params, fq) for fq in rem_old}
    new_param_cache = {fq: param_tokens(new_params, fq) for fq in rem_new}

    # 2) Similarity-based mutual best matching.
    old_best: Dict[str, Tuple[Optional[str], float, float]] = {}
    for ofq in rem_old:
        best_n: Optional[str] = None
        best_s = -1.0
        second_s = -1.0
        ot = old_type_cache[ofq]
        for nfq in rem_new:
            s = match_score(
                ofq,
                nfq,
                old_type=ot,
                new_type=new_type_cache[nfq],
                old_param=old_param_cache[ofq],
                new_param=new_param_cache[nfq],
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
        nt = new_type_cache[nfq]
        for ofq in rem_old:
            s = match_score(
                ofq,
                nfq,
                old_type=old_type_cache[ofq],
                new_type=nt,
                old_param=old_param_cache[ofq],
                new_param=new_param_cache[nfq],
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
