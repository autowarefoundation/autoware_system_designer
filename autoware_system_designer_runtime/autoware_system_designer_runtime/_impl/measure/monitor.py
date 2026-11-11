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

A reading sums the counts of every endpoint per key (node, ``in`` / ``out`` /
``timer``, topic or timer label), endpoints and processes that ended included,
so the totals of a key never fall over a run; rates and mean latencies are the
difference of two readings (``rates_record.rates_between``).
Nothing subscribes to a topic: publishes and takes are the ones the nodes make
through rcl, so a publish that reaches intra-process readers only is not seen.

Per node: input topics with take rate and mean communication time (take minus
DDS source timestamp), output topics with publish rate and mean callback time
(publish minus the thread's latest take or timer fire), timers with fire rate.
rcl timers belong to no node; a timer is listed under its process until a fire
of it leads to a publish, from then on under the node of that publish with the
fires since. Each timer has a key of its own: its label is its period and its
rank among the timers of that period under the same owner.

The recorder appends a reading to the run's ``rates.jsonl`` at a fixed
interval and once more at shutdown, so a past run's rates can be read back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from ...rates_record import Counters, Key, RatesWriter, Sample, rates_between
from .node_graph import NodeGraph
from .node_stats import LIFECYCLE_TOPICS
from .stats_reader import ProcessStats, StatsDir, counts_since
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


@dataclass
class _Timer:
    """One timer between its creation and its end."""

    t_init: int
    owner: str
    rank: int
    pinned: bool = False  # owned by a node
    seen: int = 0  # fires at the latest reading
    skipped: int = 0  # fires before it was pinned, left out of the node's key


@dataclass
class _Process:
    # Totals of the endpoints that ended, per key.
    retired: dict[Key, Counters] = field(default_factory=dict)
    # Live timers by slot.
    timers: dict[int, _Timer] = field(default_factory=dict)


class RateMonitor:
    """Reads the counters of a trace directory into per-key totals over the run."""

    def __init__(self, trace_dir: Union[str, Path], graph: Optional[NodeGraph] = None, *, alive=None) -> None:
        self._dir = StatsDir(trace_dir, **({"alive": alive} if alive is not None else {}))
        self._graph = graph
        self._paths: dict[str, str] = {}
        # Final totals of the processes that ended.
        self._ended: dict[Key, Counters] = {}
        self._untracked_ended = 0
        self._processes: dict[int, _Process] = {}
        # Ranks in use per (owner, period).
        self._ranks: dict[tuple[str, int], set[int]] = {}

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
        state = self._processes.pop(proc.pid, None)
        for slot, timer in (state.timers if state is not None else {}).items():
            entry = proc.names.get(slot)
            if isinstance(entry, TimerInfo):
                self._release(timer, entry.period_ns)

    def _accumulate(self, proc: ProcessStats, totals: dict[Key, Counters]) -> int:
        """Adds one process's endpoints to ``totals``; returns its endpoints without counters."""
        state = self._processes.setdefault(proc.pid, _Process())
        counters = proc.read()
        names = proc.read_names()
        for entry, counts in proc.take_ended():
            self._end(proc.pid, state, entry, counts)
        for key, counts in state.retired.items():
            _add(totals, key, *counts)
        for index, slot in counters.slots.items():
            entry = names.get(index)
            if entry is None:
                continue
            counts = counts_since(entry.base, (slot.count, slot.lat_sum_ns, slot.lat_count, slot.last_ns))
            if isinstance(entry, TimerInfo):
                timer = self._timer(proc.pid, state, entry)
                output = names.get(slot.next_slot) if slot.next_slot is not None else None
                if not timer.pinned and isinstance(output, Endpoint) and output.kind == "pub":
                    self._pin(timer, entry.period_ns, self.node_key(output.fqn))
                timer.seen = counts[0]
                _add(totals, self._timer_key(timer, entry), *self._timer_counts(timer, counts))
                continue
            key = self._endpoint_key(entry)
            if key is not None:
                _add(totals, key, *counts)
        return counters.dropped

    def _end(self, pid: int, state: _Process, entry: Union[Endpoint, TimerInfo], counts: Counters) -> None:
        """Moves the counts of an endpoint whose slot went to another one to the retired totals."""
        if isinstance(entry, TimerInfo):
            timer = self._timer(pid, state, entry)
            _add(state.retired, self._timer_key(timer, entry), *self._timer_counts(timer, counts))
            self._release(timer, entry.period_ns)
            del state.timers[entry.slot]
            return
        key = self._endpoint_key(entry)
        if key is not None:
            _add(state.retired, key, *counts)

    def _endpoint_key(self, entry: Endpoint) -> Optional[Key]:
        if entry.topic in LIFECYCLE_TOPICS:
            return None
        return self.node_key(entry.fqn), "in" if entry.kind == "sub" else "out", entry.topic

    # ---- timers ------------------------------------------------------------------

    def _timer(self, pid: int, state: _Process, entry: TimerInfo) -> _Timer:
        timer = state.timers.get(entry.slot)
        if timer is None or timer.t_init != entry.t_init:
            owner = f"(process {pid})"
            timer = state.timers[entry.slot] = _Timer(entry.t_init, owner, self._claim(owner, entry.period_ns))
        return timer

    def _pin(self, timer: _Timer, period_ns: int, node: str) -> None:
        """Hands a timer to the node it publishes for; the fires read so far stay out of the node's key."""
        self._release(timer, period_ns)
        timer.owner, timer.rank, timer.pinned = node, self._claim(node, period_ns), True
        timer.skipped = timer.seen

    @staticmethod
    def _timer_key(timer: _Timer, entry: TimerInfo) -> Key:
        return timer.owner, "timer", (entry.period_ns, timer.rank)

    @staticmethod
    def _timer_counts(timer: _Timer, counts: Counters) -> Counters:
        fires = max(counts[0] - timer.skipped, 0)
        return (fires, 0, 0, counts[3] if fires else 0)

    def _claim(self, owner: str, period_ns: int) -> int:
        used = self._ranks.setdefault((owner, period_ns), set())
        rank = next(n for n in range(len(used) + 1) if n not in used)
        used.add(rank)
        return rank

    def _release(self, timer: _Timer, period_ns: int) -> None:
        used = self._ranks.get((timer.owner, period_ns))
        if used is not None:
            used.discard(timer.rank)
            if not used:
                del self._ranks[(timer.owner, period_ns)]


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
