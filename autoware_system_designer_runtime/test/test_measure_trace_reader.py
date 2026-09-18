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

"""The trace reader mirrors the tracer's on-disk layout and tolerates unfinished slots."""

import pytest

from autoware_system_designer_runtime._impl.measure.trace_reader import read_trace_dir, read_trace_file

from .measure_fixtures import MS, T0, TraceBuilder, gid


def test_records_and_names_round_trip(tmp_path):
    b = TraceBuilder(pid=11)
    b.pub(0x10, "/a", "/x", gid(1)).sub(0x20, "/b", "/x").timer(0x30, 20 * MS)
    b.fire(T0 + 1 * MS, tid=5, handle=0x30)
    b.publish(T0 + 2 * MS, T0 + 2 * MS + 100, tid=5, handle=0x10)
    b.take(T0 + 3 * MS, tid=6, handle=0x20, source_ts=T0 + 2 * MS + 50, gid=gid(1), flags=0, seq=7)
    path = b.write(tmp_path, unfinished=1)

    proc = read_trace_file(path)
    assert proc.pid == 11
    assert proc.claimed == 4 and proc.capacity == 4 and proc.dropped == 0
    assert [t.t for t in proc.timers] == [T0 + 1 * MS]
    assert proc.publishes[0].t_out - proc.publishes[0].t_in == 100
    take = proc.takes[0]
    assert take.source_ts == T0 + 2 * MS + 50 and take.gid == gid(1) and take.seq == 7
    assert proc.endpoint(0x10, T0 + 2 * MS).topic == "/x"
    assert proc.endpoint(0x20, T0).fqn == "/b"
    assert proc.timer(0x30, T0).period_ns == 20 * MS
    assert proc.record_count == 3  # the unfinished slot is skipped


def test_dropped_records_are_counted_from_the_header(tmp_path):
    b = TraceBuilder(pid=12)
    for i in range(3):
        b.fire(T0 + i * MS, tid=1, handle=0x1)
    b.write(tmp_path, capacity=2)
    b.records = b.records[:2]
    path = b.write(tmp_path, capacity=2)
    # count in header is 2 (records) + 0; emulate an overflowed header by hand
    data = bytearray(path.read_bytes())
    data[32:40] = (7).to_bytes(8, "little")  # count
    path.write_bytes(bytes(data))

    proc = read_trace_file(path)
    assert proc.capacity == 2 and proc.claimed == 7 and proc.dropped == 5
    assert len(proc.timers) == 2


def test_handle_reuse_resolves_by_time(tmp_path):
    b = TraceBuilder(pid=13)
    b.pub(0x10, "/a", "/x", gid(1), t=T0).pub(0x10, "/a", "/z", gid(2), t=T0 + 10 * MS)
    b.publish(T0 + 1 * MS, T0 + 1 * MS, tid=1, handle=0x10)
    b.publish(T0 + 11 * MS, T0 + 11 * MS, tid=1, handle=0x10)
    path = b.write(tmp_path)

    proc = read_trace_file(path)
    assert proc.endpoint(0x10, T0 + 1 * MS).topic == "/x"
    assert proc.endpoint(0x10, T0 + 11 * MS).topic == "/z"


def test_directory_reader_skips_foreign_files(tmp_path):
    TraceBuilder(pid=21).pub(0x1, "/a", "/x", gid(1)).publish(T0, T0, 1, 0x1).write(tmp_path)
    (tmp_path / "junk.trace").write_bytes(b"not a trace file at all, definitely not")

    trace_set = read_trace_dir(tmp_path)
    assert list(trace_set.processes) == [21]
    assert trace_set.publishers_by_gid[gid(1)].fqn == "/a"
    assert trace_set.time_span() == (T0, T0)


def test_bad_magic_is_rejected(tmp_path):
    path = tmp_path / "1.trace"
    path.write_bytes(b"X" * 64)
    with pytest.raises(ValueError):
        read_trace_file(path)


def test_clock_records_are_read_and_merged_by_wall_time(tmp_path):
    TraceBuilder(pid=31).clock(T0 + 2 * MS, 5_000).clock(T0 + 0 * MS, 4_000).write(tmp_path)
    TraceBuilder(pid=32).clock(T0 + 1 * MS, 4_000, tid=3, handle=0xC2).write(tmp_path)

    trace_set = read_trace_dir(tmp_path)
    proc = trace_set.processes[31]
    assert [(s.t, s.ros_ns) for s in proc.clocks] == [(T0, 4_000), (T0 + 2 * MS, 5_000)]
    assert proc.record_count == 2
    merged = trace_set.clock_samples()
    assert [(s.pid, s.ros_ns) for s in merged] == [(31, 4_000), (32, 4_000), (31, 5_000)]
    assert merged[1].handle == 0xC2
