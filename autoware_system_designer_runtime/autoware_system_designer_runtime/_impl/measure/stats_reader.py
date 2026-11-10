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

"""Reader of the tracer's ``<pid>.stats`` counters and the name table that labels their slots.

Mirrors ``asd_stats_header_t`` / ``asd_stats_slot_t`` of ``trace_format.h``. The
files are mapped read-only and read while the processes write them; a read is a
copy of the claimed slots, the name table is followed from where the last read
stopped.
"""

from __future__ import annotations

import logging
import mmap
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .trace_reader import Endpoint, TimerInfo, parse_name_line

logger = logging.getLogger(__name__)

STATS_MAGIC = b"ASDSTATS"
STATS_VERSION = 1
_HEADER = struct.Struct("<8sIIIIIIQ24s")
_SLOT = struct.Struct("<B7sQQQQQQQ")
SLOT_SIZE = _SLOT.size

SLOT_PUB = 1
SLOT_SUB = 2
SLOT_TIMER = 3


@dataclass(slots=True)
class SlotCounters:
    """One slot's cumulative counters at the time of a read."""

    kind: int
    handle: int
    count: int
    last_ns: int
    lat_sum_ns: int
    lat_count: int
    next_slot: Optional[int]  # slot of the latest publish this trigger led to


@dataclass
class ProcessCounters:
    pid: int
    start_ns: int
    capacity: int
    claimed: int
    slots: dict[int, SlotCounters] = field(default_factory=dict)

    @property
    def dropped(self) -> int:
        """Endpoints created past the slot capacity, without counters."""
        return max(0, self.claimed - self.capacity)


class ProcessStats:
    """One traced process: its mapped counters and its name table, read incrementally."""

    def __init__(self, stats_path: Path) -> None:
        self.path = Path(stats_path)
        self.pid = int(self.path.stem)
        self._names_path = self.path.with_suffix(".names")
        self._names_offset = 0
        self._names_partial = ""
        self.names: dict[int, Union[Endpoint, TimerInfo]] = {}
        with open(self.path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size < _HEADER.size:
                raise ValueError(f"{self.path}: truncated header")
            self._map = mmap.mmap(handle.fileno(), size, prot=mmap.PROT_READ)
        magic, version, slot_size, _pid, header_size, capacity, _claimed, start_ns, _ = _HEADER.unpack_from(self._map)
        if magic != STATS_MAGIC or version != STATS_VERSION or slot_size != SLOT_SIZE:
            self._map.close()
            raise ValueError(f"{self.path}: unsupported counters version {version} / slot size {slot_size}")
        self._header_size = header_size
        self._capacity = capacity
        self._start_ns = start_ns

    def close(self) -> None:
        self._map.close()

    def read(self) -> ProcessCounters:
        claimed = _HEADER.unpack_from(self._map)[6]
        used = min(claimed, self._capacity)
        counters = ProcessCounters(self.pid, self._start_ns, self._capacity, claimed)
        start = self._header_size
        data = self._map[start : start + used * SLOT_SIZE]
        for index, (kind, _pad, handle, count, last_ns, lat_sum, lat_count, next_slot, _) in enumerate(
            _SLOT.iter_unpack(data)
        ):
            if kind == 0:
                continue  # claimed, not yet published
            counters.slots[index] = SlotCounters(
                kind, handle, count, last_ns, lat_sum, lat_count, next_slot - 1 if next_slot else None
            )
        return counters

    def read_names(self) -> dict[int, Union[Endpoint, TimerInfo]]:
        """Slot → name-table entry, with the lines appended since the last call."""
        try:
            with open(self._names_path, encoding="utf-8", errors="replace") as handle:
                if os.fstat(handle.fileno()).st_size < self._names_offset:  # rewritten by a new process of the pid
                    self._names_offset, self._names_partial = 0, ""
                    self.names.clear()
                handle.seek(self._names_offset)
                text = handle.read()
                self._names_offset = handle.tell()
        except OSError:
            return self.names
        lines = (self._names_partial + text).split("\n")
        self._names_partial = lines.pop()
        for line in lines:
            entry = parse_name_line(line, self.pid)
            if entry is not None and entry.slot is not None:
                self.names[entry.slot] = entry
        return self.names


def _pid_alive(pid: int) -> bool:
    return os.path.exists(f"/proc/{pid}")


class StatsDir:
    """Every live traced process of a trace directory. A process that ended is handed once to
    ``on_end`` for its final counters, then dropped; one that started and ended between two
    refreshes is handed over too."""

    def __init__(self, trace_dir: Union[str, Path], *, alive=_pid_alive) -> None:
        self._dir = Path(trace_dir)
        self._alive = alive
        self._procs: dict[int, ProcessStats] = {}
        self._ended: set[int] = set()

    def refresh(self, on_end=None) -> dict[int, ProcessStats]:
        for path in self._dir.glob("*.stats"):
            try:
                pid = int(path.stem)
            except ValueError:
                continue
            if pid in self._procs or pid in self._ended:
                continue
            try:
                self._procs[pid] = ProcessStats(path)
            except (OSError, ValueError) as exc:
                logger.debug("counters of %s not readable yet: %s", path, exc)
        for pid in [pid for pid in self._procs if not self._alive(pid)]:
            proc = self._procs.pop(pid)
            self._ended.add(pid)
            if on_end is not None:
                on_end(proc)
            proc.close()
        return self._procs

    def close(self) -> None:
        for proc in self._procs.values():
            proc.close()
        self._procs.clear()
