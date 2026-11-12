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

"""Launch sessions of the serve daemon, driven over a fake runtime's control socket."""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from autoware_system_designer_studio.serve import api, jobs, launch

from .test_serve import PKG, SYSTEM, UNITS, _await_job_output, _install_tree, _Running
from .test_serve_jobs import _wait_job

# A runtime stand-in: serves the control protocol on the --control socket. Behaviour switches come
# in as system arguments (``--arg behaviour=...``): ``nosocket`` never serves the socket, ``big`` sends
# a monitor snapshot far over 64 KiB.
# Fake tools run on this interpreter: the launch PATH under test holds only the fake bin.
SHEBANG = f"#!{sys.executable}\n"

FAKE_RUNTIME = """\
import asyncio, json, os, signal, sys

args = sys.argv[1:]
sock = args[args.index("--control") + 1]
behaviour = next((a.split("=", 1)[1] for a in args if a.startswith("behaviour=")), "")
log_dir = sock + ".logs"
os.makedirs(os.path.join(log_dir, "demo_talker"), exist_ok=True)
with open(os.path.join(log_dir, "run.json"), "w") as f:
    json.dump({"system_structure": args[0], "mode": "Runtime", "compute_unit": args[args.index("--compute-unit") + 1],
               "deploy": None,
               "launch": args[args.index("--launch-id") + 1] if "--launch-id" in args else None,
               "started": 1.5, "members": ["/demo/talker", "/demo/listener"]}, f)
with open(os.path.join(log_dir, "demo_talker", "out"), "w") as f:
    f.write("hello 1\\nhello 2\\n")
with open(os.path.join(log_dir, "demo_talker", "err"), "w") as f:
    f.write("bye")
METHODS = ["status", "stop", "restart", "kill", "measure.start", "measure.stop", "measure.status",
           "monitor.sample", "monitor.follow", "monitor.unfollow", "shutdown"]
RATES = {"time_ns": 1, "interval_s": 2.0, "processes": 1, "untracked_endpoints": 0,
         "nodes": [{"node": "/demo/talker", "pids": [1], "inputs": [],
                    "outputs": [{"topic": "/chatter", "hz": 1.0, "callback_ms": 0.1}], "timers": []}]}
members = [
    {"path": "/demo/talker", "state": "pending", "detail": {}, "time": 0.0},
    {"path": "/demo/listener", "state": "pending", "detail": {}, "time": 0.0},
]


async def main():
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
    print("argv: " + " ".join(args), flush=True)
    if behaviour == "nosocket":
        await stop.wait()
        return

    async def serve(reader, writer):
        def send(obj):
            writer.write((json.dumps(obj) + "\\n").encode())

        send({"event": "hello", "pid": os.getpid(), "methods": METHODS, "policy": "guard",
              "measure_armed": "--measure" in args, "monitor": True, "members": members, "launch_ready": True,
              "shutting_down": False, "log_dir": log_dir})
        send({"event": "member", "path": "/demo/talker", "state": "running", "detail": {"pid": 1}, "time": 1.0})
        send({"event": "member.activity", "window": 2.0, "rates": {"/demo/talker": 5120}})
        follows = set()
        while True:
            line = await reader.readline()
            if not line:
                break
            req = json.loads(line)
            method, params = req["method"], req.get("params") or {}
            if method == "status":
                result = {"members": members, "launch_ready": True, "shutting_down": False}
            elif method in ("stop", "restart", "kill"):
                pattern = params.get("pattern")
                if pattern == "/guarded":
                    send({"id": req["id"], "ok": False,
                          "error": {"code": "refused", "message": "stop refused: operation mode is autonomous"}})
                    continue
                send({"event": "member", "path": pattern, "state": "stopped", "detail": {"exit_code": 0}, "time": 2.0})
                result = {"members": [pattern], **({"exact": True} if params.get("exact") is True else {})}
            elif method == "log.follow":
                if params.get("path") not in [m["path"] for m in members]:
                    send({"id": req["id"], "ok": False, "error": {"code": "not_found", "message": "no member"}})
                    continue
                follows.add(params["path"])
                result = {"path": params["path"], "out": ["hello 1"], "err": []}
                send({"event": "member.log", "path": params["path"], "stream": "out", "lines": ["hello 2"]})
            elif method == "log.unfollow":
                released = sorted(follows) if "path" not in params else [params["path"]]
                follows.difference_update(released)
                result = {"released": released}
            elif method == "measure.status":
                result = {"state": "idle", "message": "measure idle"}
            elif method == "monitor.sample":
                result = RATES
            elif method == "monitor.follow":
                result = {"interval_s": 2.0}
                rates = RATES
                if behaviour == "big":
                    node = RATES["nodes"][0]
                    rates = {**RATES, "nodes": [{**node, "node": f"/n{i:05d}"} for i in range(3000)]}
                send({"event": "monitor", **rates})
            elif method == "monitor.unfollow":
                result = {"released": True}
                with open(log_dir + "/unfollowed", "a") as f:
                    f.write("x")
            elif method == "shutdown":
                result = {"shutting_down": True}
                stop.set()
            else:
                send({"id": req["id"], "ok": False, "error": {"code": "unknown_method", "message": method}})
                continue
            send({"id": req["id"], "ok": True, "result": result})
            await writer.drain()

    server = await asyncio.start_unix_server(serve, sock)
    await stop.wait()
    server.close()
    os.unlink(sock)


asyncio.run(main())
"""


