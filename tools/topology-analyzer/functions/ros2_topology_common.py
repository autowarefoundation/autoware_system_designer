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


def iter_type_items(sig: Signature) -> Iterable[str]:
    """Yield direction-qualified type tokens without topic names.

    Used for type-composition-first matching: two nodes are considered similar when
    they publish/subscribe/serve the same message types, regardless of what the
    topics are named.  Robust across namespace changes and topic remappings.
    """
    for _, types in sig.pubs:
        for t in types:
            yield f"PT|{t}"
    for _, types in sig.subs:
        for t in types:
            yield f"ST|{t}"
    for _, types in sig.srvs:
        for t in types:
            yield f"SVT|{t}"
    for _, types in sig.clis:
        for t in types:
            yield f"CLT|{t}"


def iter_signature_items(
    sig: Signature,
    include_types: bool = False,
    type_only: bool = False,
) -> Iterable[str]:
    """Yield comparison tokens for a node signature.

    Args:
        type_only: if True, yield direction-qualified type tokens only (no topic
                   names).  Takes priority over *include_types*.  This is the
                   recommended mode for matching nodes across systems where topic
                   names may differ (namespace moves, topic remapping, etc.).
        include_types: if True (and *type_only* is False), append message types
                       to topic names.
    """
    if type_only:
        yield from iter_type_items(sig)
        return

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
