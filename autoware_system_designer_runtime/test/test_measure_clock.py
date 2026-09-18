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

"""The analysis clock: wall time, or ROS time interpolated through the recorded /clock overrides."""

import pytest

from autoware_system_designer_runtime._impl.measure.clock import RosClock, WallClock, clock_for
from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.node_stats import analyze
from autoware_system_designer_runtime._impl.measure.trace_reader import ClockSample, read_trace_dir

from .measure_fixtures import MS, T0, S, TraceBuilder, chain_design, write_chain_traces, write_clock_trace

R0 = 1_600_000_000 * S  # ROS time origin of the samples; zero is the unset value


def sample(t, ros):
    return ClockSample(t, 1, 1, 0xC, R0 + ros)


def test_wall_clock_is_the_identity():
    clock = WallClock()
    assert clock.at(T0 + 3) == T0 + 3
    assert clock.elapsed(T0, T0 + 5 * MS) == 5 * MS
    assert clock.as_dict() == {"base": "wall"}


def test_ros_clock_interpolates_between_samples_and_continues_past_them():
    clock = RosClock([sample(T0, 0), sample(T0 + 100 * MS, 50 * MS), sample(T0 + 200 * MS, 100 * MS)])
    assert clock.rate == 0.5 and clock.samples == 3
    assert clock.at(T0 + 50 * MS) == R0 + 25 * MS
    assert clock.elapsed(T0 + 20 * MS, T0 + 120 * MS) == 50 * MS
    assert clock.at(T0 - 100 * MS) == R0 - 50 * MS  # before the first sample, along the first segment
    assert clock.at(T0 + 300 * MS) == R0 + 150 * MS  # after the last, along the last segment
    assert clock.as_dict() == {"base": "ros", "rate": 0.5, "samples": 3}


def test_ros_clock_stands_still_while_the_bag_is_paused():
    clock = RosClock(
        [sample(T0, 0), sample(T0 + 10 * MS, 10 * MS), sample(T0 + 510 * MS, 20 * MS), sample(T0 + 520 * MS, 30 * MS)]
    )
    # Between 10 ms and 510 ms wall only 10 ms of ROS time passed: a pause of ~490 ms vanishes.
    assert clock.elapsed(T0 + 10 * MS, T0 + 510 * MS) == 10 * MS
    assert clock.elapsed(T0 + 510 * MS, T0 + 520 * MS) == 10 * MS


def test_ros_clock_merges_the_same_value_seen_by_several_processes():
    # Two processes saw ROS time 10 ms at slightly different wall instants; the earliest wins.
    clock = RosClock(
        [
            sample(T0, 0),
            sample(T0 + 10 * MS + 300, 10 * MS),
            sample(T0 + 10 * MS, 10 * MS),
            sample(T0 + 20 * MS, 20 * MS),
        ]
    )
    assert clock.samples == 3
    assert clock.at(T0 + 10 * MS) == R0 + 10 * MS


def test_ros_clock_needs_two_distinct_samples():
    with pytest.raises(ValueError):
        RosClock([sample(T0, 5), sample(T0 + MS, 5)])


def test_ros_clock_ignores_the_unset_zero_value():
    unset = ClockSample(T0 - 3 * S, 1, 1, 0xC, 0)
    clock = RosClock([unset, sample(T0, 10 * MS), sample(T0 + 10 * MS, 20 * MS)])
    assert clock.samples == 2 and clock.rate == 1.0
    assert clock.at(T0 - 3 * S) == R0 + 10 * MS - 3 * S


def test_clock_for_picks_ros_time_only_when_the_trace_has_overrides(tmp_path):
    start, end = write_chain_traces(tmp_path)
    assert isinstance(clock_for(read_trace_dir(tmp_path)), WallClock)
    with pytest.raises(ValueError):
        clock_for(read_trace_dir(tmp_path), "ros")
    write_clock_trace(tmp_path, start, end, rate=2.0)
    trace_set = read_trace_dir(tmp_path)
    assert isinstance(clock_for(trace_set), RosClock)
    assert isinstance(clock_for(trace_set, "wall"), WallClock)
    with pytest.raises(ValueError):
        clock_for(trace_set, "sim")


def test_analysis_durations_follow_the_ros_clock(tmp_path):
    start, end = write_chain_traces(tmp_path)
    write_clock_trace(tmp_path, start, end, rate=0.5)
    trace_set = read_trace_dir(tmp_path)
    graph = NodeGraph.from_system_structure(chain_design())

    analysis = analyze(trace_set, graph, start, end, clock=clock_for(trace_set))

    assert analysis.window_s == 0.5 and analysis.window_wall_s == 1.0
    assert {p.exec_ns for p in analysis.nodes["/a"].pubs_by_topic["/x"]} == {500_000}  # 1 ms wall
    assert {p.exec_ns for p in analysis.nodes["/b"].pubs_by_topic["/y"]} == {1 * MS}  # 2 ms wall
    comm = analysis.links[("/x", "/a", "/b")]
    assert all(v == 265_000 for v in comm)  # 0.53 ms wall
    assert analysis.rate(50) == 100.0
    # The match of a take to its publish stays a wall-time decision.
    assert all(t.source_pub is not None for t in analysis.nodes["/b"].takes_by_topic["/x"])