def _fake_launcher(root):
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / launch.LAUNCHER
    script.write_text(SHEBANG + FAKE_RUNTIME)
    script.chmod(0o755)
    return bin_dir


FAKE_RVIZ = """\
import signal, sys, time
print("argv: " + " ".join(sys.argv[1:]), flush=True)
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
while True:
    time.sleep(0.1)
"""


def _fake_rviz(bin_dir):
    script = bin_dir / launch.RVIZ
    script.write_text(SHEBANG + FAKE_RVIZ)
    script.chmod(0o755)
    return script


def _launch_env(bin_dir, **extra):
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "ROS_DISTRO": "test", **extra}


class _Recording:
    """Published events of a hub, by topic."""

    def __init__(self, hub):
        self.events = []
        original = hub.publish

        def publish(topic, data):
            self.events.append((topic, data))
            return original(topic, data)

        hub.publish = publish

    def of(self, topic):
        return [data for t, data in self.events if t == topic]


@pytest.fixture
def run_dir(monkeypatch):
    """A short socket directory; Unix socket paths are capped near 108 bytes, which pytest's tmp_path exceeds."""
    path = tempfile.mkdtemp(prefix="asd-run-")
    monkeypatch.setenv("XDG_RUNTIME_DIR", path)
    yield Path(path)
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def launching(tmp_path, run_dir):
    install = _install_tree(tmp_path, compute_units=UNITS)
    running = _Running(
        install,
        served_units=["main_ecu"],
        launch_env=_launch_env(_fake_launcher(tmp_path)),
        env_script=tmp_path / "setup.bash",
        heartbeat_s=60,
    )
    running.events = _Recording(running.daemon.hub)
    yield running
    running.close()


def _start(running, **params):
    status, body = running.rpc(
        {
            "id": 1,
            "method": "launch.start",
            "params": {"system": f"{PKG}:{SYSTEM}", "compute_unit": "main_ecu", **params},
        }
    )
    if status == 200:
        _await_job_output(running, body["result"]["job"])
    return status, body


def _greeted(session):
    """The runtime's hello and its first member event have arrived; the connection alone precedes both."""
    states = {member["path"]: member["state"] for member in session["members"]}
    return session["connected"] and bool(session["control"]) and states.get("/demo/talker") == "running"


def _wait_session(running, predicate, what="session"):
    deadline = time.time() + 10
    while time.time() < deadline:
        _, body = running.rpc({"method": "launch.status", "params": {"compute_unit": "main_ecu"}})
        session = body["result"]["sessions"][0]
        if predicate(session):
            return session
        time.sleep(0.05)
    pytest.fail(f"{what} did not come: {session}")


# -- environment and launcher ---------------------------------------------------


def test_member_slug_matches_the_runtime_log_layout():
    assert launch.member_slug("/sensing/lidar/front_upper/lidar") == "sensing_lidar_front_upper_lidar"
    assert launch.member_slug("/a-b.c_d/e f") == "a-b.c_d_e_f"
    assert launch.member_slug("//") == "_unnamed_"


def test_tail_lines_reads_the_end_of_a_file(tmp_path):
    missing = tmp_path / "none"
    assert launch.tail_lines(missing) == []
    (tmp_path / "out").write_bytes(b"one\ntwo\nthree")
    assert launch.tail_lines(tmp_path / "out") == ["one", "two", "three"]
    (tmp_path / "long").write_bytes(b"".join(b"line %06d\n" % i for i in range(10000)))
    tail = launch.tail_lines(tmp_path / "long")
    assert len(tail) == launch.TAIL_LINES and tail[-1] == "line 009999"


def test_capture_env_sources_the_script_over_this_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEP_ME", "1")
    script = tmp_path / "setup.bash"
    script.write_text('export ROS_DISTRO=humble\nexport MULTI="a\nb"\necho noise\n')
    env = launch.capture_env(script)
    assert env["ROS_DISTRO"] == "humble"
    assert env["MULTI"] == "a\nb"
    assert env["KEEP_ME"] == "1"
    assert launch.capture_env(None) == dict(os.environ)


def test_capture_env_reports_a_failing_script(tmp_path):
    script = tmp_path / "setup.bash"
    script.write_text("echo broken >&2\nexit 3\n")
    with pytest.raises(launch.LaunchEnvError, match="broken"):
        launch.capture_env(script)
    with pytest.raises(launch.LaunchEnvError):
        launch.capture_env(tmp_path / "missing.bash")


def test_find_launcher_prefers_the_path_then_the_install_tree(tmp_path):
    install = tmp_path / "install"
    assert launch.find_launcher(install, {"PATH": str(tmp_path / "nowhere")}) is None
    isolated = install / launch.RUNTIME_PACKAGE / "bin" / launch.LAUNCHER
    isolated.parent.mkdir(parents=True)
    isolated.write_text("#!/bin/sh\n")
    isolated.chmod(0o755)
    assert launch.find_launcher(install, {"PATH": str(tmp_path / "nowhere")}) == isolated
    bin_dir = _fake_launcher(tmp_path)
    assert launch.find_launcher(install, {"PATH": str(bin_dir)}) == bin_dir / launch.LAUNCHER


