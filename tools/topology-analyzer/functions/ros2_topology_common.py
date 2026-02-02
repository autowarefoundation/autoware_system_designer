#!/usr/bin/env python3

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set, Tuple


@dataclass(frozen=True)
class Signature:
    pubs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    subs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    srvs: Tuple[Tuple[str, Tuple[str, ...]], ...]
    clis: Tuple[Tuple[str, Tuple[str, ...]], ...]


def freeze_map(m: Dict[str, List[str]]) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    items: List[Tuple[str, Tuple[str, ...]]] = []
    for k, v in (m or {}).items():
        items.append((k, tuple(sorted(v or []))))
    items.sort(key=lambda x: x[0])
    return tuple(items)


def signature_from_node(node: Dict) -> Signature:
    return Signature(
        pubs=freeze_map(node.get("publishers", {})),
        subs=freeze_map(node.get("subscribers", {})),
        srvs=freeze_map(node.get("services", {})),
        clis=freeze_map(node.get("clients", {})),
    )


def signature_id(sig: Signature) -> str:
    payload = {"pubs": sig.pubs, "subs": sig.subs, "srvs": sig.srvs, "clis": sig.clis}
    b = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(b).hexdigest()[:12]


def iter_signature_items(sig: Signature, include_types: bool) -> Iterable[str]:
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


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0
