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

"""Live rates from counter files written the way the tracer lays them out."""

import struct
from pathlib import Path

from autoware_system_designer_runtime._impl.measure.monitor import RateMonitor, format_rates
from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.stats_reader import SLOT_PUB, SLOT_SUB, SLOT_TIMER
from autoware_system_designer_runtime._impl.measure.trace_reader import read_trace_dir
from autoware_system_designer_runtime._impl.measure.tracer import TraceControl

from .measure_fixtures import node, out_port, system

_HEADER = struct.Struct("<8sIIIIIIQ24s")
_SLOT = struct.Struct("<B7sQQQQQQQ")
S = 1_000_000_000


class _Proc:
    """A <pid>.stats / <pid>.names pair; slots are rewritten in place like the mapped file."""

    def __init__(self, trace_dir: Path, pid: int, capacity: int = 8) -> None:
        self.pid = pid
        self.capacity = capacity
        self.stats = trace_dir / f"{pid}.stats"
        self.names = trace_dir / f"{pid}.names"
        self.slots: list[tuple] = []
        self.claimed = 0
        trace_dir.mkdir(parents=True, exist_ok=True)
        self.names.write_text(f"# asd-names 1 pid={pid}\n")
        self.stats.write_bytes(b"\0" * (64 + capacity * 64))
        self._flush()

    def endpoint(self, kind: str, handle: int, fqn: str, topic: str) -> int:
        slot = self._claim(SLOT_PUB if kind == "pub" else SLOT_SUB, handle)
        with open(self.names, "a") as f:
            f.write(f"1\t{kind}\t{handle:x}\t{fqn}\t{topic}\t\t{slot}\n")
        return slot

    def timer(self, handle: int, period_ns: int) -> int:
        slot = self._claim(SLOT_TIMER, handle)
        with open(self.names, "a") as f:
            f.write(f"1\ttimer\t{handle:x}\t{period_ns}\t{slot}\n")
        return slot

    def set(self, slot: int, *, count: int, last_ns: int = 0, lat_sum: int = 0, lat_count: int = 0, next_slot=None):
        kind, handle = self.slots[slot][:2]
        self.slots[slot] = (kind, handle, count, last_ns, lat_sum, lat_count, 0 if next_slot is None else next_slot + 1)
        self._flush()

    def _claim(self, kind: int, handle: int) -> int:
        slot = self.claimed
        self.claimed += 1
        if slot < self.capacity:
            self.slots.append((kind, handle, 0, 0, 0, 0, 0))
        self._flush()
        return slot if slot < self.capacity else -1

    def _flush(self) -> None:
        header = _HEADER.pack(b"ASDSTATS", 1, 64, self.pid, 64, self.capacity, self.claimed, 1, b"")
        body = b"".join(_SLOT.pack(k, b"", h, c, last, ls, lc, nxt, 0) for k, h, c, last, ls, lc, nxt in self.slots)
        with open(self.stats, "r+b") as f:
            f.write(header + body)


def _monitor(trace_dir, graph=None, alive=None):
    return RateMonitor(trace_dir, graph, alive=alive or (lambda pid: True))


def _node(snapshot, key):
    return next(n for n in snapshot["nodes"] if n["node"] == key)


