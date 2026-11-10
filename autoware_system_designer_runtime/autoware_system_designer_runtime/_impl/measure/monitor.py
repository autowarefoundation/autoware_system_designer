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

"""Live rates of a running system from the tracer's counters.

A reading copies the counters of every live traced process; rates and mean
latencies are the difference of two readings over the wall time between them.
Nothing subscribes to a topic: publishes and takes are the ones the nodes make
through rcl, so a publish that reaches intra-process readers only is not seen.

Per node: input topics with take rate and mean communication time (take minus
DDS source timestamp), output topics with publish rate and mean callback time
(publish minus the thread's latest take or timer fire), timers with fire rate.
rcl timers belong to no node; a timer is listed under the node of the latest
publish it led to, else under its process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from .node_graph import NodeGraph
from .node_stats import LIFECYCLE_TOPICS, NS_PER_MS, NS_PER_S
from .stats_reader import SLOT_PUB, SLOT_SUB, SLOT_TIMER, ProcessCounters, StatsDir
from .trace_reader import Endpoint, TimerInfo


@dataclass
class Reading:
    t_ns: int
    processes: dict[int, ProcessCounters] = field(default_factory=dict)
    names: dict[int, dict[int, Union[Endpoint, TimerInfo]]] = field(default_factory=dict)


@dataclass
class _Row:
    count: int = 0
    lat_sum_ns: int = 0
    lat_count: int = 0
    last_ns: int = 0
    known: bool = True  # every slot of the row was in the earlier reading

    def add(self, count: int, lat_sum_ns: int, lat_count: int, last_ns: int, known: bool) -> None:
        self.count += count
        self.lat_sum_ns += lat_sum_ns
        self.lat_count += lat_count
        self.last_ns = max(self.last_ns, last_ns)
        self.known = self.known and known


class RateMonitor:
    """Reads the counters of a trace directory and turns two readings into per-node rates."""

    def __init__(self, trace_dir: Union[str, Path], graph: Optional[NodeGraph] = None, *, alive=None) -> None:
        self._dir = StatsDir(trace_dir, **({"alive": alive} if alive is not None else {}))
        self._graph = graph
        self._paths: dict[str, str] = {}

    def close(self) -> None:
        self._dir.close()

    def read(self) -> Reading:
        reading = Reading(t_ns=time.time_ns())
        for pid, proc in self._dir.refresh().items():
            reading.processes[pid] = proc.read()
            reading.names[pid] = dict(proc.read_names())
        return reading

    def node_key(self, fqn: str) -> str:
        """Design path of a node, its fully qualified name when the design does not know it."""
        key = self._paths.get(fqn)
        if key is None:
            info = self._graph.node_for_fqn(fqn) if self._graph is not None else None
            key = self._paths[fqn] = info.path if info is not None else fqn
        return key

    def rates(self, before: Reading, after: Reading) -> dict[str, Any]:
        """Per-node rates over the interval between two readings, ordered by node."""
        interval_ns = max(after.t_ns - before.t_ns, 1)
        rows: dict[tuple[str, str, Any], _Row] = {}
        pids: dict[str, set[int]] = {}
        untracked = 0
        for pid, counters in after.processes.items():
            untracked += counters.dropped
            names = after.names.get(pid, {})
            earlier = before.processes.get(pid)
            for index, slot in counters.slots.items():
                entry = names.get(index)
                if entry is None:
                    continue
                previous = earlier.slots.get(index) if earlier is not None else None
                known = previous is not None and previous.handle == slot.handle and previous.count <= slot.count
                base = previous if known else None
                key = self._row_key(pid, slot.kind, entry, slot.next_slot, names)
                if key is None:
                    continue
                pids.setdefault(key[0], set()).add(pid)
                rows.setdefault(key, _Row()).add(
                    slot.count - (base.count if base else 0),
                    slot.lat_sum_ns - (base.lat_sum_ns if base else 0),
                    slot.lat_count - (base.lat_count if base else 0),
                    slot.last_ns,
                    known,
                )

        nodes: dict[str, dict[str, Any]] = {}
        for (node, direction, label), row in sorted(rows.items(), key=lambda item: _sort_key(item[0])):
            record = nodes.setdefault(
                node, {"node": node, "pids": sorted(pids[node]), "inputs": [], "outputs": [], "timers": []}
            )
            record[direction].append(_row_record(direction, label, row, interval_ns, after.t_ns))
        return {
            "time_ns": after.t_ns,
            "interval_s": round(interval_ns / NS_PER_S, 3),
            "processes": len(after.processes),
            "untracked_endpoints": untracked,
            "nodes": list(nodes.values()),
        }

    def _row_key(self, pid, kind, entry, next_slot, names) -> Optional[tuple[str, str, Any]]:
        if kind == SLOT_TIMER and isinstance(entry, TimerInfo):
            output = names.get(next_slot) if next_slot is not None else None
            owner = self.node_key(output.fqn) if isinstance(output, Endpoint) else f"(process {pid})"
            return owner, "timers", entry.period_ns
        if not isinstance(entry, Endpoint) or entry.topic in LIFECYCLE_TOPICS:
            return None
        if kind == SLOT_SUB:
            return self.node_key(entry.fqn), "inputs", entry.topic
        if kind == SLOT_PUB:
            return self.node_key(entry.fqn), "outputs", entry.topic
        return None


def _sort_key(key: tuple[str, str, Any]) -> tuple:
    node, direction, label = key
    return (node, direction, str(label) if not isinstance(label, int) else f"{label:020d}")


def _row_record(direction: str, label: Any, row: _Row, interval_ns: int, now_ns: int) -> dict[str, Any]:
    if direction == "timers":
        record: dict[str, Any] = {"period_ms": round(label / NS_PER_MS, 3)}
    else:
        record = {"topic": label}
    # A row with a slot new since the earlier reading has no rate yet.
    record["hz"] = round(row.count * NS_PER_S / interval_ns, 3) if row.known else None
    if row.lat_count > 0 and direction != "timers":
        record["comm_ms" if direction == "inputs" else "callback_ms"] = round(
            row.lat_sum_ns / row.lat_count / NS_PER_MS, 3
        )
    if row.known and row.count == 0 and row.last_ns > 0:
        record["silent_s"] = round((now_ns - row.last_ns) / NS_PER_S, 1)
    return record


def format_rates(snapshot: dict[str, Any], pattern: Optional[str] = None) -> list[str]:
    """Plain-text table of a rates snapshot, nodes whose key contains *pattern* only."""
    lines = [f"rates over {snapshot['interval_s']:g} s, {snapshot['processes']} traced processes"]
    for node in snapshot["nodes"]:
        if pattern and pattern not in node["node"]:
            continue
        lines.append(node["node"])
        for direction, arrow in (("inputs", "<-"), ("outputs", "->")):
            for row in node[direction]:
                extra = ""
                if "comm_ms" in row:
                    extra = f"  comm {row['comm_ms']:.3f} ms"
                elif "callback_ms" in row:
                    extra = f"  callback {row['callback_ms']:.3f} ms"
                if "silent_s" in row:
                    extra += f"  silent {row['silent_s']:g} s"
                lines.append(f"  {arrow} {row['topic']:<60} {_hz(row['hz'])}{extra}")
        for row in node["timers"]:
            lines.append(f"  timer {row['period_ms']:g} ms{'':<47} {_hz(row['hz'])}")
    if snapshot.get("untracked_endpoints"):
        lines.append(f"{snapshot['untracked_endpoints']} endpoints past the counter capacity are not counted")
    return lines


def _hz(value: Optional[float]) -> str:
    return "    new" if value is None else f"{value:8.2f} Hz"
