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

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from autoware_system_designer_studio.serve import api, jobs, launch

from .test_serve import PKG, SYSTEM, UNITS, _install_tree, _Running
from .test_serve_jobs import _wait_job

# A runtime stand-in: serves the control protocol on the --control socket. Behaviour switches come
# in as system arguments (``--arg behaviour=...``): ``nosocket`` never serves the socket.
FAKE_RUNTIME = """#!/usr/bin/env python3
import asyncio, json, os, signal, sys

args = sys.argv[1:]
sock = args[args.index("--control") + 1]
behaviour = next((a.split("=", 1)[1] for a in args if a.startswith("behaviour=")), "")
METHODS = ["status", "stop", "restart", "kill", "measure.start", "measure.stop", "measure.status", "shutdown"]
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
              "measure_armed": "--measure" in args, "members": members, "launch_ready": True,
              "shutting_down": False})
        send({"event": "member", "path": "/demo/talker", "state": "running", "detail": {"pid": 1}, "time": 1.0})
        send({"event": "member.log", "path": "/demo/talker", "stream": "out", "lines": ["hello 1"]})
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
                result = {"members": [pattern]}
            elif method == "measure.status":
                result = {"state": "idle", "message": "measure idle"}
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
    script.write_text(FAKE_RUNTIME)
    script.chmod(0o755)
    return bin_dir


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
    return running.rpc(
        {
            "id": 1,
            "method": "launch.start",
            "params": {"system": f"{PKG}:{SYSTEM}", "compute_unit": "main_ecu", **params},
        }
    )


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

    session = _wait_session(launching, lambda s: s["connected"], "connection")
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
    assert argv[8:] == ["--deploy", "car_a", "--arg", "behaviour=", "--arg", "x=1", "--measure"]
    assert job["result"]["job"]["params"]["measure"] is True

    deadline = time.time() + 5
    while time.time() < deadline and not launching.events.of(launch.TOPIC_MEMBER_LOG):
        time.sleep(0.02)
    member_events = launching.events.of(launch.TOPIC_MEMBER)
    assert {
        "compute_unit": "main_ecu",
        "system": f"{PKG}:{SYSTEM}",
        "path": "/demo/talker",
        "state": "running",
        "detail": {"pid": 1},
        "time": 1.0,
    } in member_events
    assert launching.events.of(launch.TOPIC_MEMBER_LOG) == [
        {"compute_unit": "main_ecu", "path": "/demo/talker", "stream": "out", "lines": ["hello 1"]}
    ]
    launches = launching.events.of(launch.TOPIC_LAUNCH)
    assert [e["state"] for e in launches][:2] == [launch.SESSION_STARTING, launch.SESSION_RUNNING]
    assert "members" not in launches[0]

    status, body = launching.rpc(
        {"method": "launch.stop", "params": {"compute_unit": "main_ecu", "pattern": "/demo/listener"}}
    )
    assert status == 200 and body["result"] == {"members": ["/demo/listener"]}
    session = _wait_session(launching, lambda s: s["members"][1]["state"] == "stopped", "stop")
    assert session["members"][1]["detail"] == {"exit_code": 0}

    status, body = launching.rpc(
        {"method": "launch.kill", "params": {"compute_unit": "main_ecu", "pattern": "/guarded"}}
    )
    assert status == 409
    assert body["error"] == {"code": api.REFUSED, "message": "stop refused: operation mode is autonomous"}

    status, body = launching.rpc({"method": "measure.status", "params": {"compute_unit": "main_ecu"}})
    assert status == 200 and body["result"] == {"state": "idle", "message": "measure idle"}

    status, body = launching.rpc({"method": "launch.shutdown", "params": {"compute_unit": "main_ecu"}})
    assert status == 200 and body["result"] == {"shutting_down": True}
    result = _wait_job(launching, "launch-1")
    assert result["job"]["state"] == jobs.STATE_SUCCEEDED
    session = _wait_session(launching, lambda s: s["state"] == launch.SESSION_EXITED, "exit")
    assert session["connected"] is False and session["exit_code"] == 0
    assert not Path(job["result"]["job"]["detail"]["socket"]).exists()
    _, listed = launching.json("GET", "/api/systems")
    assert listed["result"]["systems"][0]["compute_units"][0]["status"] == "available"

    for method in ("launch.stop", "measure.start"):
        status, body = launching.rpc({"method": method, "params": {"compute_unit": "main_ecu", "pattern": "x"}})
        assert status == 404, method
    status, body = launching.rpc({"method": "launch.status", "params": {"compute_unit": "sub_ecu"}})
    assert status == 404


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


def test_daemon_stop_ends_a_running_launch(tmp_path, run_dir):
    install = _install_tree(tmp_path, compute_units=UNITS)
    running = _Running(install, launch_env=_launch_env(_fake_launcher(tmp_path)), heartbeat_s=60)
    status, body = _start(running)
    assert status == 200, body
    _wait_session(running, lambda s: s["connected"], "connection")
    job = running.daemon.jobs.get("launch-1")
    running.close()
    assert job.state == jobs.STATE_CANCELLED
    assert not list(run_dir.glob("**/*.sock"))