def test_rates_and_means_come_from_the_difference_of_two_readings(tmp_path):
    proc = _Proc(tmp_path, 100)
    timer = proc.timer(0x10, 100_000_000)
    pub = proc.endpoint("pub", 0x20, "/ns/talker", "/chatter")
    sub = proc.endpoint("sub", 0x30, "/ns/listener", "/chatter")
    proc.endpoint("pub", 0x40, "/ns/talker", "/rosout")
    monitor = _monitor(tmp_path)

    proc.set(timer, count=10, next_slot=pub)
    proc.set(pub, count=10, lat_sum=10_000, lat_count=10)
    proc.set(sub, count=10, lat_sum=1_000_000, lat_count=10)
    before = monitor.read()
    proc.set(timer, count=30, next_slot=pub)
    proc.set(pub, count=30, lat_sum=50_000, lat_count=30)
    proc.set(sub, count=30, lat_sum=5_000_000, lat_count=30)
    after = monitor.read()
    after.t_ns = before.t_ns + 2 * S
    snapshot = monitor.rates(before, after)

    assert snapshot["interval_s"] == 2.0 and snapshot["processes"] == 1
    talker = _node(snapshot, "/ns/talker")
    assert talker["outputs"] == [{"topic": "/chatter", "hz": 10.0, "callback_ms": 0.002}]
    assert talker["timers"] == [{"period_ms": 100.0, "hz": 10.0}]  # attributed through the publish it led to
    assert _node(snapshot, "/ns/listener")["inputs"] == [{"topic": "/chatter", "hz": 10.0, "comm_ms": 0.2}]
    lines = format_rates(snapshot, "talker")
    assert lines[1] == "/ns/talker" and "10.00 Hz" in lines[2] and not any("listener" in line for line in lines)


def test_new_slots_have_no_rate_and_silent_endpoints_say_since_when(tmp_path):
    proc = _Proc(tmp_path, 100)
    old = proc.endpoint("pub", 0x20, "/a", "/x")
    proc.set(old, count=5, last_ns=7 * S)
    monitor = _monitor(tmp_path)
    before = monitor.read()
    fresh = proc.endpoint("pub", 0x21, "/a", "/y")
    proc.set(fresh, count=3, last_ns=9 * S)
    timer = proc.timer(0x30, 50_000_000)
    after = monitor.read()
    after.t_ns = 10 * S
    snapshot = monitor.rates(before, after)

    node = _node(snapshot, "/a")
    assert node["outputs"] == [{"topic": "/x", "hz": 0.0, "silent_s": 3.0}, {"topic": "/y", "hz": None}]
    # A timer that never led to a publish belongs to its process.
    assert _node(snapshot, "(process 100)")["timers"] == [{"period_ms": 50.0, "hz": None}]
    assert timer == 2


def test_nodes_are_keyed_by_design_path_and_ended_processes_drop_out(tmp_path):
    graph = NodeGraph.from_system_structure(
        system([node("/sensing/lidar/driver", out_ports=[out_port("points", "/points", "d.out", [])])])
    )
    alive = {100: True, 200: True}
    first = _Proc(tmp_path, 100)
    pub = first.endpoint("pub", 0x20, "/sensing/lidar/driver", "/points")
    second = _Proc(tmp_path, 200, capacity=1)
    second.endpoint("pub", 0x20, "/other", "/o")
    assert second.endpoint("pub", 0x21, "/other", "/p") == -1  # past capacity
    monitor = _monitor(tmp_path, graph, alive=lambda pid: alive[pid])
    before = monitor.read()
    first.set(pub, count=4)
    after = monitor.read()
    after.t_ns = before.t_ns + S
    snapshot = monitor.rates(before, after)
    assert [n["node"] for n in snapshot["nodes"]] == ["/other", "/sensing/lidar/driver"]
    assert snapshot["untracked_endpoints"] == 1

    alive[200] = False
    assert [n["node"] for n in monitor.rates(after, monitor.read())["nodes"]] == ["/sensing/lidar/driver"]
    monitor.close()


def test_a_process_without_events_is_still_read_from_its_name_table(tmp_path):
    proc = _Proc(tmp_path, 100)
    proc.endpoint("pub", 0x20, "/a", "/x")
    trace_set = read_trace_dir(tmp_path)
    (only,) = trace_set.processes.values()
    assert only.pid == 100 and only.record_count == 0
    (endpoint,) = only.endpoints.values()
    assert (endpoint.fqn, endpoint.topic, endpoint.slot) == ("/a", "/x", 0)


def test_the_control_file_switches_events_in_place(tmp_path):
    control = TraceControl(tmp_path)
    control.create()
    inode = control.path.stat().st_ino
    assert not control.events()
    control.set_events(True)
    assert control.events() and control.path.stat().st_ino == inode  # mapped by every traced process
    control.set_events(False)
    assert not control.events()
