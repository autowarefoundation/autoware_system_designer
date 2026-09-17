# Copyright 2026 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Message-flow tracing from detected timers to terminal publishes.

A chain instance starts at a timer fire, follows the publishes that fire
triggered on the same thread, matches each publish to the takes of its message
(same topic, source timestamp inside the publish call), and continues through
the publishes those takes trigger. Where a node samples an input, the
chain continues at the node's first later publish for which the matched take is
still the latest arrival of that topic.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Optional

from .node_stats import NS_PER_MS, Analysis, NodeObs, PubEvent, Summary, TakeEvent, TimerEvent, summarize

MAX_DEPTH = 24
MAX_STARTS_PER_TIMER = 600
MAX_BRANCH = 8
# A sampled continuation later than this is a different chain.
SAMPLE_HORIZON_NS = 2_000_000_000


@dataclass
class ChainSummary:
    source: str  # <node_path>:timer:<period_ms>
    target: str  # <node_path>:<topic>
    source_node: str
    target_node: str
    target_topic: str
    hops: int
    terminal: bool
    summary: Summary
    path: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {"from": self.source, "to": self.target, "hops": self.hops, "terminal": self.terminal}
        out.update(self.summary.as_dict())
        out["path"] = self.path
        return out


def _node_key(node: NodeObs) -> str:
    return node.path or node.key


def _matched_takes(analysis: Analysis, pub: PubEvent) -> list[TakeEvent]:
    return analysis.takes_by_pub.get(id(pub), [])


def _continuations(analysis: Analysis, take: TakeEvent) -> list[PubEvent]:
    """Publishes the take fired, else the node's first later sampled publish per output."""
    direct = analysis.pubs_by_trigger.get(id(take))
    if direct:
        return direct
    node = take.node
    out: list[PubEvent] = []
    takes = node.takes_by_topic.get(take.topic, [])
    times = [t.t for t in takes]
    index = bisect_right(times, take.t)
    next_take_t = takes[index].t if index < len(takes) else take.t + SAMPLE_HORIZON_NS
    for topic, pubs in node.pubs_by_topic.items():
        pub_times = [p.t_in for p in pubs]
        start = bisect_right(pub_times, take.t)
        for pub in pubs[start:]:
            if pub.t_in > next_take_t or pub.t_in - take.t > SAMPLE_HORIZON_NS:
                break
            trigger = pub.trigger
            if trigger is not None and trigger.kind == "input" and trigger.topic == take.topic:
                # Fired by a different take of the same topic; not this message.
                continue
            out.append(pub)
            break
    return out


def _intra_continuations(analysis: Analysis, pub: PubEvent) -> list[PubEvent]:
    return analysis.pubs_by_trigger.get(id(pub), [])


def trace_chains(analysis: Analysis) -> list[ChainSummary]:
    reached: dict[tuple[str, str], list[int]] = {}
    hops: dict[tuple[str, str], int] = {}
    terminal: dict[tuple[str, str], bool] = {}
    paths: dict[tuple[str, str], list[str]] = {}

    for node in analysis.matched_nodes():
        for (pid, handle), period_ns in node.timer_handles.items():
            if analysis.timer_owner.get((pid, handle)) != node.key:
                continue
            period_ms = period_ns / NS_PER_MS if period_ns else 0
            source = f"{_node_key(node)}:timer:{period_ms:g}"
            fires = _timer_fires_of(analysis, node, pid, handle)
            step = max(1, len(fires) // MAX_STARTS_PER_TIMER)
            for fire in fires[::step]:
                _walk_from_timer(analysis, node, fire, source, reached, hops, terminal, paths)

    chains: list[ChainSummary] = []
    for (source, target), values in reached.items():
        summary = summarize(values)
        if summary is None:
            continue
        target_node, _, topic = target.partition(":")
        chains.append(
            ChainSummary(
                source=source,
                target=target,
                source_node=source.split(":timer:")[0],
                target_node=target_node,
                target_topic=topic,
                hops=hops[(source, target)],
                terminal=terminal[(source, target)],
                summary=summary,
                path=paths[(source, target)],
            )
        )
    chains.sort(key=lambda c: (c.source, -c.hops, c.target))
    return chains


def _timer_fires_of(analysis: Analysis, node: NodeObs, pid: int, handle: int) -> list[TimerEvent]:
    fires: dict[int, TimerEvent] = {}
    for pubs in node.pubs_by_topic.values():
        for pub in pubs:
            ref = pub.trigger.ref if pub.trigger else None
            if isinstance(ref, TimerEvent) and ref.pid == pid and ref.handle == handle:
                fires[id(ref)] = ref
    return sorted(fires.values(), key=lambda f: f.t)


def _walk_from_timer(analysis, node, fire, source, reached, hops, terminal, paths) -> None:
    first_pubs = [p for p in analysis.pubs_by_trigger.get(id(fire), []) if p.node is node]
    stack: list[tuple[PubEvent, int, tuple[str, ...]]] = [(p, 1, (_node_key(node),)) for p in first_pubs]
    # target → (latency, depth, path) of the earliest arrival from this fire
    arrivals: dict[str, tuple[int, int, tuple[str, ...]]] = {}
    while stack:
        pub, depth, path = stack.pop()
        target = f"{_node_key(pub.node)}:{pub.topic}"
        key = (source, target)
        latency = pub.t_in - fire.t
        best = arrivals.get(target)
        if best is None or latency < best[0]:
            arrivals[target] = (latency, depth, path)
        if depth >= MAX_DEPTH:
            terminal.setdefault(key, False)
            continue
        next_pubs: list[tuple[PubEvent, tuple[str, ...]]] = []
        for take in _matched_takes(analysis, pub):
            downstream = _node_key(take.node)
            if downstream in path:
                continue
            for cont in _continuations(analysis, take)[:MAX_BRANCH]:
                next_pubs.append((cont, path + (downstream,)))
        for cont in _intra_continuations(analysis, pub)[:MAX_BRANCH]:
            downstream = _node_key(cont.node)
            if downstream in path:
                continue
            next_pubs.append((cont, path + (downstream,)))
        is_terminal = not next_pubs
        terminal[key] = terminal.get(key, True) and is_terminal
        for cont, cont_path in next_pubs:
            stack.append((cont, depth + 1, cont_path))
    for target, (latency, depth, path) in arrivals.items():
        key = (source, target)
        reached.setdefault(key, []).append(latency)
        hops.setdefault(key, depth)
        paths.setdefault(key, list(path))
