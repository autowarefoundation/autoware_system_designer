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

"""The ``autoware_system_designer/latency/2`` file and the measurement report.

Records are keyed by node path and topic, never by process name or unique_id.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from .chains import ChainSummary
from .detect import STATUS_MATCH, NodeDiff, diff_node, observed_trigger
from .node_graph import NodeGraph
from .node_stats import NS_PER_MS, Analysis, NodeObs, summarize

LATENCY_SCHEMA = "autoware_system_designer/latency/2"
TRACER_VERSION = "0.1.0"


def _iso(t_ns: int) -> str:
    return datetime.fromtimestamp(t_ns / 1e9, tz=timezone.utc).isoformat()


def _node_record(graph: NodeGraph, analysis: Analysis, node: NodeObs, diff: NodeDiff) -> dict[str, Any]:
    inputs = []
    for topic in sorted(set(node.takes_by_topic) | set(node.dup_takes)):
        count = len(node.takes_by_topic.get(topic, []))
        record: dict[str, Any] = {"topic": topic, "count": count, "rate_hz": _round(analysis.rate(count))}
        if topic in node.intra_inputs:
            record["intra_process"] = True
        if node.dup_takes.get(topic):
            record["duplicate_count"] = node.dup_takes[topic]
        inputs.append(record)

    timers = []
    for (pid, handle), period_ns in sorted(node.timer_handles.items(), key=lambda item: item[1] or 0):
        if analysis.timer_owner.get((pid, handle)) != node.key:
            continue
        fires = analysis.timer_fires.get((pid, handle), 0)
        timers.append(
            {
                "period_ms": _round(period_ns / NS_PER_MS) if period_ns else None,
                "count": fires,
                "rate_hz": _round(analysis.rate(fires)),
            }
        )

    outputs = []
    for topic in sorted(node.pubs_by_topic):
        pubs = node.pubs_by_topic[topic]
        record = {"topic": topic, "count": len(pubs), "rate_hz": _round(analysis.rate(len(pubs)))}
        observed = observed_trigger(node, topic)
        if observed is not None:
            record["trigger"] = observed.as_dict()
            record["trigger_distribution"] = observed.distribution
        exec_summary = summarize([p.exec_ns for p in pubs if p.exec_ns is not None])
        if exec_summary is not None:
            record["exec"] = exec_summary.as_dict()
        responses = []
        for (out_topic, in_topic), values in sorted(node.response.items()):
            if out_topic != topic:
                continue
            summary = summarize(values)
            if summary is not None:
                responses.append({"from": in_topic, **summary.as_dict()})
        if responses:
            record["response"] = responses
        outputs.append(record)

    record = {
        "node_path": node.path,
        "inputs": inputs,
        "timers": timers,
        "outputs": outputs,
        "declared_diff": [row.as_dict() for row in diff.rows],
    }
    notes: dict[str, list[str]] = {}
    if diff.never_taken:
        notes["declared_trigger_never_taken"] = diff.never_taken
    if diff.feeds_nothing:
        notes["input_feeds_nothing_declared"] = diff.feeds_nothing
    if diff.undeclared_inputs:
        notes["input_not_in_design"] = diff.undeclared_inputs
    if notes:
        record["notes"] = notes
    return record


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def _link_records(graph: NodeGraph, analysis: Analysis) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for (topic, publisher, subscriber), values in sorted(
        analysis.links.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2])
    ):
        summary = summarize(values)
        if summary is None:
            continue
        record: dict[str, Any] = {"topic": topic}
        if publisher is not None:
            record["publisher"] = _path_of(analysis, publisher)
        record["subscriber"] = _path_of(analysis, subscriber)
        record.update(summary.as_dict())
        records.append(record)
    for link in graph.links:
        if link.intra_process:
            records.append(
                {"topic": link.topic, "publisher": link.publisher, "subscriber": link.subscriber, "intra_process": True}
            )
    return records


def _path_of(analysis: Analysis, key: str) -> str:
    node = analysis.nodes.get(key)
    return node.path if node is not None and node.path else key


def build_latency_file(
    graph: NodeGraph,
    analysis: Analysis,
    chains: list[ChainSummary],
    *,
    mode: Optional[str],
    probe: bool,
    started_ns: Optional[int] = None,
) -> tuple[dict[str, Any], dict[str, NodeDiff]]:
    diffs = {node.key: diff_node(graph, analysis, node) for node in analysis.matched_nodes()}
    nodes = []
    for node in sorted(analysis.matched_nodes(), key=lambda n: n.path or ""):
        record = _node_record(graph, analysis, node, diffs[node.key])
        # A node seen only through its infrastructure endpoints (containers) has nothing to say.
        if record["inputs"] or record["timers"] or record["outputs"] or record["declared_diff"]:
            nodes.append(record)
    unmatched = [
        {
            "node": node.fqn,
            "pids": sorted(node.pids),
            "publishes": sum(len(p) for p in node.pubs_by_topic.values()),
            "takes": sum(len(t) for t in node.takes_by_topic.values()),
        }
        for node in sorted(analysis.unmatched_nodes(), key=lambda n: n.fqn)
    ]
    data = {
        "schema": LATENCY_SCHEMA,
        "mode": mode,
        "run": {
            "started": _iso(started_ns or analysis.window_start_ns),
            "window_start": _iso(analysis.window_start_ns),
            "window_end": _iso(analysis.window_end_ns),
            "window_s": _round(analysis.window_s, 3),
            "probe": probe,
            "tracer": TRACER_VERSION,
            "processes": analysis.process_count,
            "dropped_records": analysis.dropped_records,
            "invalid_communication_samples": analysis.comm_invalid,
        },
        "nodes": nodes,
        "links": _link_records(graph, analysis),
        "chains": [chain.as_dict() for chain in chains],
        "unmatched_nodes": unmatched,
    }
    return data, diffs


def write_latency_file(data: dict[str, Any], path: Union[str, Path]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# ---- report -----------------------------------------------------------------------


def _fmt(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.2f}"


def build_report(
    graph: NodeGraph,
    analysis: Analysis,
    data: dict[str, Any],
    diffs: dict[str, NodeDiff],
    chains: list[ChainSummary],
) -> str:
    run = data["run"]
    lines = [f"# Measurement report: {data.get('mode') or 'system'}", ""]
    lines += [
        f"- window: {run['window_s']} s from {run['window_start']} to {run['window_end']}",
        f"- traced processes: {run['processes']}, probe subscriptions: {'on' if run['probe'] else 'off'}",
        f"- design nodes: {len(graph.nodes)}, observed: {len(analysis.matched_nodes())}, "
        f"not in design: {len(analysis.unmatched_nodes())}",
        f"- dropped records: {run['dropped_records']}, invalid communication samples: "
        f"{run['invalid_communication_samples']}",
        "",
    ]

    rows = []
    matches = 0
    for node in sorted(analysis.matched_nodes(), key=lambda n: n.path or ""):
        for row in diffs[node.key].rows:
            if row.status == STATUS_MATCH:
                matches += 1
                continue
            rows.append((node.path, row))
    lines += ["## Declared vs observed", ""]
    lines.append(f"{matches} output(s) match their declared trigger; {len(rows)} differ or are unobserved.")
    lines.append("")
    if rows:
        lines += ["| node | output | declared | observed | status | note |", "| --- | --- | --- | --- | --- | --- |"]
        for path, row in rows:
            lines.append(f"| {path} | {row.output} | {row.declared} | {row.observed} | {row.status} | {row.note} |")
        lines.append("")

    notes = []
    for node in sorted(analysis.matched_nodes(), key=lambda n: n.path or ""):
        diff = diffs[node.key]
        for topic in diff.never_taken:
            notes.append(f"| {node.path} | {topic} | declared trigger never taken |")
        for topic in diff.feeds_nothing:
            notes.append(f"| {node.path} | {topic} | taken, feeds no declared process |")
        for topic in diff.undeclared_inputs:
            notes.append(f"| {node.path} | {topic} | taken, not an input of the design |")
    if notes:
        lines += ["## Inputs", "", "| node | topic | note |", "| --- | --- | --- |", *notes, ""]

    unobserved = sorted(set(graph.nodes) - {n.path for n in analysis.matched_nodes()})
    if unobserved:
        lines += ["## Design nodes without records", ""]
        lines += [f"- {path}" for path in unobserved]
        lines.append("")
    if data["unmatched_nodes"]:
        lines += ["## Traced nodes not in the design", ""]
        lines += [
            f"- {u['node']} (pid {', '.join(map(str, u['pids']))}): {u['publishes']} publishes, {u['takes']} takes"
            for u in data["unmatched_nodes"]
        ]
        lines.append("")

    approx = []
    for node in sorted(analysis.matched_nodes(), key=lambda n: n.path or ""):
        for topic, pubs in sorted(node.pubs_by_topic.items()):
            unknown = sum(1 for p in pubs if p.trigger is None or p.trigger.kind == "unknown")
            if unknown:
                approx.append(f"| {node.path} | {topic} | {unknown}/{len(pubs)} publishes with no visible trigger |")
    lines += ["## Approximations", ""]
    lines.append("- intra-process hops are folded into the downstream node's process time and carry no transport")
    lines.append(
        "- a publish from a thread that never took or fired has an unknown trigger; its response is still computed"
    )
    if approx:
        lines += ["", "| node | output | note |", "| --- | --- | --- |", *approx]
    lines.append("")

    if chains:
        lines += [
            "## Chains",
            "",
            "| from | to | hops | count | mean ms | max ms |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for chain in chains:
            if not chain.terminal:
                continue
            s = chain.summary
            lines.append(
                f"| {chain.source} | {chain.target} | {chain.hops} | {s.count} | {_fmt(s.mean_ms)} | {_fmt(s.max_ms)} |"
            )
        lines.append("")
    return "\n".join(lines)


def write_report(text: str, path: Union[str, Path]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