def test_find_rviz_config_walks_the_ament_prefixes(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    config = second / launch.RVIZ_CONFIG_SHARE
    config.parent.mkdir(parents=True)
    config.write_text("")
    env = {"AMENT_PREFIX_PATH": os.pathsep.join([str(first), str(second)])}
    assert launch.find_rviz_config(env) == config
    assert launch.find_rviz_config({"AMENT_PREFIX_PATH": str(first)}) is None
    assert launch.find_rviz_config({}) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, {}),
        ({"vehicle_id": "a", "rate": 10, "flag": True}, {"vehicle_id": "a", "rate": "10", "flag": "true"}),
    ],
)
def test_parse_args_param_binds_scalars(value, expected):
    assert launch.parse_args_param(value) == expected


@pytest.mark.parametrize("value", [[], {"bad name": "x"}, {"ok": ["x"]}])
def test_parse_args_param_rejects_shapes(value):
    with pytest.raises(api.ApiError) as info:
        launch.parse_args_param(value)
    assert info.value.code == api.INVALID_PARAMS


# -- sessions -------------------------------------------------------------------


def test_launch_runs_the_launcher_and_follows_its_control_channel(launching):
    _, health = launching.json("GET", "/api/health")
    assert "launch.start" in health["result"]["methods"] and "measure.status" in health["result"]["methods"]
    assert health["result"]["launch"]["ros_distro"] == "test"
    assert health["result"]["launch"]["launcher"].endswith(launch.LAUNCHER)

    status, body = _start(launching, mode="Runtime", deploy="car_a", args={"behaviour": "", "x": 1}, measure=True)
    assert status == 200, body
    session = body["result"]
    assert session["state"] == launch.SESSION_STARTING
    assert session["job"] == "launch-1"

    _, listed = launching.json("GET", "/api/systems")
    units = {u["name"]: u["status"] for u in listed["result"]["systems"][0]["compute_units"]}
    assert units["main_ecu"] == "running"
    status, body = _start(launching)
    assert status == 409 and body["error"]["code"] == api.CONFLICT

    session = _wait_session(launching, _greeted, "connection")
    assert session["state"] == launch.SESSION_RUNNING
    assert session["control"]["measure_armed"] is True
    assert session["launch_ready"] is True
    assert {m["path"]: m["state"] for m in session["members"]} == {
        "/demo/talker": "running",
        "/demo/listener": "pending",
    }

    _, job = launching.rpc({"method": "jobs.get", "params": {"job": "launch-1"}})
    argv = job["result"]["job"]["argv"]
    assert argv[1].endswith("system_structure/Runtime.json")
    assert argv[2:6] == ["--compute-unit", "main_ecu", "--control", job["result"]["job"]["detail"]["socket"]]
    assert argv[6:8] == ["--control-policy", "guard"]
    launch_id = job["result"]["job"]["params"]["launch"]
    assert argv[8:] == [
        "--deploy",
        "car_a",
        "--launch-id",
        launch_id,
        "--arg",
        "behaviour=",
        "--arg",
        "x=1",
        "--measure",
    ]
    assert job["result"]["job"]["params"]["measure"] is True

    deadline = time.time() + 5
    while time.time() < deadline and not launching.events.of(launch.TOPIC_MEMBER):
        time.sleep(0.02)
    member_events = launching.events.of(launch.TOPIC_MEMBER)
    assert {
        "compute_unit": "main_ecu",
        "system": f"{PKG}:{SYSTEM}",
        "mode": "Runtime",
        "path": "/demo/talker",
        "state": "running",
        "detail": {"pid": 1},
        "time": 1.0,
        "restarts": 0,
        "respawns": 0,
    } in member_events
    while time.time() < deadline and not launching.events.of(launch.TOPIC_MEMBER_ACTIVITY):
        time.sleep(0.02)
    assert launching.events.of(launch.TOPIC_MEMBER_ACTIVITY) == [
        {
            "compute_unit": "main_ecu",
            "system": f"{PKG}:{SYSTEM}",
            "mode": "Runtime",
            "window": 2.0,
            "rates": {"/demo/talker": 5120},
        }
    ]
    # Member output flows only once followed; the follow answers with the tail.
    assert launching.events.of(launch.TOPIC_MEMBER_LOG) == []
    status, body = launching.rpc(
        {"method": "launch.follow", "params": {"compute_unit": "main_ecu", "path": "/demo/talker"}}
    )
    assert status == 200 and body["result"] == {"path": "/demo/talker", "out": ["hello 1"], "err": []}
    deadline = time.time() + 5
    while time.time() < deadline and not launching.events.of(launch.TOPIC_MEMBER_LOG):
        time.sleep(0.02)
    assert launching.events.of(launch.TOPIC_MEMBER_LOG) == [
        {"compute_unit": "main_ecu", "path": "/demo/talker", "stream": "out", "lines": ["hello 2"]}
    ]
    assert launching.daemon.launch.session("main_ecu").follows == {"/demo/talker"}
    status, body = launching.rpc({"method": "launch.follow", "params": {"compute_unit": "main_ecu", "path": "/nope"}})
    assert status == 404 and body["error"]["code"] == api.NOT_FOUND
    status, body = launching.rpc({"method": "launch.unfollow", "params": {"compute_unit": "main_ecu"}})
    assert status == 200 and body["result"] == {"released": ["/demo/talker"]}
    assert launching.daemon.launch.session("main_ecu").follows == set()
    # Follows end with the last event stream.
    launching.rpc({"method": "launch.follow", "params": {"compute_unit": "main_ecu", "path": "/demo/talker"}})
    assert launching.daemon.launch.session("main_ecu").follows == {"/demo/talker"}
    launching.loop.call_soon_threadsafe(launching.daemon.launch.release_follows)
    deadline = time.time() + 5
    while time.time() < deadline and launching.daemon.launch.session("main_ecu").follows:
        time.sleep(0.02)
    assert launching.daemon.launch.session("main_ecu").follows == set()
    launches = launching.events.of(launch.TOPIC_LAUNCH)
    assert [e["state"] for e in launches][:2] == [launch.SESSION_STARTING, launch.SESSION_RUNNING]
    assert "members" not in launches[0]

    status, body = launching.rpc(
        {"method": "launch.stop", "params": {"compute_unit": "main_ecu", "pattern": "/demo/listener", "exact": True}}
    )
    assert status == 200 and body["result"] == {"members": ["/demo/listener"], "exact": True}
    session = _wait_session(launching, lambda s: s["members"][1]["state"] == "stopped", "stop")
    assert session["members"][1]["detail"] == {"exit_code": 0}

    status, body = launching.rpc(
        {"method": "launch.kill", "params": {"compute_unit": "main_ecu", "pattern": "/guarded"}}
    )
    assert status == 409
    assert body["error"] == {"code": api.REFUSED, "message": "stop refused: operation mode is autonomous"}

    status, body = launching.rpc(
        {"method": "launch.restart", "params": {"compute_unit": "main_ecu", "pattern": "/demo/talker", "exact": "yes"}}
    )
    assert status == 400 and body["error"]["code"] == api.INVALID_PARAMS

    status, body = launching.rpc({"method": "measure.status", "params": {"compute_unit": "main_ecu"}})
    assert status == 200 and body["result"] == {"state": "idle", "message": "measure idle"}

    # Topic rates: sampled once, or followed by named callers and forwarded as the monitor topic.
    assert session["control"]["monitor"] is True
    status, body = launching.rpc({"method": "monitor.sample", "params": {"compute_unit": "main_ecu"}})
    assert status == 200 and body["result"]["nodes"][0]["node"] == "/demo/talker"
    status, body = launching.rpc({"method": "monitor.sample", "params": {"compute_unit": "main_ecu", "interval": 60}})
    assert status == 400 and body["error"]["code"] == api.INVALID_PARAMS
    for client in ("a", "a", "b"):  # following twice is following once
        params = {"compute_unit": "main_ecu", "client": client}
        status, body = launching.rpc({"method": "monitor.follow", "params": params})
        assert status == 200 and body["result"] == {"interval_s": 2.0}
    deadline = time.time() + 5
    while time.time() < deadline and not launching.events.of(launch.TOPIC_MONITOR):
        time.sleep(0.02)
    monitor_event = launching.events.of(launch.TOPIC_MONITOR)[0]
    assert monitor_event["compute_unit"] == "main_ecu" and monitor_event["mode"] == "Runtime"
    assert monitor_event["nodes"][0]["outputs"][0]["hz"] == 1.0
    unfollowed = Path(session["log_dir"]) / "unfollowed"
    status, body = launching.rpc({"method": "monitor.unfollow", "params": {"compute_unit": "main_ecu", "client": "a"}})
    assert body["result"] == {"released": False}  # one follower is left
    assert launching.daemon.launch.session("main_ecu").monitors == {"b"}
    launching.loop.call_soon_threadsafe(launching.daemon.launch.release_follows)
    deadline = time.time() + 5
    # The file exists before its content is written.
    while time.time() < deadline and not (unfollowed.exists() and unfollowed.read_text()):
        time.sleep(0.02)
    assert launching.daemon.launch.session("main_ecu").monitors == set() and unfollowed.read_text() == "x"

    status, body = launching.rpc({"method": "launch.shutdown", "params": {"compute_unit": "main_ecu"}})
    assert status == 200 and body["result"] == {"shutting_down": True}
    result = _wait_job(launching, "launch-1")
    assert result["job"]["state"] == jobs.STATE_SUCCEEDED
    session = _wait_session(launching, lambda s: s["state"] == launch.SESSION_EXITED, "exit")
    assert session["connected"] is False and session["exit_code"] == 0
    assert not Path(job["result"]["job"]["detail"]["socket"]).exists()
    # Without the channel, a follow reads the member's files from the runtime's log directory.
    assert session["log_dir"] == job["result"]["job"]["detail"]["socket"] + ".logs"
    status, body = launching.rpc(
        {"method": "launch.follow", "params": {"compute_unit": "main_ecu", "path": "/demo/talker"}}
    )
    assert status == 200 and body["result"] == {"path": "/demo/talker", "out": ["hello 1", "hello 2"], "err": ["bye"]}
    status, body = launching.rpc(
        {"method": "launch.follow", "params": {"compute_unit": "main_ecu", "path": "/demo/listener"}}
    )
    assert status == 200 and body["result"] == {"path": "/demo/listener", "out": [], "err": []}
    status, body = launching.rpc({"method": "launch.follow", "params": {"compute_unit": "main_ecu", "path": "/nope"}})
    assert status == 404 and body["error"]["code"] == api.NOT_FOUND
    status, body = launching.rpc({"method": "launch.unfollow", "params": {"compute_unit": "main_ecu"}})
    assert status == 200 and body["result"] == {"released": []}
    _, listed = launching.json("GET", "/api/systems")
    assert listed["result"]["systems"][0]["compute_units"][0]["status"] == "available"

    for method in ("launch.stop", "measure.start"):
        status, body = launching.rpc({"method": method, "params": {"compute_unit": "main_ecu", "pattern": "x"}})
        assert status == 404, method
    status, body = launching.rpc({"method": "launch.status", "params": {"compute_unit": "sub_ecu"}})
    assert status == 404


