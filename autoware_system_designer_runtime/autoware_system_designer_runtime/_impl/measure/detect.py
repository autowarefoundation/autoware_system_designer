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

"""Trigger detection and the declared-versus-observed diff.

Detected triggers form the observed event table of a node; the design's process
events are the claim it is compared against, output by output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .node_graph import DeclaredTrigger, NodeGraph
from .node_stats import NS_PER_MS, Analysis, NodeObs, Trigger

# Observed and declared rates within this fraction of each other agree.
RATE_TOLERANCE = 0.1

STATUS_MATCH = "match"
STATUS_MISMATCH = "mismatch"
STATUS_RATE_MISMATCH = "rate_mismatch"
STATUS_UNOBSERVED = "unobserved"
STATUS_UNDECLARED = "undeclared"
STATUS_UNKNOWN = "unknown"


@dataclass
class ObservedTrigger:
    """Dominant trigger class of one output over the run."""

    kind: str
    share: float
    total: int
    topic: Optional[str] = None
    period_ms: Optional[float] = None
    intra: bool = False
    distribution: dict[str, int] = field(default_factory=dict)

    def label(self) -> str:
        if self.kind == "timer":
            return f"timer {self.period_ms:g} ms" if self.period_ms else "timer"
        if self.kind == "input":
            return f"input {self.topic}" + (" (intra_process)" if self.intra else "")
        return "unknown"

    def as_dict(self) -> dict:
        out: dict = {"kind": self.kind, "share": round(self.share, 4)}
        if self.kind == "timer":
            out["period_ms"] = self.period_ms
        elif self.kind == "input":
            out["topic"] = self.topic
            out["intra_process"] = self.intra
        return out


def observed_trigger(node: NodeObs, topic: str) -> Optional[ObservedTrigger]:
    pubs = node.pubs_by_topic.get(topic, [])
    if not pubs:
        return None
    counts: dict[tuple, int] = {}
    labels: dict[str, int] = {}
    for pub in pubs:
        trigger = pub.trigger or Trigger(kind="unknown", t=0)
        counts[trigger.key()] = counts.get(trigger.key(), 0) + 1
        labels[trigger.label()] = labels.get(trigger.label(), 0) + 1
    (kind, trig_topic, period_ns, intra), best = max(counts.items(), key=lambda item: item[1])
    return ObservedTrigger(
        kind=kind,
        share=best / len(pubs),
        total=len(pubs),
        topic=trig_topic,
        period_ms=period_ns / NS_PER_MS if period_ns else None,
        intra=intra,
        distribution=dict(sorted(labels.items(), key=lambda item: -item[1])),
    )


@dataclass
class DiffRow:
    output: str
    declared: str
    observed: str
    status: str
    note: str = ""

    def as_dict(self) -> dict:
        out = {"output": self.output, "declared": self.declared, "observed": self.observed, "status": self.status}
        if self.note:
            out["note"] = self.note
        return out


def _rates_agree(declared_hz: Optional[float], observed_hz: Optional[float]) -> Optional[bool]:
    if not declared_hz or not observed_hz:
        return None
    return abs(observed_hz - declared_hz) <= RATE_TOLERANCE * declared_hz


def diff_output(
    declared: DeclaredTrigger,
    observed: Optional[ObservedTrigger],
    *,
    observed_rate_hz: Optional[float],
) -> DiffRow:
    """Compare one output's declared trigger with what the trace showed."""
    declared_label = declared.label()
    if observed is None:
        status = STATUS_UNOBSERVED if declared.kind != "none" else STATUS_UNDECLARED
        return DiffRow(output="", declared=declared_label, observed="not published", status=status)

    observed_label = observed.label()
    if observed_rate_hz:
        observed_label += f" @ {observed_rate_hz:.1f} Hz"

    if observed.kind == "unknown":
        return DiffRow(
            "", declared_label, observed_label, STATUS_UNKNOWN, "trigger not visible on the publishing thread"
        )

    if declared.kind == "none":
        return DiffRow("", declared_label, observed_label, STATUS_UNDECLARED, "no process event produces this output")

    if declared.kind == "untriggered":
        return DiffRow("", declared_label, observed_label, STATUS_MISMATCH, "the producing process declares no trigger")

    if declared.kind == "periodic":
        if observed.kind != "timer":
            return DiffRow("", declared_label, observed_label, STATUS_MISMATCH, "declared periodic, fired by an input")
        observed_hz = 1000.0 / observed.period_ms if observed.period_ms else observed_rate_hz
        agree = _rates_agree(declared.rate_hz, observed_hz)
        if agree is False:
            return DiffRow("", declared_label, observed_label, STATUS_RATE_MISMATCH)
        return DiffRow("", declared_label, observed_label, STATUS_MATCH)

    # declared.kind == "input"
    if observed.kind == "timer":
        if declared.upstream_rate_hz:
            agree = _rates_agree(declared.upstream_rate_hz, 1000.0 / observed.period_ms if observed.period_ms else None)
            if agree is False:
                return DiffRow(
                    "", declared_label, observed_label, STATUS_RATE_MISMATCH, "clock reached through a process"
                )
            return DiffRow("", declared_label, observed_label, STATUS_MATCH, "clock reached through a process")
        return DiffRow("", declared_label, observed_label, STATUS_MISMATCH, "declared input-driven, fired by a timer")
    if observed.topic in declared.topics:
        row = DiffRow("", declared_label, observed_label, STATUS_MATCH)
        agree = _rates_agree(declared.rate_hz, observed_rate_hz)
        if agree is False:
            row.status = STATUS_RATE_MISMATCH
        return row
    return DiffRow("", declared_label, observed_label, STATUS_MISMATCH, "fired by an input the design does not name")


@dataclass
class NodeDiff:
    rows: list[DiffRow] = field(default_factory=list)
    # Declared trigger topics never taken, inputs taken that feed nothing declared.
    never_taken: list[str] = field(default_factory=list)
    feeds_nothing: list[str] = field(default_factory=list)
    undeclared_inputs: list[str] = field(default_factory=list)


def diff_node(graph: NodeGraph, analysis: Analysis, node: NodeObs) -> NodeDiff:
    result = NodeDiff()
    info = node.info
    if info is None:
        return result
    topics = sorted(set(info.outputs) | set(node.pubs_by_topic))
    for topic in topics:
        declared = graph.declared_trigger(info, topic) if topic in info.outputs else DeclaredTrigger(kind="none")
        observed = observed_trigger(node, topic)
        rate = analysis.rate(len(node.pubs_by_topic.get(topic, [])))
        row = diff_output(declared, observed, observed_rate_hz=rate)
        row.output = topic
        if topic not in info.outputs:
            row.note = "output not in the design"
        result.rows.append(row)

    declared_trigger_topics: set[str] = set()
    for topic in info.outputs:
        declared_trigger_topics.update(graph.declared_trigger(info, topic).topics)
    taken = set(node.takes_by_topic) | set(node.dup_takes)
    for topic in sorted(declared_trigger_topics):
        if topic not in taken and topic not in node.intra_inputs:
            result.never_taken.append(topic)
    for topic in sorted(taken):
        if topic not in info.inputs:
            result.undeclared_inputs.append(topic)
        elif not graph.declared_consumers(info, topic):
            result.feeds_nothing.append(topic)
    return result
