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

"""Runtime plumbing of the measurement: env at spawn, hook order, tracer env, output paths, session lifecycle."""

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest

from autoware_system_designer_runtime import records
from autoware_system_designer_runtime._impl.core import events as ev
from autoware_system_designer_runtime._impl.core import regular_actor
from autoware_system_designer_runtime._impl.core.config import ActorConfig
from autoware_system_designer_runtime._impl.core.coordinator import CoordinatorBuilder
from autoware_system_designer_runtime._impl.core.regular_actor import NodeSpec, RegularNodeActor
from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.node_stats import ProcessExit
from autoware_system_designer_runtime._impl.measure.session import (
    MAX_WINDOW_S,
    MeasureOptions,
    MeasureSession,
    analyze_traces,
    default_latency_out,
    tracer_env,
)
from autoware_system_designer_runtime._impl.measure.trace_reader import read_trace_dir, trace_dir_progress
from autoware_system_designer_runtime._impl.measure.tracer import TraceControl, TracerArming
from autoware_system_designer_runtime._impl.measure.writer import LATENCY_SCHEMA

from .measure_fixtures import MS, US, S, chain_design, write_chain_traces, write_clock_trace

pytestmark = pytest.mark.usefixtures("fake_process_groups")


def _read_latency(path: Path) -> dict:
    """The object of a latency file in either shape: bare JSON or the bundle's script."""
    text = path.read_text()
    _, assign, body = text.partition("] = ")
    return json.loads(body.rstrip().rstrip(";")) if assign else json.loads(text)


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


def test_default_latency_out_is_a_new_record_of_the_export_or_under_the_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOWARE_SYSTEM_DESIGNER_RECORD_DIR", str(tmp_path / "records"))
    export = tmp_path / "install" / "pkg" / "share" / "pkg" / "exports" / "Auto"
    json_path = export / "system_structure" / "Runtime.json"
    json_path.parent.mkdir(parents=True)
    json_path.write_text("{}")

    # Without a manifest naming the package, the log directory takes it.
    out = default_latency_out("Runtime", json_path, tmp_path / "logs")
    assert out == tmp_path / "logs" / "latency" / "Runtime_latency.json"

    (export / "deployment.json").write_text(json.dumps({"deployment_package_name": "pkg"}))
    out = default_latency_out("Runtime", json_path, tmp_path / "logs", stamp="20261105-120000")
    assert out == tmp_path / "records" / "pkg" / "Auto" / "latency" / "Runtime" / "20261105-120000.json"

    # A JSON outside an export tree falls back as well.
    loose = tmp_path / "Psim.json"
    loose.write_text("{}")
    assert default_latency_out("Psim", loose, tmp_path / "logs") == tmp_path / "logs" / "latency" / "Psim_latency.json"