def _sessions(running, unit):
    _, body = running.rpc({"method": "launch.status", "params": {"compute_unit": unit}})
    return {session["mode"]: session for session in body["result"]["sessions"]}


def _shutdown(running, unit, mode):
    status, body = running.rpc({"method": "launch.shutdown", "params": {"compute_unit": unit}})
    assert status == 200, body
    deadline = time.time() + 10
    while time.time() < deadline and _sessions(running, unit)[mode]["state"] != launch.SESSION_EXITED:
        time.sleep(0.05)
    assert _sessions(running, unit)[mode]["state"] == launch.SESSION_EXITED


def test_a_system_runs_one_mode_at_a_time_and_keeps_the_last_session_of_each(tmp_path, run_dir):
    install = _install_tree(tmp_path, compute_units=UNITS)
    structures = install / PKG / "share" / PKG / "exports" / SYSTEM / "system_structure"
    (structures / "Simulation.json").write_text((structures / "Runtime.json").read_text())
    running = _Running(
        install,
        served_units=["main_ecu", "sub_ecu"],
        launch_env=_launch_env(_fake_launcher(tmp_path)),
        heartbeat_s=60,
    )
    try:
        nosocket = {"behaviour": "nosocket"}
        status, body = _start(running, mode="Runtime", args=nosocket)
        assert status == 200, body
        # Another unit joins the running mode; another mode waits for every unit of the system.
        status, body = _start(running, compute_unit="sub_ecu", mode="Simulation", args=nosocket)
        assert status == 409 and body["error"]["code"] == api.CONFLICT, body
        status, body = _start(running, compute_unit="sub_ecu", mode="Runtime", args=nosocket)
        assert status == 200, body
        _shutdown(running, "main_ecu", "Runtime")
        status, body = _start(running, mode="Simulation", args=nosocket)
        assert status == 409 and body["error"]["code"] == api.CONFLICT, body
        _shutdown(running, "sub_ecu", "Runtime")

        status, body = _start(running, mode="Simulation", args=nosocket)
        assert status == 200, body
        sessions = _sessions(running, "main_ecu")
        assert sessions["Runtime"]["state"] == launch.SESSION_EXITED
        assert sessions["Simulation"]["state"] != launch.SESSION_EXITED
        manager = running.daemon.launch
        assert manager.session("main_ecu").mode == "Simulation"
        assert manager.session("main_ecu", system_id=f"{PKG}:{SYSTEM}", mode="Runtime").mode == "Runtime"
        for scope in ({"system_id": f"{PKG}:{SYSTEM}"}, {"mode": "Runtime"}):
            with pytest.raises(api.ApiError) as info:
                manager.session("main_ecu", **scope)
            assert info.value.code == api.INVALID_PARAMS
        _shutdown(running, "main_ecu", "Simulation")
    finally:
        running.close()


