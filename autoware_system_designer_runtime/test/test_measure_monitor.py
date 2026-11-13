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

from autoware_system_designer_runtime._impl.measure.monitor import RateMonitor, RatesRecorder
from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.stats_reader import SLOT_PUB, SLOT_SUB, SLOT_TIMER
from autoware_system_designer_runtime._impl.measure.trace_reader import read_trace_dir
from autoware_system_designer_runtime._impl.measure.tracer import TraceControl
from autoware_system_designer_runtime.rates_record import RATES_FILE, RatesRecord, RatesWriter, Sample, format_rates

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

    def reuse(self, slot: int, kind: str, handle: int, fqn: str, topic: str) -> None:
        """Hands the slot of a finalized endpoint to a new one, counters kept, the way the tracer does."""
        _kind, _handle, count, last_ns, lat_sum, lat_count, _next = self.slots[slot]
        self.slots[slot] = (SLOT_PUB if kind == "pub" else SLOT_SUB, handle, count, last_ns, lat_sum, lat_count, 0)
        self._flush()
        with open(self.names, "a") as f:
            f.write(f"2\t{kind}\t{handle:x}\t{fqn}\t{topic}\t\t{slot}\t{count}\t{lat_sum}\t{lat_count}\t{last_ns}\n")

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


def test_new_slots_count_from_zero_and_silent_endpoints_say_since_when(tmp_path):
    proc = _Proc(tmp_path, 100)
    old = proc.endpoint("pub", 0x20, "/a", "/x")
    proc.set(old, count=5, last_ns=7 * S)
    monitor = _monitor(tmp_path)
    before = monitor.read()
    fresh = proc.endpoint("pub", 0x21, "/a", "/y")
    proc.set(fresh, count=3, last_ns=9 * S)
    timer = proc.timer(0x30, 50_000_000)
    after = monitor.read()
    before.t_ns, after.t_ns = 0, 10 * S
    snapshot = monitor.rates(before, after)

    node = _node(snapshot, "/a")
    assert node["outputs"] == [{"topic": "/x", "hz": 0.0, "silent_s": 3.0}, {"topic": "/y", "hz": 0.3}]
    # A timer that never led to a publish belongs to its process.
    assert _node(snapshot, "(process 100)")["timers"] == [{"period_ms": 50.0, "hz": 0.0}]
    assert timer == 2


def test_a_timer_moves_to_its_node_with_the_fires_since_and_keeps_a_row_of_its_own(tmp_path):
    proc = _Proc(tmp_path, 100)
    late = proc.timer(0x10, 100_000_000)
    idle = proc.timer(0x11, 100_000_000)
    pub = proc.endpoint("pub", 0x20, "/ns/talker", "/chatter")
    monitor = _monitor(tmp_path)
    proc.set(late, count=6000)
    proc.set(idle, count=10)
    first = monitor.read()
    assert first.totals[("(process 100)", "timer", (100_000_000, 0))][0] == 6000
    assert first.totals[("(process 100)", "timer", (100_000_000, 1))][0] == 10

    # The first publish of the timer: the node's key starts at the fires since the last reading.
    proc.set(late, count=6020, next_slot=pub)
    proc.set(idle, count=30)
    second = monitor.read()
    second.t_ns = first.t_ns + 2 * S
    assert ("(process 100)", "timer", (100_000_000, 0)) not in second.totals
    assert second.totals[("/ns/talker", "timer", (100_000_000, 0))][0] == 20
    snapshot = monitor.rates(first, second)
    assert _node(snapshot, "/ns/talker")["timers"] == [{"period_ms": 100.0, "hz": 10.0}]
    assert _node(snapshot, "(process 100)")["timers"] == [{"period_ms": 100.0, "hz": 10.0}]

    # A later publish of another node on the thread does not move the timer.
    other = proc.endpoint("pub", 0x21, "/ns/other", "/x")
    proc.set(late, count=6040, next_slot=other)
    third = monitor.read()
    assert third.totals[("/ns/talker", "timer", (100_000_000, 0))][0] == 40
    assert not any(key[0] == "/ns/other" and key[1] == "timer" for key in third.totals)


