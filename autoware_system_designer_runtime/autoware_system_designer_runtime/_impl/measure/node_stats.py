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

"""Per-node statistics from trace records: rates, process time, response, link communication.

The unit of analysis is the node. Each publish is attributed to the trigger that
immediately precedes it on its thread (a take, a timer fire, or, when the input
arrives without a DDS hop, the upstream publish), and the time between them is
the node's process time. Intra-process hops are folded into the downstream
node's process time and carry no transport of their own.

A take is matched to the publish of its message by topic and source timestamp:
the DDS source timestamp lies inside the publish call. Publisher gids are not
comparable across processes under every RMW, so they only break ties.
"""

from __future__ import annotations

import logging
import math
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional, Union

from .node_graph import INFRA_TOPICS, NodeGraph, NodeInfo
from .trace_reader import Endpoint, TraceSet

logger = logging.getLogger(__name__)

NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000

# An upstream intra-process publish older than this cannot be the trigger.
INTRA_LOOKBACK_NS = 1 * NS_PER_S
# An input older than this is not part of the response to an output.
RESPONSE_HORIZON_NS = 5 * NS_PER_S
# Communication values outside this range come from unrelated clocks and are dropped.
COMM_LIMIT_NS = 10 * NS_PER_S
# Records this far before the window feed thread state without being counted.
PREROLL_NS = 1 * NS_PER_S
# Tolerance around a publish call when placing a source timestamp inside it.
MATCH_SLACK_NS = 200_000


# ---- summaries ---------------------------------------------------------------


@dataclass(slots=True)
class Summary:
    count: int
    min_ms: float
    mean_ms: float
    max_ms: float
    sd_ms: float

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "min_ms": round(self.min_ms, 4),
            "mean_ms": round(self.mean_ms, 4),
            "max_ms": round(self.max_ms, 4),
            "sd_ms": round(self.sd_ms, 4),
        }


def summarize(values_ns: list[int]) -> Optional[Summary]:
    if not values_ns:
        return None
    n = len(values_ns)
    mean = sum(values_ns) / n
    var = sum((v - mean) ** 2 for v in values_ns) / n
    return Summary(
        count=n,
        min_ms=min(values_ns) / NS_PER_MS,
        mean_ms=mean / NS_PER_MS,
        max_ms=max(values_ns) / NS_PER_MS,
        sd_ms=math.sqrt(var) / NS_PER_MS,
    )


# ---- events --------------------------------------------------------------------


@dataclass(slots=True)
class TakeEvent:
    t: int
    tid: int
    pid: int
    node: "NodeObs"
    topic: str
    gid: str
    source_ts: int
    dup: bool
    publisher: Optional["NodeObs"]
    in_window: bool
    # The publish call this message came out of, when one was traced.
    source_pub: Optional["PubEvent"] = None


@dataclass(slots=True)
class TimerEvent:
    t: int
    tid: int
    pid: int
    handle: int
    period_ns: Optional[int]
    in_window: bool


@dataclass(slots=True)
class Trigger:
    kind: str  # timer | input | unknown
    t: int
    topic: Optional[str] = None
    period_ns: Optional[int] = None
    intra: bool = False
    ref: Optional[object] = None

    def label(self) -> str:
        if self.kind == "timer":
            period = f"{self.period_ns / NS_PER_MS:g} ms" if self.period_ns else "?"
            return f"timer({period})"
        if self.kind == "input":
            return f"input({self.topic}, intra_process)" if self.intra else f"input({self.topic})"
        return "unknown"

    def key(self) -> tuple:
        return (self.kind, self.topic, self.period_ns, self.intra)


@dataclass(slots=True)
class PubEvent:
    t_in: int
    t_out: int
    tid: int
    pid: int
    node: "NodeObs"
    topic: str
    gid: str
    in_window: bool
    trigger: Optional[Trigger] = None
    exec_ns: Optional[int] = None


Event = Union[TakeEvent, TimerEvent, PubEvent]


# ---- observed node ---------------------------------------------------------------