def test_shutdown_of_a_launch_without_a_channel_cancels_its_job(launching):
    status, body = _start(launching, args={"behaviour": "nosocket"})
    assert status == 200, body
    status, body = launching.rpc({"method": "launch.shutdown", "params": {"compute_unit": "main_ecu"}})
    assert status == 200 and body["result"] == {"shutting_down": True, "cancelled": True}
    result = _wait_job(launching, "launch-1")
    assert result["job"]["state"] == jobs.STATE_CANCELLED
    session = _wait_session(launching, lambda s: s["state"] == launch.SESSION_EXITED, "exit")
    assert session["connected"] is False
    status, body = launching.rpc({"method": "launch.stop", "params": {"compute_unit": "main_ecu", "pattern": "x"}})
    assert status == 404


@pytest.mark.parametrize(
    "params, code",
    [
        ({"compute_unit": "ghost"}, api.INVALID_PARAMS),
        ({"compute_unit": "sub_ecu"}, api.INVALID_PARAMS),
        ({"compute_unit": "infra"}, api.INVALID_PARAMS),
        ({"mode": "Nope"}, api.INVALID_PARAMS),
        ({"mode": "Simulation"}, api.INVALID_PARAMS),
        ({"deploy": "car_z"}, api.INVALID_PARAMS),
        ({"policy": "lax"}, api.INVALID_PARAMS),
        ({"measure": "yes"}, api.INVALID_PARAMS),
        ({"args": "x=1"}, api.INVALID_PARAMS),
        ({"launch": "a/b"}, api.INVALID_PARAMS),
        ({"system": "x:y"}, api.NOT_FOUND),
    ],
)
def test_launch_start_validates_its_params(launching, params, code):
    status, body = _start(launching, **params)
    assert body["error"]["code"] == code, body
    assert status == api.ERROR_STATUS[code]
    assert launching.daemon.launch.sessions == {}