def test_a_reused_slot_keeps_the_counts_of_the_endpoint_that_ended(tmp_path):
    proc = _Proc(tmp_path, 100)
    slot = proc.endpoint("sub", 0x20, "/a", "/x")
    proc.set(slot, count=5, last_ns=3 * S, lat_sum=50, lat_count=5)
    monitor = _monitor(tmp_path)
    before = monitor.read()
    # Two more takes, then the subscription ends; two endpoints follow it in the slot between readings.
    proc.set(slot, count=7, last_ns=4 * S, lat_sum=70, lat_count=7)
    proc.reuse(slot, "sub", 0x21, "/a", "/x")
    proc.set(slot, count=8, last_ns=5 * S, lat_sum=80, lat_count=8)
    proc.reuse(slot, "pub", 0x22, "/b", "/y")
    proc.set(slot, count=11, last_ns=6 * S, lat_sum=80, lat_count=8)
    after = monitor.read()
    assert after.totals[("/a", "in", "/x")] == (8, 80, 8, 5 * S)
    assert after.totals[("/b", "out", "/y")] == (3, 0, 0, 6 * S)
    after.t_ns = before.t_ns + S
    assert _node(monitor.rates(before, after), "/a")["inputs"] == [{"topic": "/x", "hz": 3.0, "comm_ms": 0.0}]


def test_counters_with_an_unfinished_header_are_read_once_it_is_complete(tmp_path):
    proc = _Proc(tmp_path, 100)
    proc.set(proc.endpoint("pub", 0x20, "/a", "/x"), count=4)
    complete = proc.stats.read_bytes()
    proc.stats.write_bytes(_HEADER.pack(b"ASDSTATS", 1, 64, 100, 0, 0, 0, 0, b"") + complete[64:])
    monitor = _monitor(tmp_path)
    assert monitor.read().totals == {}
    proc.stats.write_bytes(complete)
    assert monitor.read().totals[("/a", "out", "/x")][0] == 4


def test_nodes_are_keyed_by_design_path_and_ended_processes_keep_their_counts(tmp_path):
    graph = NodeGraph.from_system_structure(
        system([node("/sensing/lidar/driver", out_ports=[out_port("points", "/points", "d.out", [])])])
    )
    alive = {100: True, 200: True, 300: True}
    first = _Proc(tmp_path, 100)
    pub = first.endpoint("pub", 0x20, "/sensing/lidar/driver", "/points")
    second = _Proc(tmp_path, 200, capacity=1)
    other = second.endpoint("pub", 0x20, "/other", "/o")
    assert second.endpoint("pub", 0x21, "/other", "/p") == -1  # past capacity
    monitor = _monitor(tmp_path, graph, alive=lambda pid: alive.get(pid, True))
    before = monitor.read()
    first.set(pub, count=4)
    second.set(other, count=2)
    after = monitor.read()
    after.t_ns = before.t_ns + S
    snapshot = monitor.rates(before, after)
    assert [n["node"] for n in snapshot["nodes"]] == ["/other", "/sensing/lidar/driver"]
    assert snapshot["untracked_endpoints"] == 1

    # The process ends after two more publishes; a respawn of the node continues its totals.
    second.set(other, count=4)
    alive[200] = False
    third = _Proc(tmp_path, 300)
    respawned = third.endpoint("pub", 0x50, "/other", "/o")
    third.set(respawned, count=3)
    later = monitor.read()
    later.t_ns = after.t_ns + S
    assert later.totals[("/other", "out", "/o")][0] == 7
    assert _node(monitor.rates(after, later), "/other")["outputs"] == [{"topic": "/o", "hz": 5.0}]
    assert later.untracked == 1
    monitor.close()


