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

"""Runtime plumbing of the measurement: env at spawn, hook order, tracer env, output paths."""

import asyncio
import json
from pathlib import Path

from autoware_system_designer_runtime._impl.core import regular_actor
from autoware_system_designer_runtime._impl.core.config import ActorConfig
from autoware_system_designer_runtime._impl.core.coordinator import CoordinatorBuilder
from autoware_system_designer_runtime._impl.core.regular_actor import NodeSpec, RegularNodeActor
from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.session import (
    analyze_traces,
    default_latency_out,
    report_path_for,
    tracer_env,
)
from autoware_system_designer_runtime._impl.measure.writer import LATENCY_SCHEMA

from .measure_fixtures import chain_design, write_chain_traces


class _FakeProc:
    pid = 4321
    returncode = None

    async def wait(self):
        return 0


def test_node_spec_env_reaches_spawn(monkeypatch, tmp_path):
    captured = {}

    async def fake_spawn(cmd, *, env=None, stdout_path=None, stderr_path=None, cwd=None):
        captured["cmd"] = list(cmd)
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(regular_actor, "spawn_pgrp", fake_spawn)

    async def run():
        spec = NodeSpec(name="/n", cmd=["true"], env={"ASD_TRACE_DIR": "/t", "LD_PRELOAD": "/lib.so"})
        actor = RegularNodeActor(
            spec, ActorConfig(output_dir=tmp_path), asyncio.Queue(), asyncio.Queue(), asyncio.Event()
        )
        await actor._handle_pending()

    asyncio.run(run())
    assert captured["env"] == {"ASD_TRACE_DIR": "/t", "LD_PRELOAD": "/lib.so"}
    assert captured["cmd"] == ["true"]


def test_default_env_is_inherited(monkeypatch, tmp_path):
    captured = {}

    async def fake_spawn(cmd, *, env=None, stdout_path=None, stderr_path=None, cwd=None):
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(regular_actor, "spawn_pgrp", fake_spawn)

    async def run():
        actor = RegularNodeActor(
            NodeSpec(name="/n", cmd=["true"]),
            ActorConfig(output_dir=tmp_path),
            asyncio.Queue(),
            asyncio.Queue(),
            asyncio.Event(),
        )
        await actor._handle_pending()

    asyncio.run(run())
    assert captured["env"] is None


def test_hooks_run_pre_start_post_start_shutdown_in_order():
    order = []
    builder = CoordinatorBuilder()
    spec = NodeSpec(name="/n", cmd=["true"])

    async def pre(coord):
        order.append("pre")
        for s in coord.specs():
            s.env = {"X": "1"}

    async def post(coord):
        order.append("post")

    async def shutdown(coord):
        order.append("shutdown")

    builder.add_pre_start_hook(pre)
    builder.add_post_start_hook(post)
    builder.add_shutdown_hook(shutdown)
    # No members: run() returns at once, so the hook sequence is observable without spawning.
    coord = builder.build()
    assert asyncio.run(coord.run()) == 0
    assert order == ["pre", "post", "shutdown"]

    builder2 = CoordinatorBuilder()
    builder2.add_node(spec)
    builder2.add_pre_start_hook(pre)
    coord2 = builder2.build()
    assert coord2.specs() == [spec]


def test_tracer_env_prepends_preload_and_sets_trace_dir():
    env = tracer_env(Path("/opt/lib/libtracer.so"), Path("/tmp/trace"), {"LD_PRELOAD": "/other.so", "HOME": "/h"})
    assert env["LD_PRELOAD"] == "/opt/lib/libtracer.so:/other.so"
    assert env["ASD_TRACE_DIR"] == "/tmp/trace"
    assert env["HOME"] == "/h"
    again = tracer_env(Path("/opt/lib/libtracer.so"), Path("/tmp/trace"), env)
    assert again["LD_PRELOAD"] == env["LD_PRELOAD"]
    bare = tracer_env(Path("/opt/lib/libtracer.so"), Path("/tmp/trace"), {})
    assert bare["LD_PRELOAD"] == "/opt/lib/libtracer.so"


def test_default_latency_out_targets_the_export_bundle_or_the_log_dir(tmp_path):
    export = tmp_path / "exports" / "Auto"
    json_path = export / "system_structure" / "Runtime.json"
    json_path.parent.mkdir(parents=True)
    json_path.write_text("{}")

    # No visualization bundle yet: the log directory takes it.
    out = default_latency_out("Runtime", json_path, tmp_path / "logs")
    assert out == tmp_path / "logs" / "latency" / "Runtime_latency.json"

    (export / "visualization" / "web").mkdir(parents=True)
    out = default_latency_out("Runtime", json_path, tmp_path / "logs")
    assert out == export / "visualization" / "web" / "data" / "Runtime_latency.json"

    # A JSON outside an export tree falls back as well.
    loose = tmp_path / "Psim.json"
    loose.write_text("{}")
    assert default_latency_out("Psim", loose, tmp_path / "logs") == tmp_path / "logs" / "latency" / "Psim_latency.json"

    assert report_path_for(Path("/l/Runtime_latency.json")) == Path("/l/Runtime_measure_report.md")
    assert report_path_for(Path("/l/custom.json")) == Path("/l/custom_measure_report.md")


def test_analyze_traces_writes_file_and_report(tmp_path):
    traces = tmp_path / "trace"
    start, end = write_chain_traces(traces)
    graph = NodeGraph.from_system_structure(chain_design())
    latency_out = tmp_path / "latency" / "Test_latency.json"
    report_out = report_path_for(latency_out)

    data = analyze_traces(
        traces,
        graph,
        window_start_ns=start,
        window_end_ns=end,
        mode="Test",
        probe=True,
        latency_out=latency_out,
        report_out=report_out,
    )

    written = json.loads(latency_out.read_text())
    assert written["schema"] == LATENCY_SCHEMA == data["schema"]
    assert written["mode"] == "Test" and written["run"]["probe"] is True
    assert written["run"]["window_s"] == 1.0
    by_path = {n["node_path"]: n for n in written["nodes"]}
    assert "/cc" not in by_path  # a container has nothing to report
    a_out = by_path["/a"]["outputs"][0]
    assert a_out["topic"] == "/x" and a_out["trigger"]["kind"] == "timer" and a_out["exec"]["mean_ms"] == 1.0
    assert by_path["/a"]["timers"] == [{"period_ms": 20.0, "count": 50, "rate_hz": 50.0}]
    assert by_path["/d"]["inputs"][0] == {
        "topic": "/p",
        "count": 0,
        "rate_hz": 0.0,
        "intra_process": True,
        "duplicate_count": 20,
    }
    links = {(l["topic"], l.get("publisher"), l["subscriber"]): l for l in written["links"]}
    assert links[("/x", "/a", "/b")]["mean_ms"] == 0.53
    assert links[("/p", "/c", "/d")] == {"topic": "/p", "publisher": "/c", "subscriber": "/d", "intra_process": True}
    chains = {(c["from"], c["to"]): c for c in written["chains"]}
    assert chains[("/a:timer:20", "/b:/y")]["terminal"] is True
    report = report_out.read_text()
    assert report.startswith("# Measurement report: Test")
    assert "| /f | /w |" in report  # the unknown-trigger output is listed
    assert "/a:timer:20 | /b:/y" in report