@dataclass
class NodeObs:
    key: str
    fqn: str
    info: Optional[NodeInfo]
    pids: set[int] = field(default_factory=set)
    takes_by_topic: dict[str, list[TakeEvent]] = field(default_factory=dict)
    dup_takes: dict[str, int] = field(default_factory=dict)
    pubs_by_topic: dict[str, list[PubEvent]] = field(default_factory=dict)
    # Timer handles that triggered this node's outputs: (pid, handle) → period.
    timer_handles: dict[tuple[int, int], Optional[int]] = field(default_factory=dict)
    intra_inputs: set[str] = field(default_factory=set)
    last_arrival: dict[str, int] = field(default_factory=dict)
    # (output topic, input topic) → response values
    response: dict[tuple[str, str], list[int]] = field(default_factory=dict)

    @property
    def path(self) -> Optional[str]:
        return self.info.path if self.info else None

    @property
    def matched(self) -> bool:
        return self.info is not None


# ---- analysis -----------------------------------------------------------------------


@dataclass
class Analysis:
    window_start_ns: int
    window_end_ns: int
    nodes: dict[str, NodeObs] = field(default_factory=dict)
    # (topic, publisher key or None, subscriber key) → communication values
    links: dict[tuple[str, Optional[str], str], list[int]] = field(default_factory=dict)
    comm_invalid: int = 0
    timer_fires: dict[tuple[int, int], int] = field(default_factory=dict)
    # (pid, handle) → key of the node first seen publishing after the timer fired
    timer_owner: dict[tuple[int, int], str] = field(default_factory=dict)
    # trigger event id → publishes it triggered (for chain tracing)
    pubs_by_trigger: dict[int, list[PubEvent]] = field(default_factory=dict)
    # publish event id → takes of that message
    takes_by_pub: dict[int, list[TakeEvent]] = field(default_factory=dict)
    dropped_records: int = 0
    process_count: int = 0

    @property
    def window_s(self) -> float:
        return max(self.window_end_ns - self.window_start_ns, 0) / NS_PER_S

    def rate(self, count: int) -> Optional[float]:
        return count / self.window_s if self.window_s > 0 else None

    def unmatched_nodes(self) -> list[NodeObs]:
        return [n for n in self.nodes.values() if not n.matched]

    def matched_nodes(self) -> list[NodeObs]:
        return [n for n in self.nodes.values() if n.matched]


def analyze(trace_set: TraceSet, graph: NodeGraph, window_start_ns: int, window_end_ns: int) -> Analysis:
    analysis = Analysis(window_start_ns=window_start_ns, window_end_ns=window_end_ns)
    analysis.dropped_records = trace_set.total_dropped()
    analysis.process_count = len(trace_set.processes)
    nodes: dict[str, NodeObs] = analysis.nodes

    def obs_for(endpoint: Endpoint) -> NodeObs:
        info = graph.node_for_fqn(endpoint.fqn)
        key = info.path if info else endpoint.fqn
        node = nodes.get(key)
        if node is None:
            node = NodeObs(key=key, fqn=endpoint.fqn, info=info)
            if info is not None:
                node.intra_inputs = graph.intra_inputs(info)
            nodes[key] = node
        node.pids.add(endpoint.pid)
        return node

    gid_owner: dict[str, NodeObs] = {}
    for gid, endpoint in trace_set.publishers_by_gid.items():
        gid_owner[gid] = obs_for(endpoint)

    per_process = [_process_events(proc, analysis, obs_for) for proc in trace_set.processes.values()]
    index = _PublishIndex(event for events in per_process for event in events if isinstance(event, PubEvent))
    for events in per_process:
        for event in events:
            if isinstance(event, TakeEvent):
                _match_take(event, index, gid_owner, graph)
    for events in per_process:
        _scan_process(events, analysis)

    for node in nodes.values():
        for takes in node.takes_by_topic.values():
            takes.sort(key=lambda e: e.t)
        for pubs in node.pubs_by_topic.values():
            pubs.sort(key=lambda e: e.t_in)
    for takes in analysis.takes_by_pub.values():
        takes.sort(key=lambda e: e.t)
    return analysis


