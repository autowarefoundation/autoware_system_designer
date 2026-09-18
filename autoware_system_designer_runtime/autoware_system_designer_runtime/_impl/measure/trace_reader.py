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

"""Reader of the tracer's ``<pid>.trace`` / ``<pid>.names`` files.

Mirrors ``autoware_system_designer_tracer/trace_format.h`` field for field: a
64-byte header, fixed 64-byte records, and a tab-separated name table joining
record handles to node names, topics, publisher gids and timer periods.
"""

from __future__ import annotations

import logging
import struct
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Union

logger = logging.getLogger(__name__)

TRACE_MAGIC = b"ASDTRACE"
TRACE_VERSION = 1
_HEADER = struct.Struct("<8sIIIIQQQ16s")
_RECORD = struct.Struct("<BBHIQQQ24sQ")
RECORD_SIZE = _RECORD.size

REC_TAKE = 1
REC_TIMER = 2
REC_PUBLISH = 3
REC_CLOCK = 4

FLAG_FROM_INTRA = 1
FLAG_SERIALIZED = 2
FLAG_LOANED = 4
FLAG_NO_INFO = 8


@dataclass(slots=True)
class Take:
    t: int
    tid: int
    pid: int
    handle: int
    source_ts: int
    gid: str  # hex
    flags: int
    seq: int


@dataclass(slots=True)
class TimerFire:
    t: int
    tid: int
    pid: int
    handle: int


@dataclass(slots=True)
class Publish:
    t_in: int
    t_out: int
    tid: int
    pid: int
    handle: int
    flags: int


@dataclass(slots=True)
class ClockSample:
    """A ROS time override: the process saw ROS time ``ros_ns`` at wall time ``t``."""

    t: int
    tid: int
    pid: int
    handle: int
    ros_ns: int


@dataclass(slots=True)
class Endpoint:
    pid: int
    kind: str  # pub | sub
    handle: int
    fqn: str
    topic: str
    gid: str  # hex, publishers only
    t_init: int


@dataclass(slots=True)
class TimerInfo:
    pid: int
    handle: int
    period_ns: int
    t_init: int


class _HandleTable:
    """Latest name-table entry for a handle at a given time; handles may be reused."""

    def __init__(self) -> None:
        self._entries: dict[int, list] = {}
        self._times: dict[int, list[int]] = {}

    def add(self, handle: int, entry, t_init: int) -> None:
        self._entries.setdefault(handle, []).append(entry)
        self._times.setdefault(handle, []).append(t_init)

    def resolve(self, handle: int, t: int):
        entries = self._entries.get(handle)
        if not entries:
            return None
        if len(entries) == 1:
            return entries[0]
        index = bisect_right(self._times[handle], t) - 1
        return entries[max(index, 0)]

    def values(self) -> Iterator:
        for entries in self._entries.values():
            yield from entries


@dataclass
class ProcessTrace:
    pid: int
    capacity: int
    claimed: int
    start_ns: int
    takes: list[Take] = field(default_factory=list)
    timers: list[TimerFire] = field(default_factory=list)
    publishes: list[Publish] = field(default_factory=list)
    clocks: list[ClockSample] = field(default_factory=list)
    endpoints: _HandleTable = field(default_factory=_HandleTable)
    timer_infos: _HandleTable = field(default_factory=_HandleTable)

    @property
    def dropped(self) -> int:
        return max(0, self.claimed - self.capacity)

    @property
    def record_count(self) -> int:
        return len(self.takes) + len(self.timers) + len(self.publishes) + len(self.clocks)

    def endpoint(self, handle: int, t: int) -> Optional[Endpoint]:
        return self.endpoints.resolve(handle, t)

    def timer(self, handle: int, t: int) -> Optional[TimerInfo]:
        return self.timer_infos.resolve(handle, t)

    def last_time(self) -> int:
        candidates = [self.start_ns]
        if self.takes:
            candidates.append(self.takes[-1].t)
        if self.timers:
            candidates.append(self.timers[-1].t)
        if self.publishes:
            candidates.append(self.publishes[-1].t_in)
        return max(candidates)