def test_a_process_that_started_and_ended_between_readings_is_counted(tmp_path):
    monitor = _monitor(tmp_path, alive=lambda pid: pid != 400)
    before = monitor.read()
    short = _Proc(tmp_path, 400)
    short.set(short.endpoint("pub", 0x20, "/once", "/x"), count=6)
    assert monitor.read().totals[("/once", "out", "/x")][0] == 6


def test_the_run_record_holds_changed_counters_and_reads_back_any_span(tmp_path):
    proc = _Proc(tmp_path / "trace", 100)
    pub = proc.endpoint("pub", 0x20, "/a", "/x")
    quiet = proc.endpoint("pub", 0x21, "/a", "/q")
    proc.set(quiet, count=1, last_ns=1)
    monitor = _monitor(tmp_path / "trace")
    recorder = RatesRecorder(monitor, tmp_path / RATES_FILE, interval_s=10)
    clock = iter(range(0, 100 * S, 10 * S))
    real_read = monitor.read

    def read():
        sample = real_read()
        sample.t_ns = next(clock)
        return sample

    monitor.read = read
    for count in (0, 100, 300, 600):  # 10, 20, 30 Hz over the three spans
        proc.set(pub, count=count, last_ns=count)
        proc.set(quiet, count=1, last_ns=1)
        recorder.sample()
    recorder.close()  # the final sample, nothing moved
    with open(tmp_path / RATES_FILE, "a") as torn:
        torn.write('{"t": 99, "c": [[0, ')

    lines = (tmp_path / RATES_FILE).read_text().splitlines()
    assert '"schema":"autoware_system_designer/rates/1"' in lines[0]
    assert '"keys"' in lines[1] and '"keys"' not in lines[2]
    assert '"c":[[0,100,0,0,100]]' in lines[2]  # the quiet key did not change

    record = RatesRecord.load(tmp_path / RATES_FILE)
    assert record.times == [0, 10 * S, 20 * S, 30 * S, 40 * S]
    whole = record.rates()
    assert whole["interval_s"] == 40.0
    assert _node(whole, "/a")["outputs"] == [{"topic": "/q", "hz": 0.0, "silent_s": 40.0}, {"topic": "/x", "hz": 15.0}]
    span = record.rates(until_ns=30 * S, span_s=10)
    assert span["from_ns"] == 20 * S and _node(span, "/a")["outputs"][1] == {"topic": "/x", "hz": 30.0}
    assert span["sample"] == 3 and record.rates(sample=3, span_s=10) == span
    assert record.rates(sample=99)["sample"] == 4
    between = record.rates(start=1, sample=2)
    assert (between["start"], between["sample"]) == (1, 2) and between["from_ns"] == 10 * S
    assert _node(between, "/a")["outputs"][1] == {"topic": "/x", "hz": 20.0}
    assert record.rates(start=3, sample=1)["start"] == 0
    assert record.density() == [0.0, 10.0, 20.0, 30.0, 0.0]


def test_the_run_record_drops_a_key_the_samples_no_longer_hold(tmp_path):
    writer = RatesWriter(tmp_path / RATES_FILE, interval_s=10)
    waiting = ("(process 1)", "timer", (100_000_000, 0))
    owned = ("/a", "timer", (100_000_000, 0))
    writer.write(Sample(0, {waiting: (5, 0, 0, 1)}))
    writer.write(Sample(10 * S, {owned: (100, 0, 0, 2)}))
    writer.write(Sample(20 * S, {owned: (200, 0, 0, 3)}))
    writer.close()
    record = RatesRecord.load(tmp_path / RATES_FILE)
    first, last = record.samples(0, 2)
    assert list(first.totals) == [waiting] and list(last.totals) == [owned]
    assert [n["node"] for n in record.rates()["nodes"]] == ["/a"]
    assert _node(record.rates(span_s=10), "/a")["timers"] == [{"period_ms": 100.0, "hz": 10.0}]


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
