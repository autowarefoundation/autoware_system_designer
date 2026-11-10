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

"""Live and recorded rates of a running system from the tracer's counters.

A reading sums the counters of every traced process per key (node, ``in`` /
``out`` / ``timer``, topic or period), the final counters of processes that
ended included, so the totals of a key never fall over a run; rates and mean
latencies are the difference of two readings (``rates_record.rates_between``).
Nothing subscribes to a topic: publishes and takes are the ones the nodes make
through rcl, so a publish that reaches intra-process readers only is not seen.

Per node: input topics with take rate and mean communication time (take minus
DDS source timestamp), output topics with publish rate and mean callback time
(publish minus the thread's latest take or timer fire), timers with fire rate.
rcl timers belong to no node; a timer is listed under the node of the latest
publish it led to, else under its process.

The recorder appends a reading to the run's ``rates.jsonl`` at a fixed
interval and once more at shutdown, so a past run's rates can be read back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Optional, Union

from ...rates_record import Counters, Key, RatesWriter, Sample, rates_between
from .node_graph import NodeGraph
from .node_stats import LIFECYCLE_TOPICS
from .stats_reader import SLOT_PUB, SLOT_SUB, SLOT_TIMER, ProcessStats, StatsDir
from .trace_reader import Endpoint, TimerInfo

logger = logging.getLogger(__name__)

# Interval of the samples in a run's rates record.
RECORD_INTERVAL_S = 10.0


def _add(totals: dict[Key, Counters], key: Key, count: int, lat_sum: int, lat_count: int, last_ns: int) -> None:
    held = totals.get(key)
    if held is None:
        totals[key] = (count, lat_sum, lat_count, last_ns)
    else:
        totals[key] = (held[0] + count, held[1] + lat_sum, held[2] + lat_count, max(held[3], last_ns))


class RateMonitor:
    """Reads the counters of a trace directory into per-key totals over the run."""

    def __init__(self, trace_dir: Union[str, Path], graph: Optional[NodeGraph] = None, *, alive=None) -> None:
        self._dir = StatsDir(trace_dir, **({"alive": alive} if alive is not None else {}))
        self._graph = graph
        self._paths: dict[str, str] = {}
        # Final totals of the processes that ended.
        self._ended: dict[Key, Counters] = {}
        self._untracked_ended = 0

    def close(self) -> None:
        self._dir.close()

    def read(self) -> Sample:
        procs = self._dir.refresh(self._retire)
        sample = Sample(t_ns=time.time_ns(), totals=dict(self._ended), untracked=self._untracked_ended)
        for proc in procs.values():
            sample.untracked += self._accumulate(proc, sample.totals)
        sample.processes = len(procs)
        return sample

    def rates(self, before: Sample, after: Sample) -> dict[str, Any]:
        """Per-node rates over the interval between two readings, ordered by node."""
        return rates_between(before, after)

    def node_key(self, fqn: str) -> str:
        """Design path of a node, its fully qualified name when the design does not know it."""
        key = self._paths.get(fqn)
        if key is None:
            info = self._graph.node_for_fqn(fqn) if self._graph is not None else None
            key = self._paths[fqn] = info.path if info is not None else fqn
        return key

    def _retire(self, proc: ProcessStats) -> None:
        self._untracked_ended += self._accumulate(proc, self._ended)

    def _accumulate(self, proc: ProcessStats, totals: dict[Key, Counters]) -> int:
        """Adds one process's slots to ``totals``; returns its endpoints without counters."""
        counters = proc.read()
        names = proc.read_names()
        for index, slot in counters.slots.items():
            entry = names.get(index)
            key = self._key(proc.pid, slot.kind, entry, slot.next_slot, names) if entry is not None else None
            if key is not None:
                _add(totals, key, slot.count, slot.lat_sum_ns, slot.lat_count, slot.last_ns)
        return counters.dropped

    def _key(self, pid, kind, entry, next_slot, names) -> Optional[Key]:
        if kind == SLOT_TIMER and isinstance(entry, TimerInfo):
            output = names.get(next_slot) if next_slot is not None else None
            owner = self.node_key(output.fqn) if isinstance(output, Endpoint) else f"(process {pid})"
            return owner, "timer", entry.period_ns
        if not isinstance(entry, Endpoint) or entry.topic in LIFECYCLE_TOPICS:
            return None
        if kind == SLOT_SUB:
            return self.node_key(entry.fqn), "in", entry.topic
        if kind == SLOT_PUB:
            return self.node_key(entry.fqn), "out", entry.topic
        return None


class RatesRecorder:
    """Appends a reading of the monitor to the run's rates record every interval, and once at close."""

    def __init__(self, monitor: RateMonitor, path: Union[str, Path], *, interval_s: float = RECORD_INTERVAL_S) -> None:
        self._monitor = monitor
        self._interval_s = interval_s
        self._writer = RatesWriter(path, interval_s=interval_s)

    @property
    def path(self) -> Path:
        return self._writer.path

    async def run(self) -> None:
        while True:
            self.sample()
            await asyncio.sleep(self._interval_s)

    def sample(self) -> None:
        try:
            self._writer.write(self._monitor.read())
        except Exception:  # noqa: BLE001
            logger.exception("recording the rates failed")

    def close(self) -> None:
        """Records the final totals; called once every process has ended."""
        self.sample()
        self._writer.close()


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


def _hz(value: float) -> str:
    return f"{value:8.2f} Hz"