def test_launch_is_unavailable_without_a_ros_environment_or_launcher(tmp_path):
    install = _install_tree(tmp_path, compute_units=UNITS)
    bin_dir = _fake_launcher(tmp_path)
    running = _Running(install, launch_env={**_launch_env(bin_dir), "ROS_DISTRO": ""}, heartbeat_s=60)
    try:
        status, body = _start(running)
        assert status == 503 and "sourced" in body["error"]["message"]
    finally:
        running.close()
    running = _Running(install, launch_env={**_launch_env(bin_dir), "PATH": str(tmp_path / "nowhere")}, heartbeat_s=60)
    try:
        _, health = running.json("GET", "/api/health")
        assert health["result"]["launch"]["launcher"] is None
        status, body = _start(running)
        assert status == 503 and launch.LAUNCHER in body["error"]["message"]
    finally:
        running.close()


def test_rviz_start_runs_rviz_once_on_the_configuration(tmp_path, run_dir):
    install = _install_tree(tmp_path, compute_units=UNITS)
    bin_dir = _fake_launcher(tmp_path)
    rviz = _fake_rviz(bin_dir)
    config = tmp_path / "custom.rviz"
    config.write_text("")
    running = _Running(install, launch_env=_launch_env(bin_dir, PATH=str(bin_dir)), rviz_config=config, heartbeat_s=60)
    try:
        _, health = running.json("GET", "/api/health")
        assert health["result"]["launch"]["rviz"] == str(rviz)
        assert health["result"]["launch"]["rviz_config"] == str(config)
        status, body = running.rpc({"id": 1, "method": "rviz.start", "params": {}})
        assert status == 200, body
        job = body["result"]
        assert job["kind"] == launch.RVIZ_JOB_KIND and job["argv"] == [str(rviz), "-d", str(config)]
        status, body = running.rpc({"id": 2, "method": "rviz.start", "params": {}})
        assert status == 409 and body["error"]["code"] == api.CONFLICT
        running.rpc({"method": "jobs.cancel", "params": {"job": job["id"]}})
        assert _wait_job(running, job["id"])["job"]["state"] == jobs.STATE_CANCELLED
        status, body = running.rpc({"id": 3, "method": "rviz.start", "params": {}})
        assert status == 200, body
    finally:
        running.close()


def test_rviz_start_without_a_configuration_or_rviz(tmp_path, run_dir):
    install = _install_tree(tmp_path, compute_units=UNITS)
    bin_dir = _fake_launcher(tmp_path)
    running = _Running(
        install, launch_env=_launch_env(bin_dir, PATH=str(bin_dir), AMENT_PREFIX_PATH=""), heartbeat_s=60
    )
    try:
        _, health = running.json("GET", "/api/health")
        assert health["result"]["launch"]["rviz"] is None
        assert health["result"]["launch"]["rviz_config"] is None
        status, body = running.rpc({"id": 1, "method": "rviz.start", "params": {}})
        assert status == 503 and launch.RVIZ in body["error"]["message"]
        rviz = _fake_rviz(bin_dir)
        status, body = running.rpc({"id": 2, "method": "rviz.start", "params": {}})
        assert status == 200, body
        assert body["result"]["argv"] == [str(rviz)]
    finally:
        running.close()


def test_daemon_stop_ends_a_running_launch(tmp_path, run_dir):
    install = _install_tree(tmp_path, compute_units=UNITS)
    running = _Running(install, launch_env=_launch_env(_fake_launcher(tmp_path)), heartbeat_s=60)
    status, body = _start(running)
    assert status == 200, body
    _wait_session(running, _greeted, "connection")
    job = running.daemon.jobs.get("launch-1")
    running.close()
    assert job.state == jobs.STATE_CANCELLED
    assert not list(run_dir.glob("**/*.sock"))


# -- past runs -------------------------------------------------------------------


def _run_tree(base, name, manifest, members=("demo_talker",)):
    run = base / name
    for member in members:
        (run / member).mkdir(parents=True)
        (run / member / "out").write_text(f"{member} out\n")
        (run / member / "err").write_text("")
    if manifest is not None:
        (run / launch.RUN_MANIFEST).write_text(json.dumps(manifest))
    return run


