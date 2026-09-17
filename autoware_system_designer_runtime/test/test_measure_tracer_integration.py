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
import time
from pathlib import Path

import pytest

from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.node_stats import analyze
from autoware_system_designer_runtime._impl.measure.session import locate_tracer, tracer_env
from autoware_system_designer_runtime._impl.measure.trace_reader import read_trace_dir

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


def test_talker_listener_are_traced_and_linked(tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    env = tracer_env(_tracer(), trace_dir)
    env["ROS_DOMAIN_ID"] = str(random.randint(150, 230))
    procs = [
        subprocess.Popen([str(_demo(name))], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for name in ("talker", "listener")
    ]
    t_start = time.time_ns()
    try:
        time.sleep(4.0)
    finally:
        for proc in procs:
            os.killpg(proc.pid, signal.SIGTERM)
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    t_end = time.time_ns()

    trace_set = read_trace_dir(trace_dir)
    assert len(trace_set.processes) == 2
    design = system(
        [
            node("/talker", out_ports=[out_port("chatter", "/chatter", "t.out", ["t.tick"])], events=[process("tick", "t.tick", "periodic", [], ["t.out"], 1.0)]),
            node("/listener", in_ports=[in_port("chatter", "/chatter", "l.in")], events=[process("hear", "l.hear", "on_input", ["l.in"], [])]),
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
