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

"""Checks made before a replay steps: the bag against the boundary, then the real endpoints
against the design.

A problem is an error, which stops the run, or a warning. Plain Python with no ROS import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from ..measure.node_graph import NodeGraph
from .bag import BagInfo
from .scope import INFRA_TOPICS, Boundary

ERROR = "error"
WARNING = "warning"

# Relative gap between a declared rate and a timer's rate that still counts as the same.
RATE_TOLERANCE = 0.02


@dataclass(frozen=True)
class Problem:
    level: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}


def has_errors(problems: Iterable[Problem]) -> bool:
    return any(p.level == ERROR for p in problems)


def fed_topics(boundary: Boundary, bag: BagInfo, extra_inputs: Sequence[str]) -> dict[str, str]:
    """Bag topics the driver delivers, with their recorded types: boundary inputs and declared extras."""
    wanted = list(boundary.input_topics()) + [t for t in extra_inputs if t not in boundary.input_topics()]
    return {topic: bag.topics[topic].msg_type for topic in wanted if topic in bag.topics}


def remap_candidates(bag: BagInfo, msg_type: str, taken: Iterable[str] = ()) -> list[str]:
    """Bag topics of *msg_type* that no input in *taken* is fed from."""
    taken = set(taken)
    if not msg_type:
        return []
    return sorted(name for name, topic in bag.topics.items() if topic.msg_type == msg_type and name not in taken)


def check_bag(
    boundary: Boundary, bag: BagInfo, *, extra_inputs: Sequence[str] = (), accept: Sequence[str] = ()
) -> list[Problem]:
    """The bag against the boundary: every input present with the design's type, every way out declared."""
    accepted = set(accept)
    taken = set(boundary.input_topics()) | set(extra_inputs)
    problems: list[Problem] = [
        Problem(WARNING, "input_shadowed", f"{topic} of the bag is not fed: a remap takes its name")
        for topic in bag.shadowed
        if topic in taken
    ]
    for entry in boundary.inputs:
        recorded = bag.topics.get(entry.topic)
        if recorded is None:
            if entry.topic in accepted:
                continue
            if not entry.publishers:
                problems.append(
                    Problem(
                        WARNING,
                        "input_without_publisher",
                        f"input {entry.topic} has no publisher in the design and is not in the bag; "
                        f"its subscribers {', '.join(entry.subscribers)} receive nothing",
                    )
                )
            else:
                candidates = remap_candidates(bag, entry.msg_type, taken)
                problems.append(
                    Problem(
                        ERROR,
                        "input_missing",
                        f"boundary input {entry.topic} ({entry.msg_type}) is not in the bag; "
                        f"subscribed by {', '.join(entry.subscribers)}"
                        + (f"; bag topics of that type to remap: {', '.join(candidates)}" if candidates else ""),
                    )
                )
            continue
        if entry.msg_type and recorded.msg_type != entry.msg_type:
            problems.append(
                Problem(
                    ERROR,
                    "input_type",
                    f"boundary input {entry.topic} is {recorded.msg_type} in the bag, {entry.msg_type} in the design",
                )
            )
        if recorded.count == 0:
            problems.append(Problem(WARNING, "input_empty", f"boundary input {entry.topic} has no message in the bag"))
        if entry.scope_publishers:
            problems.append(
                Problem(
                    WARNING,
                    "input_mixed",
                    f"{entry.topic} is also published inside the scope by {', '.join(entry.scope_publishers)}: "
                    "the bag holds their recorded messages beside the replayed ones",
                )
            )
    for topic in extra_inputs:
        if topic not in bag.topics:
            problems.append(Problem(ERROR, "input_missing", f"declared input {topic} is not in the bag"))
    for edge in boundary.services:
        if edge.scope_peers or edge.name in accepted:
            continue
        other = "server" if edge.role == "client" else "client"
        problems.append(
            Problem(
                ERROR if edge.role == "client" else WARNING,
                "service_outside",
                f"{edge.role} {edge.name} ({edge.srv_type}) of {edge.node} has no {other} in the scope; "
                "the port model replays no service, list it under accept once it needs none",
            )
        )
    return problems