@pytest.fixture
def archived(tmp_path):
    """A daemon over a log base of three past runs (two of the system: by name, by structure path; one of
    another system), a directory without a manifest and a stray file."""
    install = _install_tree(tmp_path, compute_units=UNITS)
    export = install / PKG / "share" / PKG / "exports" / SYSTEM
    base = tmp_path / "logs"
    _run_tree(
        base,
        "20260101_000000",
        {
            "system": SYSTEM,
            "mode": "Runtime",
            "compute_unit": "main_ecu",
            "deploy": "site_a",
            "started": 1.0,
            "members": ["/demo/talker", "/demo/listener"],
        },
    )
    _run_tree(
        base,
        "20260102_000000",
        {
            "system_structure": str(export / "system_structure" / "Runtime.json"),
            "started": 2.0,
            "members": ["/demo/talker"],
        },
    )
    _run_tree(base, "20260103_000000", {"system": "Other", "started": 3.0})
    _run_tree(base, "20260104_000000", None, members=("demo_talker", "demo_listener"))
    (base / "stray").write_text("")
    running = _Running(install, served_units=["main_ecu"], log_base=base, heartbeat_s=60)
    yield running
    running.close()


def test_past_runs_are_listed_by_system_newest_first(archived):
    status, body = archived.rpc({"method": "launch.runs", "params": {"system": f"{PKG}:{SYSTEM}"}})
    assert status == 200
    runs = body["result"]["runs"]
    assert [r["run"] for r in runs] == ["20260102_000000", "20260101_000000"]
    assert runs[1]["mode"] == "Runtime" and runs[1]["compute_unit"] == "main_ecu" and runs[1]["deploy"] == "site_a"
    assert runs[1]["member_count"] == 2 and runs[1]["started"] == 1.0
    assert runs[0]["mode"] is None and runs[0]["member_count"] == 1
    status, body = archived.rpc({"method": "launch.runs"})
    assert [r["run"] for r in body["result"]["runs"]] == ["20260103_000000", "20260102_000000", "20260101_000000"]


def test_a_past_run_lists_its_members_and_tails_them(archived):
    status, body = archived.rpc({"method": "launch.run", "params": {"run": "20260101_000000"}})
    assert status == 200
    assert body["result"]["run"] == "20260101_000000" and body["result"]["mode"] == "Runtime"
    # A listed member without a directory never spawned and is left out.
    assert body["result"]["members"] == [{"path": "/demo/talker", "slug": "demo_talker"}]
    status, body = archived.rpc(
        {"method": "launch.run_tail", "params": {"run": "20260101_000000", "member": "demo_talker"}}
    )
    assert status == 200
    assert body["result"] == {"run": "20260101_000000", "member": "demo_talker", "out": ["demo_talker out"], "err": []}
    for params in ({"run": "../logs", "member": "demo_talker"}, {"run": "20260101_000000", "member": "a/b"}):
        status, body = archived.rpc({"method": "launch.run_tail", "params": params})
        assert status == 400 and body["error"]["code"] == api.INVALID_PARAMS, params
    status, body = archived.rpc({"method": "launch.run_tail", "params": {"run": "20260101_000000", "member": "nope"}})
    assert status == 404 and body["error"]["code"] == api.NOT_FOUND
    # A directory without a manifest is not a run, nor is a file.
    for name in ("20260104_000000", "stray"):
        status, body = archived.rpc({"method": "launch.run", "params": {"run": name}})
        assert status == 404, name


def test_a_past_run_reads_back_its_topic_rates(archived, tmp_path):
    from autoware_system_designer_runtime.rates_record import RATES_FILE, RatesWriter, Sample

    run = tmp_path / "logs" / "20260101_000000"
    writer = RatesWriter(run / RATES_FILE, interval_s=10)
    key = ("/demo/talker", "out", "/chatter")
    for second, count in ((0, 0), (10, 10), (20, 50)):
        writer.write(Sample(second * 1_000_000_000, {key: (count, 0, 0, second)}, processes=1))
    writer.close()

    status, body = archived.rpc({"method": "launch.runs", "params": {"system": f"{PKG}:{SYSTEM}"}})
    assert {r["run"]: r["rates"] for r in body["result"]["runs"]} == {
        "20260102_000000": False,
        "20260101_000000": True,
    }
    status, body = archived.rpc({"method": "launch.run_rates", "params": {"run": "20260101_000000"}})
    assert status == 200, body
    result = body["result"]
    assert result["times"] == [0, 10_000_000_000, 20_000_000_000] and result["interval_s"] == 20.0
    assert (result["start"], result["sample"]) == (0, 2) and result["density"] == [0.0, 1.0, 4.0]
    assert result["mode"] == "Runtime" and result["compute_unit"] == "main_ecu"
    assert result["nodes"][0]["outputs"] == [{"topic": "/chatter", "hz": 2.5}]
    status, body = archived.rpc(
        {"method": "launch.run_rates", "params": {"run": "20260101_000000", "sample": 1, "span_s": 10}}
    )
    assert body["result"]["sample"] == 1 and body["result"]["nodes"][0]["outputs"] == [{"topic": "/chatter", "hz": 1.0}]
    status, body = archived.rpc(
        {"method": "launch.run_rates", "params": {"run": "20260101_000000", "start": 1, "sample": 2}}
    )
    assert body["result"]["start"] == 1 and body["result"]["nodes"][0]["outputs"] == [{"topic": "/chatter", "hz": 4.0}]
    status, body = archived.rpc({"method": "launch.run_rates", "params": {"run": "20260101_000000", "start": -1}})
    assert status == 400 and body["error"]["code"] == api.INVALID_PARAMS
    # The record is read again once its file changed.
    writer = RatesWriter(run / RATES_FILE, interval_s=10)
    for second, count in ((0, 0), (10, 30)):
        writer.write(Sample(second * 1_000_000_000, {key: (count, 0, 0, second)}, processes=1))
    writer.close()
    status, body = archived.rpc({"method": "launch.run_rates", "params": {"run": "20260101_000000"}})
    assert body["result"]["nodes"][0]["outputs"] == [{"topic": "/chatter", "hz": 3.0}]
    status, body = archived.rpc({"method": "launch.run_rates", "params": {"run": "20260101_000000", "sample": -1}})
    assert status == 400 and body["error"]["code"] == api.INVALID_PARAMS
    status, body = archived.rpc({"method": "launch.run_rates", "params": {"run": "20260102_000000"}})
    assert status == 404 and body["error"]["code"] == api.NOT_FOUND
    status, body = archived.rpc({"method": "launch.run_rates", "params": {"run": "20260101_000000", "span_s": 0}})
    assert status == 400 and body["error"]["code"] == api.INVALID_PARAMS


