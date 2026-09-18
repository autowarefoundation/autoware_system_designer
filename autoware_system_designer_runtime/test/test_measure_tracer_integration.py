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

"""The preloaded tracer records a talker/listener pair; skipped without the library or the demo nodes."""

import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoware_system_designer_runtime._impl.measure.clock import RosClock, clock_for
from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.node_stats import analyze
from autoware_system_designer_runtime._impl.measure.session import locate_tracer, tracer_env
from autoware_system_designer_runtime._impl.measure.trace_reader import FLAG_CLOCK_LAST, read_trace_dir

from .measure_fixtures import in_port, node, out_port, process, system


def _demo(executable: str):
    try:
        from ament_index_python.packages import get_package_prefix

        path = Path(get_package_prefix("demo_nodes_cpp")) / "lib" / "demo_nodes_cpp" / executable
    except Exception:  # noqa: BLE001
        return None
    return path if path.is_file() else None


def _tracer():
    try:
        return locate_tracer()
    except FileNotFoundError:
        return None


pytestmark = pytest.mark.skipif(
    _tracer() is None or _demo("talker") is None or _demo("listener") is None,
    reason="tracer library or demo_nodes_cpp not installed",
)


def _terminate(procs):
    for proc in procs:
        os.killpg(proc.pid, signal.SIGTERM)
    for proc in procs:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


# Publishes /clock at 50 Hz with ROS time running at half wall speed, frozen (as a
# paused bag keeps publishing) between 1.0 s and 2.5 s; untraced.
CLOCK_PUBLISHER = """
import time, rclpy
from rclpy.node import Node
from rosgraph_msgs.msg import Clock
rclpy.init()
node = Node("fake_clock")
pub = node.create_publisher(Clock, "/clock", 10)
t0 = time.time_ns()
pause_from, pause_to = t0 + 1_000_000_000, t0 + 2_500_000_000
while rclpy.ok():
    now = time.time_ns()
    active = min(now, pause_from) - t0 + max(now - pause_to, 0)
    ros_ns = active // 2 + 1_600_000_000_000_000_000
    msg = Clock()
    msg.clock.sec, msg.clock.nanosec = divmod(ros_ns, 1_000_000_000)
    pub.publish(msg)
    time.sleep(0.02)
"""


def test_talker_listener_are_traced_and_linked(tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    env = tracer_env(_tracer(), trace_dir)
    env["ROS_DOMAIN_ID"] = str(random.randint(150, 230))
    procs = [
        subprocess.Popen(
            [str(_demo(name))], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        for name in ("talker", "listener")
    ]
    t_start = time.time_ns()
    try:
        time.sleep(4.0)
    finally:
        _terminate(procs)
    t_end = time.time_ns()

    trace_set = read_trace_dir(trace_dir)
    assert len(trace_set.processes) == 2
    design = system(
        [
            node(
                "/talker",
                out_ports=[out_port("chatter", "/chatter", "t.out", ["t.tick"])],
                events=[process("tick", "t.tick", "periodic", [], ["t.out"], 1.0)],
            ),
            node(
                "/listener",
                in_ports=[in_port("chatter", "/chatter", "l.in")],
                events=[process("hear", "l.hear", "on_input", ["l.in"], [])],
            ),
        ]
    )
    graph = NodeGraph.from_system_structure(design)
    analysis = analyze(trace_set, graph, t_start, t_end)

    talker = analysis.nodes["/talker"]
    pubs = talker.pubs_by_topic["/chatter"]
    assert len(pubs) >= 2
    assert {p.trigger.kind for p in pubs} == {"timer"}
    assert all(p.exec_ns is not None and 0 < p.exec_ns < 100_000_000 for p in pubs)

    comm = analysis.links[("/chatter", "/talker", "/listener")]
    assert len(comm) >= 2
    assert all(0 < v < 1_000_000_000 for v in comm)


def test_a_node_on_sim_time_records_the_clock_overrides(tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    env = tracer_env(_tracer(), trace_dir)
    env["ROS_DOMAIN_ID"] = str(random.randint(150, 230))
    plain_env = {k: v for k, v in env.items() if k not in ("LD_PRELOAD", "ASD_TRACE_DIR")}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", CLOCK_PUBLISHER],
            env=plain_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ),
        subprocess.Popen(
            [str(_demo("listener")), "--ros-args", "-p", "use_sim_time:=true"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ),
    ]
    try:
        time.sleep(4.0)
    finally:
        _terminate(procs)

    trace_set = read_trace_dir(trace_dir)
    assert len(trace_set.processes) == 1
    samples = [s for s in trace_set.clock_samples() if s.ros_ns > 0]  # zero: the clock before its first message
    moving = [s for s in samples if not s.flags & FLAG_CLOCK_LAST]
    assert len(moving) >= 20
    assert len({s.ros_ns for s in moving}) == len(moving)  # one record per distinct ROS time
    clock = clock_for(trace_set)
    assert isinstance(clock, RosClock)
    # The frozen value is recorded once more at its last sighting; the curve is flat between.
    held = [s for s in samples if s.flags & FLAG_CLOCK_LAST]
    assert len(held) == 1
    first = min(s.t for s in moving if s.ros_ns == held[0].ros_ns)
    assert held[0].t - first > 1_000_000_000
    assert clock.elapsed(first + 100_000_000, held[0].t - 100_000_000) == 0
    assert abs(clock.elapsed(held[0].t + 200_000_000, held[0].t + 800_000_000) - 300_000_000) < 60_000_000