@dataclass(frozen=True)
class RealEndpoint:
    """An endpoint a process created, as its tracer reported it."""

    kind: str  # p | s | t
    node: str
    topic: str = ""
    period_ns: int = 0
    # Nodes of the endpoint's process; a helper node counts for the design nodes beside it.
    process_nodes: tuple[str, ...] = ()


def check_endpoints(
    graph: NodeGraph,
    scope_fqns: Mapping[str, str],
    endpoints: Sequence[RealEndpoint],
    *,
    accept: Sequence[str] = (),
    fed: Sequence[str] = (),
    unconnected: Optional[Mapping[str, set[str]]] = None,
) -> list[Problem]:
    """The scope's real subscriptions, publishers and timers against its declared ports and events.
    *fed* names the topics fed beside the declared ports; *unconnected* names the topics of declared
    ports the design connects to nothing."""
    accepted = set(accept)
    fed = set(fed)
    unconnected = unconnected or {}
    by_fqn = {fqn: graph.nodes[path] for path, fqn in scope_fqns.items() if path in graph.nodes}
    problems: list[Problem] = []
    seen_inputs: dict[str, set[str]] = {fqn: set() for fqn in by_fqn}
    seen_outputs: dict[str, set[str]] = {fqn: set() for fqn in by_fqn}
    timer_rates: dict[str, list[float]] = {fqn: [] for fqn in by_fqn}
    reported: set[tuple[str, str, str]] = set()

    def declared_by(endpoint: RealEndpoint, inputs: bool) -> Optional[str]:
        """The design node whose port covers the endpoint: its own node, else one of its process."""
        for fqn in (endpoint.node,) + tuple(endpoint.process_nodes):
            node = by_fqn.get(fqn)
            if node is not None and endpoint.topic in (node.inputs if inputs else node.outputs):
                return fqn
        return None

    for endpoint in endpoints:
        if endpoint.kind == "t":
            if endpoint.node in timer_rates and endpoint.period_ns > 0:
                timer_rates[endpoint.node].append(1e9 / endpoint.period_ns)
            continue
        if endpoint.topic in INFRA_TOPICS:
            continue
        inputs = endpoint.kind == "s"
        owner = declared_by(endpoint, inputs)
        if owner is not None:
            (seen_inputs if inputs else seen_outputs)[owner].add(endpoint.topic)
            continue
        key = (endpoint.kind, endpoint.node, endpoint.topic)
        if key in reported:
            continue
        reported.add(key)
        if endpoint.topic in unconnected.get(endpoint.node, ()):
            if inputs:
                problems.append(
                    Problem(
                        WARNING,
                        "input_unconnected",
                        f"{endpoint.node} subscribes to {endpoint.topic}, a port the design connects to nothing",
                    )
                )
            continue
        if inputs and endpoint.topic in fed:
            continue
        if inputs:
            level = WARNING if endpoint.topic in accepted else ERROR
            problems.append(
                Problem(
                    level,
                    "undeclared_subscription",
                    f"{endpoint.node} subscribes to {endpoint.topic}, which no port of its process declares: "
                    "the boundary misses this input",
                )
            )
        else:
            problems.append(
                Problem(
                    WARNING,
                    "undeclared_publisher",
                    f"{endpoint.node} publishes {endpoint.topic}, which no port of its process declares: "
                    "the output is not recorded",
                )
            )

    for fqn, node in by_fqn.items():
        for topic in node.inputs:
            if topic not in seen_inputs[fqn] and topic not in INFRA_TOPICS:
                problems.append(
                    Problem(WARNING, "input_not_subscribed", f"{fqn} declares input {topic} and never subscribed to it")
                )
        for topic in node.outputs:
            if topic not in seen_outputs[fqn] and topic not in INFRA_TOPICS:
                problems.append(
                    Problem(WARNING, "output_not_published", f"{fqn} declares output {topic} and created no publisher")
                )
        declared = sorted({e.frequency for e in node.events.values() if e.type == "periodic" and e.frequency})
        actual = timer_rates[fqn]
        for rate in declared:
            if not any(abs(a - rate) <= RATE_TOLERANCE * rate for a in actual):
                problems.append(
                    Problem(
                        WARNING,
                        "event_mismatch",
                        f"{fqn} declares a periodic process at {rate:g} Hz and has no timer at that rate "
                        f"(timers: {', '.join(f'{a:g} Hz' for a in sorted(actual)) or 'none'})",
                    )
                )
    return problems
