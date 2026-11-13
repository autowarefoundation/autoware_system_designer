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

"""Topic and timer counters of a run over time, and the rates between two points of it.

A sample holds, per key ``(node, kind, label)``, the counters ``(count, lat_sum_ns, lat_count,
last_ns)`` accumulated over the run, processes that ended included, so the rates over any span are
the difference of two samples. ``kind`` is ``in`` (takes of a topic), ``out`` (publishes of a topic)
or ``timer`` (fires of a timer, ``label`` its period in ns and its rank among the owner's timers of
that period); the latency is the communication time of a take and the callback time of a publish.

``<log-dir>/rates.jsonl`` records the samples of a run, one JSON object per line: a header
``{"schema", "interval_s"}``, then per sample ``{"t", "processes", "untracked", "keys"?, "c"}`` where
``keys`` names the keys new since the previous sample (``{id: [node, kind, label]}``) and ``c`` lists
``[id, count, lat_sum_ns, lat_count, last_ns]`` of the keys whose counters changed; the others keep
their values, except those listed by id in ``d``, which the sample no longer holds. The module
imports no ROS package.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

SCHEMA = "autoware_system_designer/rates/1"
RATES_FILE = "rates.jsonl"
NS_PER_S = 1_000_000_000
NS_PER_MS = 1_000_000

Key = tuple[str, str, Any]
# count, lat_sum_ns, lat_count, last_ns
Counters = tuple[int, int, int, int]

DIRECTIONS = {"in": "inputs", "out": "outputs", "timer": "timers"}


@dataclass
class Sample:
    t_ns: int
    totals: dict[Key, Counters] = field(default_factory=dict)
    processes: int = 0
    untracked: int = 0


def rates_between(before: Sample, after: Sample) -> dict[str, Any]:
    """Per-node rates over the span between two samples, nodes and rows in name order.

    Counters start at zero when their endpoint is created, so a key absent from the earlier sample
    counts from zero; a key without a message in the span carries ``silent_s``, the time since its
    last one.
    """
    interval_ns = max(after.t_ns - before.t_ns, 1)
    nodes: dict[str, dict[str, Any]] = {}
    for key in sorted(after.totals, key=_order):
        node, kind, label = key
        count, lat_sum, lat_count, last_ns = after.totals[key]
        earlier = before.totals.get(key, (0, 0, 0, 0))
        record: dict[str, Any] = (
            {"period_ms": round(_period_ns(label) / NS_PER_MS, 3)} if kind == "timer" else {"topic": label}
        )
        moved = max(count - earlier[0], 0)
        lat_sum_d, lat_count_d = lat_sum - earlier[1], lat_count - earlier[2]
        record["hz"] = round(moved * NS_PER_S / interval_ns, 3)
        if kind != "timer" and lat_count_d > 0:
            name = "comm_ms" if kind == "in" else "callback_ms"
            record[name] = round(lat_sum_d / lat_count_d / NS_PER_MS, 3)
        if moved == 0 and last_ns > 0:
            record["silent_s"] = round((after.t_ns - last_ns) / NS_PER_S, 1)
        entry = nodes.setdefault(node, {"node": node, "inputs": [], "outputs": [], "timers": []})
        entry[DIRECTIONS[kind]].append(record)
    return {
        "time_ns": after.t_ns,
        "from_ns": before.t_ns,
        "interval_s": round(interval_ns / NS_PER_S, 3),
        "processes": after.processes,
        "untracked_endpoints": after.untracked,
        "nodes": list(nodes.values()),
    }


def _period_ns(label: Any) -> int:
    return label[0] if isinstance(label, tuple) else label


def _order(key: Key) -> tuple:
    node, kind, label = key
    parts = label if isinstance(label, tuple) else (label,)
    return (node, kind, tuple(f"{part:020d}" if isinstance(part, int) else str(part) for part in parts))


class RatesWriter:
    """Appends samples to a rates file; stops, once, past ``max_bytes``."""

    def __init__(self, path: Union[str, Path], *, interval_s: float, max_bytes: int = 256 << 20) -> None:
        self.path = Path(path)
        self._ids: dict[Key, int] = {}
        self._last: dict[Key, Counters] = {}
        self._max_bytes = max_bytes
        self._written = 0
        self._full = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "w", encoding="utf-8")
        self._line({"schema": SCHEMA, "interval_s": interval_s})

    def write(self, sample: Sample) -> None:
        if self._full or self._file.closed:
            return
        line: dict[str, Any] = {"t": sample.t_ns, "processes": sample.processes, "untracked": sample.untracked}
        new = {}
        changed = []
        for key, counters in sample.totals.items():
            if key not in self._ids:
                self._ids[key] = len(self._ids)
                new[str(self._ids[key])] = list(key)
            if self._last.get(key) != counters:
                self._last[key] = counters
                changed.append([self._ids[key], *counters])
        gone = [key for key in self._last if key not in sample.totals]
        for key in gone:
            del self._last[key]
        if new:
            line["keys"] = new
        line["c"] = changed
        if gone:
            line["d"] = [self._ids[key] for key in gone]
        self._line(line)

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def _line(self, obj: dict[str, Any]) -> None:
        text = json.dumps(obj, separators=(",", ":")) + "\n"
        if self._written + len(text) > self._max_bytes:
            self._full = True
            logger.warning("rates record %s reached %d bytes; later samples are not recorded", self.path, self._written)
            return
        self._file.write(text)
        self._file.flush()
        self._written += len(text)


class RatesRecord:
    """A rates file read back: sample times, and the totals at any sample."""

    def __init__(self, interval_s: Optional[float], lines: list[dict[str, Any]], keys: dict[int, Key]) -> None:
        self.interval_s = interval_s
        self._lines = lines
        self._keys = keys
        self._density: Optional[list[float]] = None

    @classmethod
    def load(cls, path: Union[str, Path]) -> "RatesRecord":
        """Reads a rates file; a torn last line (the run was killed while writing) is left out."""
        interval_s = None
        lines: list[dict[str, Any]] = []
        keys: dict[int, Key] = {}
        with open(path, encoding="utf-8") as handle:
            for text in handle:
                try:
                    obj = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if "schema" in obj:
                    if obj["schema"] != SCHEMA:
                        raise ValueError(f"{path}: unsupported rates schema {obj['schema']!r}")
                    interval_s = obj.get("interval_s")
                    continue
                if not isinstance(obj.get("t"), int):
                    continue
                for key_id, key in (obj.get("keys") or {}).items():
                    node, kind, label = key
                    keys[int(key_id)] = (node, kind, tuple(label) if isinstance(label, list) else label)
                lines.append(obj)
        return cls(interval_s, lines, keys)

    @property
    def times(self) -> list[int]:
        return [line["t"] for line in self._lines]

    def __len__(self) -> int:
        return len(self._lines)

    def samples(self, *indices: int) -> list[Sample]:
        """Samples at the given indices, replayed in one pass."""
        wanted = sorted(set(indices))
        out: dict[int, Sample] = {}
        totals: dict[Key, Counters] = {}
        for index, line in enumerate(self._lines):
            if index > wanted[-1]:
                break
            for key_id, count, lat_sum, lat_count, last_ns in line.get("c", []):
                key = self._keys.get(key_id)
                if key is not None:
                    totals[key] = (count, lat_sum, lat_count, last_ns)
            for key_id in line.get("d", []):
                totals.pop(self._keys.get(key_id), None)
            if index in wanted:
                out[index] = Sample(line["t"], dict(totals), line.get("processes", 0), line.get("untracked", 0))
        return [out[index] for index in indices]

    def density(self) -> list[float]:
        """Per sample, the takes, publishes and timer fires of every key per second over the interval
        ending at it; zero at the first sample."""
        if self._density is None:
            counts: dict[int, int] = {}
            density: list[float] = []
            previous_ns: Optional[int] = None
            for line in self._lines:
                moved = 0
                for key_id, count, *_ in line.get("c", []):
                    moved += max(count - counts.get(key_id, 0), 0)
                    counts[key_id] = count
                for key_id in line.get("d", []):
                    counts.pop(key_id, None)
                if previous_ns is None:
                    density.append(0.0)
                else:
                    density.append(round(moved * NS_PER_S / max(line["t"] - previous_ns, 1), 3))
                previous_ns = line["t"]
            self._density = density
        return self._density

    def rates(
        self,
        *,
        until_ns: Optional[int] = None,
        sample: Optional[int] = None,
        start: Optional[int] = None,
        span_s: Optional[float] = None,
    ) -> dict[str, Any]:
        """Rates from sample number ``start``, else from ``span_s`` seconds earlier, up to sample number
        ``sample``, else the last sample at or before ``until_ns``; the whole run by default. ``start``
        and ``sample`` in the result are the numbers of the span's first and end samples."""
        if len(self._lines) < 2:
            raise ValueError("the record holds fewer than two samples")
        times = self.times
        end = len(times) - 1
        if sample is not None:
            end = min(max(sample, 0), end)
        elif until_ns is not None:
            end = max((i for i, t in enumerate(times) if t <= until_ns), default=0)
        if start is not None:
            start = max(start, 0)
        elif span_s is None:
            start = 0
        else:
            start = max((i for i, t in enumerate(times) if t <= times[end] - span_s * NS_PER_S), default=0)
        if start >= end:
            start, end = (end - 1, end) if end > 0 else (0, 1)
        before, after = self.samples(start, end)
        return {**rates_between(before, after), "start": start, "sample": end}


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