def test_record_root_defaults_under_ros_home(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOWARE_SYSTEM_DESIGNER_RECORD_DIR", raising=False)
    monkeypatch.setenv("ROS_HOME", str(tmp_path / "ros"))
    assert records.record_root() == tmp_path / "ros" / "autoware_system_designer" / "records"


def test_analyze_traces_writes_the_latency_file(tmp_path):
    traces = tmp_path / "trace"
    start, end = write_chain_traces(traces)
    graph = NodeGraph.from_system_structure(chain_design())
    latency_out = tmp_path / "latency" / "Test_latency.json"

    data = analyze_traces(
        traces,
        graph,
        window_start_ns=start,
        window_end_ns=end,
        mode="Test",
        probe=True,
        latency_out=latency_out,
    )
    assert list(latency_out.parent.iterdir()) == [latency_out]  # the file is the whole result

    written = json.loads(latency_out.read_text())
    assert written["schema"] == LATENCY_SCHEMA == data["schema"]
    assert written["mode"] == "Test" and written["run"]["probe"] is True
    assert written["run"]["window_s"] == 1.0
    assert written["run"]["clock"] == {"base": "wall"}
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
    # Run-level judgement without walking the records.
    summary = written["summary"]
    assert summary["design_nodes"] == len(graph.nodes)
    assert summary["observed_nodes"] == len(graph.nodes) - summary["unobserved_nodes"]
    assert written["unobserved_nodes"] == ["/cc"]  # the fixture registers no endpoint for the container
    assert summary["nodes_not_in_design"] == len(written["unmatched_nodes"])
    assert sum(summary["outputs_by_status"].values()) == sum(len(n["declared_diff"]) for n in written["nodes"])
    f_out = by_path["/f"]["outputs"][0]
    assert f_out["topic"] == "/w" and f_out["trigger"]["kind"] == "unknown"  # approximation stays visible
    # What became of each node's process is on the record, and counted.
    assert by_path["/b"]["process"] == {
        "pids": [200],
        "state": "running",
        "last_record": by_path["/b"]["process"]["last_record"],
    }
    assert by_path["/b"]["process"]["last_record"].startswith("2023-11-14T22:13:20")
    assert summary["nodes_exited"] == 0 and summary["nodes_not_initialized"] == 0


def test_a_process_exit_reported_by_the_runtime_reaches_the_record(tmp_path):
    traces = tmp_path / "trace"
    start, end = write_chain_traces(traces)
    graph = NodeGraph.from_system_structure(chain_design())
    exits = {200: ProcessExit(pid=200, t_ns=start + 300 * MS, exit_code=-6, actor="b_actor")}
    data = analyze_traces(
        traces,
        graph,
        window_start_ns=start,
        window_end_ns=end,
        mode="Test",
        probe=True,
        latency_out=tmp_path / "Test_latency.json",
        exits=exits,
    )
    b = next(n for n in data["nodes"] if n["node_path"] == "/b")
    assert b["process"]["state"] == "exited"
    assert b["process"]["exit"] == {"at": b["process"]["exit"]["at"], "code": -6, "actor": "b_actor"}
    assert data["summary"]["nodes_exited"] == 1


def test_session_keeps_actor_exits_by_pid(tmp_path, monkeypatch):
    session, _worker = _session(tmp_path, monkeypatch)
    builder = CoordinatorBuilder()
    session.install(builder)
    assert builder._state_hooks == [session.on_state_event]
    session.on_state_event(ev.Started(name="/b", pid=200))
    session.on_state_event(ev.Exited(name="/unknown", exit_code=0))
    session.on_state_event(ev.Exited(name="/b", exit_code=-6))
    exits = session.exits
    assert list(exits) == [200] and exits[200].exit_code == -6 and exits[200].actor == "/b"


def test_coordinator_state_hooks_see_actor_events(monkeypatch, tmp_path):
    seen = []

    class _ExitingProc(_FakeProc):
        pid = 4242

    async def fake_spawn(cmd, *, env=None, stdout_path=None, stderr_path=None, cwd=None):
        return _ExitingProc()

    monkeypatch.setattr(regular_actor, "spawn_pgrp", fake_spawn)
    builder = CoordinatorBuilder(default_config=ActorConfig(output_dir=tmp_path, respawn_enabled=False))
    builder.add_node(NodeSpec(name="/n", cmd=["true"]))

    # A stopped actor waits for a shutdown; the exit is what ends the run here.
    def hook(event):
        seen.append(event)
        if isinstance(event, ev.Exited):
            coord.request_shutdown()

    builder.add_state_hook(hook)
    coord = builder.build()
    asyncio.run(asyncio.wait_for(coord.run(), timeout=10))
    kinds = [(type(e).__name__, getattr(e, "pid", None), getattr(e, "exit_code", None)) for e in seen]
    assert ("Started", 4242, None) in kinds and ("Exited", None, 0) in kinds


class _StubCoord:
    """The coordinator surface the session touches: readiness, shutdown, task tracking."""

    def __init__(self):
        self.launch_ready = asyncio.Event()
        self.shutdown_event = asyncio.Event()
        self.shutdown_requests = 0
        self.tasks = []

    def schedule_task(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    def request_shutdown(self):
        self.shutdown_requests += 1
        self.shutdown_event.set()


class _StubWorker:
    def __init__(self):
        self.probes = []
        self.removed = 0

    async def add_probe(self, topic, msg_type):
        self.probes.append(topic)

    async def remove_probes(self):
        self.removed += 1


def _session(tmp_path, monkeypatch, **options):
    tracer = tmp_path / "libtracer.so"
    tracer.write_bytes(b"")
    monkeypatch.setenv("ASD_TRACER_LIB", str(tracer))
    write_chain_traces(tmp_path / "trace")
    graph = NodeGraph.from_system_structure(chain_design())
    worker = _StubWorker()
    arming = TracerArming(tracer, tmp_path / "trace")
    arming.control.create()
    session = MeasureSession(
        graph,
        worker,
        MeasureOptions(**options),
        arming=arming,
        latency_out=tmp_path / "out" / "Test_latency.json",
        mode="Test",
    )
    return session, worker


def test_the_window_is_the_latest_part_every_ring_holds(tmp_path):
    traces = tmp_path / "trace"
    start, end = write_chain_traces(traces)
    graph = NodeGraph.from_system_structure(chain_design())

    def window_wall_s(**window):
        out = tmp_path / "Test_latency.json"
        return analyze_traces(traces, graph, mode="Test", probe=True, latency_out=out, **window)["run"]["window_wall_s"]

    assert window_wall_s(window_start_ns=start, window_end_ns=end) == 1.0
    assert window_wall_s(window_start_ns=start, window_end_ns=start + 3 * 3600 * S) == MAX_WINDOW_S

    # /b's ring wrapped: its oldest record, the first take, is where the window starts.
    path = traces / "200.trace"
    data = bytearray(path.read_bytes())
    capacity = int.from_bytes(data[24:32], "little")
    data[32:40] = (capacity + 1).to_bytes(8, "little")  # count
    path.write_bytes(bytes(data))
    first_take = start + 1 * MS + 50 * US + 500 * US
    assert window_wall_s(window_start_ns=start, window_end_ns=end) == round((end - first_take) / S, 3)


def test_trace_dir_progress_reads_headers_only(tmp_path):
    write_chain_traces(tmp_path)
    progress = trace_dir_progress(tmp_path)
    full = read_trace_dir(tmp_path)
    assert progress.processes == len(full.processes)
    assert progress.records == sum(p.record_count for p in full.processes.values())
    assert progress.overwritten == 0
    assert progress.describe().endswith(f"{progress.records} records")


class _CollectLogs(logging.Handler):
    """Attached to the session logger directly: the launch package's logger class bypasses caplog."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_timed_window_closes_then_shuts_down_then_analyzes(tmp_path, monkeypatch):
    session, worker = _session(tmp_path, monkeypatch, duration=0.1, settle=0.05, heartbeat=0.02)
    coord = _StubCoord()
    logs = _CollectLogs()
    session_logger = logging.getLogger("autoware_system_designer_runtime._impl.measure.session")
    session_logger.addHandler(logs)
    session_logger.setLevel(logging.INFO)

    async def run():
        await session._post_start(coord)
        assert session.state == "armed"
        coord.launch_ready.set()
        await asyncio.wait_for(coord.shutdown_event.wait(), timeout=5)
        # The window is closed and the shutdown requested before any analysis ran.
        assert session.state == "closed"
        assert coord.shutdown_requests == 1
        assert not session.latency_out.exists()
        await asyncio.gather(*coord.tasks)
        await session._on_shutdown(coord)

    try:
        asyncio.run(run())
    finally:
        session_logger.removeHandler(logs)
    assert session.state == "done"
    assert worker.probes and worker.removed == 1
    assert _read_latency(session.latency_out)["schema"] == LATENCY_SCHEMA
    messages = logs.messages
    assert any(m.startswith("measure: window open: settle 0.05 s, record 0.1 s, closes at ") for m in messages)
    assert any("measure: settling " in m or "measure: recording " in m for m in messages)  # heartbeat
    assert any(m.startswith("measure: window closed (duration elapsed): ") for m in messages)
    assert any("shutting the system down" in m for m in messages)
    assert any(m.startswith("measure: analyzing ") for m in messages)
    assert any(m.startswith("measure: analysis done in ") for m in messages)


def test_open_window_closes_at_shutdown_and_analyzes_in_the_hook(tmp_path, monkeypatch):
    session, worker = _session(tmp_path, monkeypatch, settle=0.0, heartbeat=0.02)
    coord = _StubCoord()

    async def run():
        await session._post_start(coord)
        coord.launch_ready.set()
        await asyncio.sleep(0.05)
        assert session.state == "running"
        assert session.status().startswith("recording ")
        coord.request_shutdown()
        await asyncio.gather(*coord.tasks)
        assert session.state == "closed"  # closed the moment the shutdown was requested
        assert worker.removed == 1
        await session._on_shutdown(coord)

    asyncio.run(run())
    assert session.state == "done"
    assert session.status() == f"done -> {session.latency_out}"


def test_console_close_and_analyze_are_idempotent(tmp_path, monkeypatch):
    session, _worker = _session(tmp_path, monkeypatch, settle=0.0)

    async def run():
        assert await session.close("console stop") == "measurement is armed, nothing to close"
        control = TraceControl(session.trace_dir)
        assert not control.events()
        await session.start()
        assert control.events()  # the window switches event recording on in every traced process
        assert await session.start() == "measurement already running"
        assert (await session.close("console stop")).startswith("window closed (console stop): ")
        assert not control.events()
        assert (await session.analyze()).startswith("analysis done in ")
        assert await session.analyze() == "measurement is done, nothing to analyze"
        assert await session.start() == "measurement done; one window per launch"

    asyncio.run(run())


def test_latency_file_is_json(tmp_path):
    traces = tmp_path / "trace"
    start, end = write_chain_traces(traces)
    graph = NodeGraph.from_system_structure(chain_design())
    json_out = tmp_path / "data" / "Test" / "20261105-120000.json"

    data = analyze_traces(
        traces, graph, window_start_ns=start, window_end_ns=end, mode="Test", probe=True, latency_out=json_out
    )

    assert json.loads(json_out.read_text()) == data
    assert sorted(json_out.parent.iterdir()) == [json_out]


def test_analyze_traces_counts_in_ros_time_when_the_run_had_a_clock(tmp_path):
    traces = tmp_path / "trace"
    start, end = write_chain_traces(traces)
    write_clock_trace(traces, start, end, rate=0.5)
    graph = NodeGraph.from_system_structure(chain_design())
    latency_out = tmp_path / "latency" / "Test_latency.json"

    data = analyze_traces(
        traces, graph, window_start_ns=start, window_end_ns=end, mode="Test", probe=True, latency_out=latency_out
    )

    run = data["run"]
    assert run["clock"] == {"base": "ros", "rate": 0.5, "samples": 103}
    assert run["window_s"] == 0.5 and run["window_wall_s"] == 1.0
    by_path = {n["node_path"]: n for n in data["nodes"]}
    # 50 fires in 0.5 ROS seconds: the declared 50 Hz timer runs at its declared rate in ROS time.
    assert by_path["/a"]["timers"] == [{"period_ms": 20.0, "count": 50, "rate_hz": 100.0}]
    assert by_path["/a"]["outputs"][0]["exec"]["mean_ms"] == 0.5

    wall = analyze_traces(
        traces,
        graph,
        window_start_ns=start,
        window_end_ns=end,
        mode="Test",
        probe=True,
        latency_out=latency_out,
        clock="wall",
    )
    assert wall["run"]["clock"] == {"base": "wall"} and wall["run"]["window_s"] == 1.0


def test_analysis_reads_a_snapshot_of_the_exits(tmp_path, monkeypatch):
    session, _worker = _session(tmp_path, monkeypatch, settle=0.0)
    seen = {}

    def fake_analyze_traces(trace_dir, graph, **kwargs):
        seen["exits"] = kwargs["exits"]
        return {
            "run": {"window_s": 0, "clock": {"base": "wall"}},
            "summary": {"observed_nodes": 0, "design_nodes": 0},
            "links": [],
            "chains": [],
        }

    monkeypatch.setattr("autoware_system_designer_runtime._impl.measure.session.analyze_traces", fake_analyze_traces)
    session.on_state_event(ev.Started(name="/n", pid=77))
    session.on_state_event(ev.Exited(name="/n", exit_code=0))

    async def run():
        await session.start()
        await session.close("console stop")
        await session.analyze()

    asyncio.run(run())
    assert seen["exits"] == session.exits
    assert seen["exits"] is not session._exits


def test_coordinator_run_ends_as_soon_as_the_last_task_finishes_after_shutdown(monkeypatch, tmp_path):
    async def fake_spawn(cmd, *, env=None, stdout_path=None, stderr_path=None, cwd=None):
        return _FakeProc()

    monkeypatch.setattr(regular_actor, "spawn_pgrp", fake_spawn)
    builder = CoordinatorBuilder(
        default_config=ActorConfig(output_dir=tmp_path, respawn_enabled=False, graceful_shutdown_timeout=0.1)
    )
    builder.add_node(NodeSpec(name="/n", cmd=["true"]))
    task_done = asyncio.Event()

    # A task that outlives the last actor: it ends shortly after the Terminated event,
    # with no actor event left to wake the run loop before its shutdown deadline.
    async def extra():
        await task_done.wait()

    def hook(event):
        if isinstance(event, ev.Exited):
            coord.request_shutdown()
        if isinstance(event, ev.Terminated):
            asyncio.get_running_loop().call_later(0.1, task_done.set)

    async def post_start(c):
        c.schedule_task(extra())

    builder.add_state_hook(hook)
    builder.add_post_start_hook(post_start)
    coord = builder.build()
    began = time.monotonic()
    assert asyncio.run(asyncio.wait_for(coord.run(), timeout=10)) == 0
    assert time.monotonic() - began < 2.0  # the deadline is the grace period + 5 s