@dataclass
class TraceSet:
    processes: dict[int, ProcessTrace] = field(default_factory=dict)

    @property
    def publishers_by_gid(self) -> dict[str, Endpoint]:
        out: dict[str, Endpoint] = {}
        for proc in self.processes.values():
            for endpoint in proc.endpoints.values():
                if endpoint.kind == "pub" and endpoint.gid:
                    out[endpoint.gid] = endpoint
        return out

    def time_span(self) -> tuple[int, int]:
        """Earliest process start and latest record time, in ns."""
        if not self.processes:
            return 0, 0
        starts = [p.start_ns for p in self.processes.values()]
        ends = [p.last_time() for p in self.processes.values()]
        return min(starts), max(ends)

    def total_dropped(self) -> int:
        return sum(p.dropped for p in self.processes.values())

    def clock_samples(self) -> list[ClockSample]:
        """Every ROS time override of every process, ordered by wall time."""
        samples = [sample for proc in self.processes.values() for sample in proc.clocks]
        samples.sort(key=lambda s: s.t)
        return samples


def read_trace_file(path: Union[str, Path]) -> ProcessTrace:
    path = Path(path)
    data = path.read_bytes()
    if len(data) < _HEADER.size:
        raise ValueError(f"{path}: truncated header")
    magic, version, record_size, pid, header_size, capacity, count, start_ns, _ = _HEADER.unpack_from(data, 0)
    if magic != TRACE_MAGIC:
        raise ValueError(f"{path}: not a trace file")
    if version != TRACE_VERSION or record_size != RECORD_SIZE:
        raise ValueError(f"{path}: unsupported trace version {version} / record size {record_size}")
    proc = ProcessTrace(pid=pid, capacity=capacity, claimed=count, start_ns=start_ns)

    available = (len(data) - header_size) // record_size
    n = min(count, capacity, available)
    view = memoryview(data)[header_size : header_size + n * record_size]
    for kind, flags, _reserved, tid, t, t2, handle, gid, seq in _RECORD.iter_unpack(view):
        if kind == REC_TAKE:
            proc.takes.append(Take(t, tid, pid, handle, t2, gid.hex(), flags, seq))
        elif kind == REC_TIMER:
            proc.timers.append(TimerFire(t, tid, pid, handle))
        elif kind == REC_PUBLISH:
            proc.publishes.append(Publish(t, t2, tid, pid, handle, flags))
        elif kind == REC_CLOCK:
            proc.clocks.append(ClockSample(t, tid, pid, handle, t2))
    proc.takes.sort(key=lambda r: r.t)
    proc.timers.sort(key=lambda r: r.t)
    proc.publishes.sort(key=lambda r: r.t_in)
    proc.clocks.sort(key=lambda r: r.t)

    names_path = path.with_suffix(".names")
    if names_path.exists():
        _read_names(names_path, proc)
    return proc


def _read_names(path: Path, proc: ProcessTrace) -> None:
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 4:
                continue
            try:
                t_init = int(fields[0])
                obj = int(fields[2], 16)
            except ValueError:
                continue
            kind = fields[1]
            if kind in ("pub", "sub") and len(fields) >= 5:
                gid = fields[5] if len(fields) > 5 else ""
                proc.endpoints.add(obj, Endpoint(proc.pid, kind, obj, fields[3], fields[4], gid, t_init), t_init)
            elif kind == "timer":
                try:
                    period = int(fields[3])
                except ValueError:
                    continue
                proc.timer_infos.add(obj, TimerInfo(proc.pid, obj, period, t_init), t_init)


@dataclass(slots=True)
class TraceProgress:
    """Header counters of a trace directory, readable while the processes still write."""

    processes: int = 0
    records: int = 0
    dropped: int = 0

    def describe(self) -> str:
        text = f"{self.processes} traced processes, {self.records} records"
        if self.dropped:
            text += f" ({self.dropped} dropped)"
        return text


def trace_dir_progress(path: Union[str, Path]) -> TraceProgress:
    progress = TraceProgress()
    for file in Path(path).glob("*.trace"):
        try:
            with file.open("rb") as handle:
                data = handle.read(_HEADER.size)
        except OSError:
            continue
        if len(data) < _HEADER.size:
            continue
        magic, version, _record_size, _pid, _header_size, capacity, count, _start_ns, _ = _HEADER.unpack_from(data, 0)
        if magic != TRACE_MAGIC or version != TRACE_VERSION:
            continue
        progress.processes += 1
        progress.records += min(count, capacity)
        progress.dropped += max(count - capacity, 0)
    return progress


def read_trace_dir(path: Union[str, Path]) -> TraceSet:
    trace_set = TraceSet()
    for file in sorted(Path(path).glob("*.trace")):
        try:
            proc = read_trace_file(file)
        except ValueError as exc:
            logger.warning("skipping %s: %s", file, exc)
            continue
        if proc.record_count == 0 and not list(proc.endpoints.values()):
            continue
        trace_set.processes[proc.pid] = proc
    return trace_set