def test_the_run_of_a_session_is_listed_once_the_session_exited(launching):
    _start(launching)
    session = _wait_session(launching, _greeted, "connect")
    run = Path(session["log_dir"]).name
    _, body = launching.rpc({"method": "launch.runs"})
    assert run not in [r["run"] for r in body["result"]["runs"]]
    launching.rpc({"method": "launch.shutdown", "params": {"compute_unit": "main_ecu"}})
    _wait_session(launching, lambda s: s["state"] == launch.SESSION_EXITED, "exit")
    _, body = launching.rpc({"method": "launch.runs", "params": {"system": f"{PKG}:{SYSTEM}"}})
    listed = [r for r in body["result"]["runs"] if r["run"] == run]
    assert listed and listed[0]["member_count"] == 2 and listed[0]["mode"] == "Runtime"
    _, body = launching.rpc({"method": "launch.run", "params": {"run": run}})
    assert body["result"]["members"] == [{"path": "/demo/talker", "slug": "demo_talker"}]
    status, body = launching.rpc({"method": "launch.run_tail", "params": {"run": run, "member": "demo_talker"}})
    assert status == 200 and body["result"]["out"] == ["hello 1", "hello 2"] and body["result"]["err"] == ["bye"]


def test_a_session_and_its_run_name_their_launch(launching):
    _, body = _start(launching)
    launch_id = body["result"]["launch"]
    assert launch.valid_launch_id(launch_id)
    session = _wait_session(launching, _greeted, "connect")
    assert session["launch"] == launch_id
    launching.rpc({"method": "launch.shutdown", "params": {"compute_unit": "main_ecu"}})
    _wait_session(launching, lambda s: s["state"] == launch.SESSION_EXITED, "exit")
    run = Path(session["log_dir"]).name
    _, body = launching.rpc({"method": "launch.runs", "params": {"system": f"{PKG}:{SYSTEM}"}})
    assert [r["launch"] for r in body["result"]["runs"] if r["run"] == run] == [launch_id]
    # No unit of the system runs: the next start opens a launch, or takes the one it names.
    _, body = _start(launching, launch="bench-1")
    assert body["result"]["launch"] == "bench-1"


def test_a_session_keeps_the_runtime_counts_of_its_members():
    count = launch.LaunchSession.count
    assert count({"path": "/a", "state": "running", "restarts": 3, "respawns": 2}) == {
        "path": "/a",
        "state": "running",
        "restarts": 3,
        "respawns": 2,
    }
    # A runtime without counts, or a malformed one, reads as no restarts.
    assert count({"path": "/a", "state": "running"})["restarts"] == 0
    assert count({"path": "/a", "restarts": "3", "respawns": -1}) == {"path": "/a", "restarts": 0, "respawns": 0}


def test_a_monitor_snapshot_far_over_64_kib_keeps_the_channel(launching):
    status, _ = _start(launching, mode="Runtime", args={"behaviour": "big"})
    assert status == 200
    _wait_session(launching, _greeted, "connection")
    status, body = launching.rpc({"method": "monitor.follow", "params": {"compute_unit": "main_ecu"}})
    assert status == 200, body
    deadline = time.time() + 5
    while time.time() < deadline and not launching.events.of(launch.TOPIC_MONITOR):
        time.sleep(0.02)
    (event,) = launching.events.of(launch.TOPIC_MONITOR)
    assert len(event["nodes"]) == 3000
    assert len(json.dumps(event)) > 64 * 1024
    status, body = launching.rpc({"method": "measure.status", "params": {"compute_unit": "main_ecu"}})
    assert status == 200  # still connected
    launching.rpc({"method": "launch.shutdown", "params": {"compute_unit": "main_ecu"}})