class _PublishIndex:
    """Publishes of every topic ordered by entry time, for placing source timestamps."""

    def __init__(self, pubs) -> None:
        self._by_topic: dict[str, tuple[list[int], list[PubEvent]]] = {}
        grouped: dict[str, list[PubEvent]] = {}
        for pub in pubs:
            grouped.setdefault(pub.topic, []).append(pub)
        for topic, events in grouped.items():
            events.sort(key=lambda e: e.t_in)
            self._by_topic[topic] = ([e.t_in for e in events], events)

    def match(self, topic: str, source_ts: int) -> Optional[PubEvent]:
        entry = self._by_topic.get(topic)
        if entry is None or source_ts <= 0:
            return None
        t_ins, events = entry
        index = bisect_right(t_ins, source_ts + MATCH_SLACK_NS) - 1
        best: Optional[PubEvent] = None
        best_distance = None
        for candidate in range(index, max(index - 4, -1), -1):
            pub = events[candidate]
            if pub.t_in - MATCH_SLACK_NS <= source_ts <= pub.t_out + MATCH_SLACK_NS:
                distance = abs(source_ts - (pub.t_in + pub.t_out) // 2)
                if best is None or distance < best_distance:
                    best, best_distance = pub, distance
        return best


def _match_take(take: TakeEvent, index: _PublishIndex, gid_owner: dict[str, NodeObs], graph: NodeGraph) -> None:
    pub = index.match(take.topic, take.source_ts)
    if pub is not None:
        take.source_pub = pub
        take.publisher = pub.node
        same_pid = pub.pid == take.pid
    else:
        take.publisher = gid_owner.get(take.gid)
        same_pid = take.publisher is not None and take.pid in take.publisher.pids and len(take.publisher.pids) == 1
    take.dup = _is_duplicate(take.node, take.publisher, same_pid, graph, take.topic)


def _is_duplicate(
    take_node: NodeObs, publisher: Optional[NodeObs], same_pid: bool, graph: NodeGraph, topic: str
) -> bool:
    """An inter-process copy of a message the node already received intra-process."""
    if publisher is None or not same_pid:
        return False
    if take_node.info is None or publisher.info is None:
        return False
    if graph.link(topic, publisher.info.path, take_node.info.path) is not None:
        return graph.is_intra_link(topic, publisher.info.path, take_node.info.path)
    return take_node.info.intra_process and publisher.info.intra_process


def _process_events(proc, analysis: Analysis, obs_for) -> list[Event]:
    t0 = analysis.window_start_ns - PREROLL_NS
    t1 = analysis.window_end_ns
    events: list[Event] = []

    for take in proc.takes:
        if take.t < t0 or take.t > t1:
            continue
        endpoint = proc.endpoint(take.handle, take.t)
        if endpoint is None or endpoint.topic in INFRA_TOPICS:
            continue
        events.append(
            TakeEvent(
                t=take.t,
                tid=take.tid,
                pid=take.pid,
                node=obs_for(endpoint),
                topic=endpoint.topic,
                gid=take.gid,
                source_ts=take.source_ts,
                dup=False,
                publisher=None,
                in_window=take.t >= analysis.window_start_ns,
            )
        )

    for fire in proc.timers:
        if fire.t < t0 or fire.t > t1:
            continue
        info = proc.timer(fire.handle, fire.t)
        events.append(
            TimerEvent(
                t=fire.t,
                tid=fire.tid,
                pid=fire.pid,
                handle=fire.handle,
                period_ns=info.period_ns if info else None,
                in_window=fire.t >= analysis.window_start_ns,
            )
        )
        if fire.t >= analysis.window_start_ns:
            key = (fire.pid, fire.handle)
            analysis.timer_fires[key] = analysis.timer_fires.get(key, 0) + 1

    for pub in proc.publishes:
        if pub.t_in < t0 or pub.t_in > t1:
            continue
        endpoint = proc.endpoint(pub.handle, pub.t_in)
        if endpoint is None or endpoint.topic in INFRA_TOPICS:
            continue
        node = obs_for(endpoint)
        events.append(
            PubEvent(
                t_in=pub.t_in,
                t_out=pub.t_out,
                tid=pub.tid,
                pid=pub.pid,
                node=node,
                topic=endpoint.topic,
                gid=endpoint.gid,
                in_window=pub.t_in >= analysis.window_start_ns,
            )
        )

    events.sort(key=_event_time)
    return events


def _event_time(event: Event) -> int:
    return event.t_in if isinstance(event, PubEvent) else event.t


def _scan_process(events: list[Event], analysis: Analysis) -> None:
    last_marker: dict[int, Union[TakeEvent, TimerEvent]] = {}
    recent_pubs: dict[str, Deque[PubEvent]] = {}
    # Intra-process readers of a topic within this process.
    intra_readers: dict[str, list[NodeObs]] = {}
    seen_nodes: set[str] = set()

    def note_node(node: NodeObs) -> None:
        if node.key in seen_nodes:
            return
        seen_nodes.add(node.key)
        for topic in node.intra_inputs:
            intra_readers.setdefault(topic, []).append(node)

    for event in events:
        if isinstance(event, TakeEvent):
            note_node(event.node)
            if event.dup:
                if event.in_window:
                    event.node.dup_takes[event.topic] = event.node.dup_takes.get(event.topic, 0) + 1
                continue
            last_marker[event.tid] = event
            event.node.last_arrival[event.topic] = event.t
            if event.in_window:
                event.node.takes_by_topic.setdefault(event.topic, []).append(event)
                if event.source_pub is not None:
                    analysis.takes_by_pub.setdefault(id(event.source_pub), []).append(event)
                _note_link(analysis, event)

        elif isinstance(event, TimerEvent):
            last_marker[event.tid] = event

        else:
            node = event.node
            note_node(node)
            marker = last_marker.get(event.tid)
            own = _own_marker(marker, node, analysis)
            upstream = _latest_intra_upstream(node, recent_pubs, event.t_in)
            trigger = _choose_trigger(own, upstream, node, analysis)
            event.trigger = trigger
            if trigger.kind != "unknown":
                event.exec_ns = event.t_in - trigger.t
                if trigger.ref is not None:
                    analysis.pubs_by_trigger.setdefault(id(trigger.ref), []).append(event)
            if event.in_window:
                node.pubs_by_topic.setdefault(event.topic, []).append(event)
                for topic, t_arrival in node.last_arrival.items():
                    delta = event.t_in - t_arrival
                    if 0 <= delta <= RESPONSE_HORIZON_NS:
                        node.response.setdefault((event.topic, topic), []).append(delta)
            recent = recent_pubs.setdefault(event.topic, deque(maxlen=256))
            recent.append(event)
            for reader in intra_readers.get(event.topic, []):
                if reader is not node:
                    reader.last_arrival[event.topic] = event.t_in


def _latest_intra_upstream(node: NodeObs, recent_pubs: dict[str, Deque[PubEvent]], t_in: int) -> Optional[PubEvent]:
    best: Optional[PubEvent] = None
    for topic in node.intra_inputs:
        recent = recent_pubs.get(topic)
        if not recent:
            continue
        for candidate in reversed(recent):
            if candidate.node is node:
                continue
            if candidate.t_in >= t_in:
                continue
            if t_in - candidate.t_in > INTRA_LOOKBACK_NS:
                break
            if best is None or candidate.t_in > best.t_in:
                best = candidate
            break
    return best


def _choose_trigger(
    own: Optional[Union[TakeEvent, TimerEvent]],
    upstream: Optional[PubEvent],
    node: NodeObs,
    analysis: Analysis,
) -> Trigger:
    """The later of the node's own thread marker and an intra-process upstream publish.

    A timer marker belongs to no node in rcl; it is attributed through the publish
    that follows it on the same thread, so a timer marker counts as the node's own.
    """
    own_t = own.t if own is not None else None
    up_t = upstream.t_in if upstream is not None else None
    if own is not None and (up_t is None or own_t >= up_t):
        if isinstance(own, TimerEvent):
            node.timer_handles[(own.pid, own.handle)] = own.period_ns
            analysis.timer_owner.setdefault((own.pid, own.handle), node.key)
            return Trigger(kind="timer", t=own.t, period_ns=own.period_ns, ref=own)
        return Trigger(kind="input", t=own.t, topic=own.topic, ref=own)
    if upstream is not None:
        return Trigger(kind="input", t=upstream.t_in, topic=upstream.topic, intra=True, ref=upstream)
    return Trigger(kind="unknown", t=0)


def _note_link(analysis: Analysis, take: TakeEvent) -> None:
    if take.source_ts <= 0:
        analysis.comm_invalid += 1
        return
    comm = take.t - take.source_ts
    if comm < -COMM_LIMIT_NS or comm > COMM_LIMIT_NS:
        analysis.comm_invalid += 1
        return
    publisher_key = take.publisher.key if take.publisher is not None else None
    analysis.links.setdefault((take.topic, publisher_key, take.node.key), []).append(comm)


# A take marker is the node's own when the same node took it. A timer belongs to
# no node in rcl, so a timer marker is the node's own unless an earlier publish
# already attributed that timer to another node.
def _own_marker(marker: Optional[Union[TakeEvent, TimerEvent]], node: NodeObs, analysis: Analysis):
    if marker is None:
        return None
    if isinstance(marker, TimerEvent):
        owner = analysis.timer_owner.get((marker.pid, marker.handle))
        return marker if owner is None or owner == node.key else None
    return marker if marker.node is node else None
