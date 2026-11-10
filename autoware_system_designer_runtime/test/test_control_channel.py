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

"""Control channel and the command surface it shares with the console, without ROS."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoware_system_designer_runtime._impl.core import control_channel as cc
from autoware_system_designer_runtime._impl.core import events as ev
from autoware_system_designer_runtime._impl.core.commands import CommandError, MemberStates, RuntimeCommands
from autoware_system_designer_runtime._impl.core.control_channel import METHODS, ControlChannel
from autoware_system_designer_runtime._impl.core.regular_actor import _slug
from autoware_system_designer_runtime._impl.core.stdin_console import _dispatch
from autoware_system_designer_runtime._impl.ros2.operation_mode import MODE_AUTONOMOUS, OperationModeGuard, is_replayed


class _Handle:
    def __init__(self, name, calls):
        self.name = name
        self._calls = calls

    async def stop(self):
        self._calls.append(("stop", self.name))

    async def restart(self):
        self._calls.append(("restart", self.name))

    async def kill(self, signum):
        self._calls.append(("kill", self.name, signum))


class _Coord:
    """The coordinator surface the commands touch: names, handles, readiness, shutdown."""

    def __init__(self, names):
        self._names = list(names)
        self.calls = []
        self.launch_ready = asyncio.Event()
        self.shutdown_event = asyncio.Event()

    def names(self):
        return list(self._names)

    def handle(self, name):
        return _Handle(name, self.calls)

    def request_shutdown(self):
        self.shutdown_event.set()


class _Measure:
    state = "armed"

    def __init__(self):
        self.calls = []

    async def start(self):
        self.calls.append("start")
        self.state = "open"
        return "window open"

    async def close(self, reason):
        self.calls.append(("close", reason))
        self.state = "closed"
        return "window closed"

    async def analyze(self):
        self.calls.append("analyze")
        return "analyzed"

    def status(self):
        return f"state={self.state}"


class _Guard:
    def __init__(self, reason=None):
        self.reason = reason
        self.asked = []

    def check(self, action):
        self.asked.append(action)
        return self.reason


MEMBERS = ["/sensing/lidar/driver", "/perception/detector", "/pointcloud_container"]


def _commands(names=MEMBERS, **kwargs):
    coord = _Coord(names)
    return coord, RuntimeCommands(coord, MemberStates(), **kwargs)


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


# ---- member states ---------------------------------------------------------


def test_member_states_follow_actor_events():
    states = MemberStates()
    seen = []
    states.subscribe(seen.append)
    states.on_event(ev.Started(name="/n", pid=7))
    states.on_event(ev.Exited(name="/n", exit_code=1))
    states.on_event(ev.Respawning(name="/n", attempt=0, delay=1.0))
    states.on_event(ev.Started(name="/n", pid=8))
    states.on_event(ev.Terminated(name="/n"))
    states.on_event(ev.TaskFinished(name="x"))
    assert [(r.state, r.detail) for r in seen] == [
        ("running", {"pid": 7}),
        ("stopped", {"exit_code": 1}),
        ("respawning", {"attempt": 0, "delay": 1.0}),
        ("running", {"pid": 8}),
        ("done", {"pid": 8}),
    ]
    assert states.get("/n").state == "done" and states.get("/n").time > 0


def test_member_records_count_restarts_and_respawns_over_the_run():
    states = MemberStates()

    def counts(event):
        states.on_event(event)
        record = states.get(event.name)
        return record.restarts, record.respawns

    # The first start is no restart; every later start counts, respawned or not.
    assert counts(ev.Started(name="/n", pid=1)) == (0, 0)
    assert counts(ev.Exited(name="/n", exit_code=0)) == (0, 0)
    assert counts(ev.Started(name="/n", pid=2)) == (1, 0)
    assert counts(ev.Respawning(name="/n", attempt=0, delay=1.0)) == (1, 1)
    assert counts(ev.Started(name="/n", pid=3)) == (2, 1)
    assert counts(ev.Terminated(name="/n")) == (2, 1)
    # A composable node counts its loads.
    assert counts(ev.LoadStarted(name="/c")) == (0, 0)
    assert counts(ev.LoadSucceeded(name="/c", full_node_name="/c", unique_id=1)) == (0, 0)
    assert counts(ev.LoadStarted(name="/c")) == (1, 0)
    assert states.get("/n").as_dict()["restarts"] == 2


def test_a_loaded_composable_keeps_its_state_when_its_task_ends():
    states = MemberStates()
    states.on_event(ev.LoadStarted(name="/c/filter"))
    assert states.get("/c/filter").state == "loading"
    states.on_event(ev.LoadSucceeded(name="/c/filter", full_node_name="/c/filter", unique_id=3))
    states.on_event(ev.Terminated(name="/c/filter"))
    assert states.get("/c/filter").state == "loaded"
    states.on_event(ev.LoadFailed(name="/c/other", error="no plugin"))
    states.on_event(ev.Terminated(name="/c/other"))
    assert states.get("/c/other").state == "failed"


# ---- commands --------------------------------------------------------------


def test_status_lists_every_member_with_pending_before_any_event():
    coord, commands = _commands()
    commands._states.on_event(ev.Started(name="/perception/detector", pid=5))
    commands._states.on_event(ev.LoadSucceeded(name="/pointcloud_container/filter", full_node_name="/f", unique_id=1))
    status = commands.status()
    by_path = {m["path"]: m["state"] for m in status["members"]}
    assert by_path == {
        "/sensing/lidar/driver": "pending",
        "/perception/detector": "running",
        "/pointcloud_container": "pending",
        "/pointcloud_container/filter": "loaded",
    }
    assert status["launch_ready"] is False and status["shutting_down"] is False


def test_member_actions_match_substrings_and_reach_the_handles():
    coord, commands = _commands()
    assert _run(commands.member_action("stop", "/perception")) == ["/perception/detector"]
    assert _run(commands.member_action("restart", "/")) == MEMBERS
    assert _run(commands.member_action("kill", "container")) == ["/pointcloud_container"]
    assert coord.calls[0] == ("stop", "/perception/detector")
    assert [c[0] for c in coord.calls] == ["stop", "restart", "restart", "restart", "kill"]
    assert coord.calls[-1][2] == 9


def test_exact_member_actions_reach_the_named_member_only():
    coord, commands = _commands()
    assert _run(commands.member_action("stop", "/perception/detector", exact=True)) == ["/perception/detector"]
    with pytest.raises(CommandError) as partial:
        _run(commands.member_action("stop", "/perception", exact=True))
    assert partial.value.code == "not_found"
    assert coord.calls == [("stop", "/perception/detector")]


def test_member_actions_reject_missing_and_unmatched_patterns():
    coord, commands = _commands()
    with pytest.raises(CommandError) as missing:
        _run(commands.member_action("stop", ""))
    with pytest.raises(CommandError) as unmatched:
        _run(commands.member_action("stop", "/planning"))
    with pytest.raises(CommandError) as bad_action:
        _run(commands.member_action("pause", "/"))
    assert (missing.value.code, unmatched.value.code, bad_action.value.code) == (
        "invalid_params",
        "not_found",
        "invalid_params",
    )
    assert coord.calls == []


def test_guard_refusal_blocks_the_action_and_shutdown():
    guard = _Guard("stop refused: operation mode is autonomous")
    coord, commands = _commands(guard=guard)
    with pytest.raises(CommandError) as refused:
        _run(commands.member_action("stop", "/perception"))
    assert refused.value.code == "refused" and coord.calls == []
    with pytest.raises(CommandError):
        commands.shutdown()
    assert not coord.shutdown_event.is_set()
    assert guard.asked == ["stop", "shutdown"]
    guard.reason = None
    commands.shutdown()
    assert coord.shutdown_event.is_set()


def test_measure_verbs_drive_the_session_and_need_an_armed_one():
    coord, commands = _commands()
    with pytest.raises(CommandError) as unarmed:
        _run(commands.measure("start"))
    assert unarmed.value.code == "unavailable"

    session = _Measure()
    coord, commands = _commands(measure=session)
    assert _run(commands.measure("start")) == {"state": "open", "message": "window open"}
    assert _run(commands.measure("status"))["message"] == "state=open"
    assert _run(commands.measure("stop")) == {"state": "closed", "message": "window closed; analyzed"}
    assert session.calls == ["start", ("close", "control stop"), "analyze"]
    with pytest.raises(CommandError) as bad:
        _run(commands.measure("pause"))
    assert bad.value.code == "invalid_params"


def test_console_prints_results_and_refusals(capsys):
    guard = _Guard()
    coord, commands = _commands(guard=guard)
    _run(_dispatch(commands, "stop /perception"))
    guard.reason = "kill refused: operation mode is autonomous"
    _run(_dispatch(commands, "kill /sensing"))
    _run(_dispatch(commands, "status"))
    _run(_dispatch(commands, "measure start"))
    out = capsys.readouterr().out
    assert "[console] stop -> /perception/detector" in out
    assert "[console] kill refused: operation mode is autonomous" in out
    assert "/sensing/lidar/driver  pending" in out
    assert "measurement is not armed" in out
    assert coord.calls == [("stop", "/perception/detector")]


# ---- operation mode guard -------------------------------------------------


def test_operation_mode_guard_refuses_autonomous_and_transitions_only():
    guard = OperationModeGuard(worker=None)
    assert guard.check("stop") is None
    guard.observe(1)
    assert guard.check("shutdown") is None
    guard.observe(MODE_AUTONOMOUS)
    assert guard.check("stop") == "stop refused: operation mode is autonomous"
    assert guard.check("measure") is None
    guard.observe(1, in_transition=True)
    assert guard.check("restart") == "restart refused: operation mode is in transition"


def _state(mode, stamp_ns, in_transition=False):
    return SimpleNamespace(
        mode=mode,
        is_in_transition=in_transition,
        stamp=SimpleNamespace(sec=stamp_ns // 10**9, nanosec=stamp_ns % 10**9),
    )


def test_operation_mode_guard_ignores_replayed_reports():
    now = 1_800_000_000 * 10**9
    assert is_replayed(now - 3600 * 10**9, now)
    assert is_replayed(0, now)
    assert not is_replayed(now - 10**9, now)
    assert not is_replayed(now - 3600 * 10**9, None)
    guard = OperationModeGuard(worker=None)
    guard._on_message(_state(MODE_AUTONOMOUS, now - 3600 * 10**9), {"source_timestamp": now})
    assert guard.check("shutdown") is None
    guard._on_message(_state(1, now - 10**9), {"source_timestamp": now})
    guard._on_message(_state(MODE_AUTONOMOUS, now - 3600 * 10**9), {"source_timestamp": now})
    assert guard.check("shutdown") is None
    guard._on_message(_state(MODE_AUTONOMOUS, now), {"source_timestamp": now})
    assert guard.check("shutdown") == "shutdown refused: operation mode is autonomous"
    guard._on_message(_state(1, now - 3600 * 10**9), {"source_timestamp": None})
    assert guard.check("shutdown") is None


# ---- control channel -------------------------------------------------------


class _Channel:
    """A channel on a short socket path with one connected client."""

    def __init__(self, commands, states, log_dir):
        self.dir = Path(tempfile.mkdtemp(prefix="asd-ctl-"))
        self.channel = ControlChannel(commands, states, socket_path=self.dir / "ctl.sock", log_dir=log_dir)
        self.reader = None
        self.writer = None

    async def __aenter__(self):
        await self.channel.start()
        self.reader, self.writer = await asyncio.open_unix_connection(str(self.channel.path))
        return self

    async def __aexit__(self, *exc):
        self.writer.close()
        await self.channel.close()

    async def event(self):
        return json.loads(await asyncio.wait_for(self.reader.readline(), timeout=5))

    async def call(self, method, params=None, request_id=1):
        self.writer.write(json.dumps({"id": request_id, "method": method, "params": params or {}}).encode() + b"\n")
        await self.writer.drain()
        return await self.event()

    async def raw(self, line: bytes):
        self.writer.write(line)
        await self.writer.drain()
        return await self.event()


def test_channel_greets_with_members_and_forwards_state_changes(tmp_path):
    async def scenario():
        coord, commands = _commands()
        states = commands._states
        async with _Channel(commands, states, tmp_path) as c:
            hello = await c.event()
            assert hello["event"] == "hello" and hello["pid"] == os.getpid()
            assert hello["methods"] == list(METHODS) and hello["policy"] == "guard"
            assert [m["path"] for m in hello["members"]] == MEMBERS
            assert hello["measure_armed"] is False and hello["launch_ready"] is False
            assert hello["log_dir"] == str(tmp_path)
            assert stat.S_IMODE(os.stat(c.channel.path).st_mode) == 0o600

            states.on_event(ev.Started(name="/perception/detector", pid=42))
            member = await c.event()
            assert member == {
                "event": "member",
                "path": "/perception/detector",
                "state": "running",
                "detail": {"pid": 42},
                "time": member["time"],
                "restarts": 0,
                "respawns": 0,
            }

            reply = await c.call("status")
            assert reply["ok"] and reply["id"] == 1
            assert {m["path"]: m["state"] for m in reply["result"]["members"]}["/perception/detector"] == "running"
        assert not c.channel.path.exists()

    _run(scenario())


def test_channel_dispatches_member_measure_and_shutdown_methods(tmp_path):
    async def scenario():
        session = _Measure()
        coord, commands = _commands(measure=session)
        async with _Channel(commands, commands._states, tmp_path) as c:
            await c.event()
            assert (await c.call("stop", {"pattern": "/sensing"}))["result"] == {"members": ["/sensing/lidar/driver"]}
            assert (await c.call("kill", {"pattern": "container"}, request_id="k"))["id"] == "k"
            assert (await c.call("stop", {"pattern": "/sensing", "exact": True}))["error"]["code"] == "not_found"
            assert (await c.call("stop", {"pattern": "/sensing", "exact": "yes"}))["error"]["code"] == "invalid_params"
            assert coord.calls == [("stop", "/sensing/lidar/driver"), ("kill", "/pointcloud_container", 9)]
            assert (await c.call("measure.start"))["result"]["state"] == "open"
            assert (await c.call("measure.status"))["result"]["message"] == "state=open"
            assert (await c.call("shutdown"))["result"] == {"shutting_down": True}
            assert coord.shutdown_event.is_set()
            assert (await c.call("status"))["result"]["shutting_down"] is True

    _run(scenario())


def test_channel_answers_errors_in_the_envelope(tmp_path):
    async def scenario():
        coord, commands = _commands(guard=_Guard("stop refused: operation mode is autonomous"))
        async with _Channel(commands, commands._states, tmp_path) as c:
            await c.event()
            assert (await c.raw(b"not json\n"))["error"]["code"] == "bad_request"
            assert (await c.raw(b"[1,2]\n"))["error"]["code"] == "bad_request"
            assert (await c.call("rename"))["error"]["code"] == "unknown_method"
            assert (await c.call("stop"))["error"]["code"] == "invalid_params"
            assert (await c.call("stop", {"pattern": "/planning"}))["error"]["code"] == "not_found"
            refused = await c.call("stop", {"pattern": "/sensing"})
            assert refused == {
                "id": 1,
                "ok": False,
                "error": {"code": "refused", "message": "stop refused: operation mode is autonomous"},
            }
            assert (await c.call("measure.start"))["error"]["code"] == "unavailable"
            assert (await c.raw(json.dumps({"id": 2, "method": "status", "params": 3}).encode() + b"\n"))["error"][
                "code"
            ] == "invalid_params"
            assert (await c.raw(json.dumps({"id": 2, "method": 5}).encode() + b"\n"))["error"]["code"] == "bad_request"
            assert coord.calls == []

    _run(scenario())


def test_channel_tails_the_output_files_of_followed_members_only(tmp_path):
    async def scenario():
        coord, commands = _commands(["/sensing/lidar/driver", "/perception/detector"])
        node_dir = tmp_path / _slug("/sensing/lidar/driver")
        node_dir.mkdir()
        out = node_dir / "out"
        out.write_bytes(b"first line\nsecond line\npartial")
        other = tmp_path / _slug("/perception/detector")
        other.mkdir()
        async with _Channel(commands, commands._states, tmp_path) as c:
            await c.event()
            # Nothing is tailed before a follow: the status reply is the next line.
            (other / "out").write_bytes(b"unfollowed\n")
            await asyncio.sleep(cc.LOG_POLL_S * 2)
            assert (await c.call("status"))["ok"]
            assert c.channel._offsets == {}

            reply = await c.call("log.follow", {"path": "/sensing/lidar/driver"})
            assert reply["result"] == {"path": "/sensing/lidar/driver", "out": ["first line", "second line"], "err": []}
            with open(out, "ab") as f:
                f.write(b" end\nthird\n")
            event = await c.event()
            assert event == {
                "event": "member.log",
                "path": "/sensing/lidar/driver",
                "stream": "out",
                "lines": ["partial end", "third"],
            }
            (node_dir / "err").write_bytes(b"warn \xff\n")
            event = await c.event()
            assert event["stream"] == "err" and event["lines"] == ["warn �"]
            with open(other / "out", "ab") as f:
                f.write(b"still unfollowed\n")

            reply = await c.call("log.unfollow", {"path": "/sensing/lidar/driver"})
            assert reply["result"] == {"released": ["/sensing/lidar/driver"]}
            assert c.channel._offsets == {}
            with open(out, "ab") as f:
                f.write(b"after release\n")
            await asyncio.sleep(cc.LOG_POLL_S * 2)
            assert (await c.call("status"))["ok"]

            assert (await c.call("log.follow", {"path": "/planning"}))["error"]["code"] == "not_found"
            assert (await c.call("log.unfollow", {"path": 3}))["error"]["code"] == "invalid_params"
            await c.call("log.follow", {"path": "/sensing/lidar/driver"})
            assert (await c.call("log.unfollow"))["result"] == {"released": ["/sensing/lidar/driver"]}

    _run(scenario())


def test_follow_reports_the_tail_of_a_long_file_and_a_second_follower_joins_mid_stream(tmp_path):
    async def scenario():
        coord, commands = _commands(["/n"])
        node_dir = tmp_path / _slug("/n")
        node_dir.mkdir()
        out = node_dir / "out"
        out.write_bytes(b"".join(b"line %05d\n" % i for i in range(20000)))
        async with _Channel(commands, commands._states, tmp_path) as c:
            await c.event()
            tail = (await c.call("log.follow", {"path": "/n"}))["result"]["out"]
            assert len(tail) == cc.BACKFILL_LINES and tail[-1] == "line 19999" and tail[0] == "line 19800"
            with open(out, "ab") as f:
                f.write(b"more\n")
            assert (await c.event())["lines"] == ["more"]

            reader, writer = await asyncio.open_unix_connection(str(c.channel.path))
            try:
                await reader.readline()
                with open(out, "ab") as f:
                    f.write(b"pending\n")
                writer.write(json.dumps({"id": 1, "method": "log.follow", "params": {"path": "/n"}}).encode() + b"\n")
                await writer.drain()
                # The pending line reaches the first follower, then the second gets it inside its tail.
                assert (await c.event())["lines"] == ["pending"]
                second = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
                assert second["result"]["out"][-2:] == ["more", "pending"]
                with open(out, "ab") as f:
                    f.write(b"both\n")
                assert (await c.event())["lines"] == ["both"]
                assert json.loads(await asyncio.wait_for(reader.readline(), timeout=5))["lines"] == ["both"]
            finally:
                writer.close()
            # The first client's follow outlives the second client.
            await asyncio.sleep(cc.LOG_POLL_S)
            with open(out, "ab") as f:
                f.write(b"alone\n")
            assert (await c.event())["lines"] == ["alone"]

    _run(scenario())


def test_channel_reports_output_growth_per_window_while_a_client_is_connected(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "ACTIVITY_POLL_S", 0.1)

    async def scenario():
        coord, commands = _commands(["/a", "/b"])
        for name in ("/a", "/b"):
            (tmp_path / _slug(name)).mkdir()
        out = tmp_path / _slug("/a") / "out"
        async with _Channel(commands, commands._states, tmp_path) as c:
            await c.event()
            # The first scan is the baseline; growth after it is reported, members without any are left out.
            await asyncio.sleep(cc.ACTIVITY_POLL_S * 1.5)
            out.write_bytes(b"x" * 1000)
            (tmp_path / _slug("/a") / "err").write_bytes(b"y" * 24)
            event = await c.event()
            assert event["event"] == "member.activity"
            assert list(event["rates"]) == ["/a"] and event["rates"]["/a"] >= 1
            assert 0 < event["window"] < 1
            # A quiet window after a busy one is reported once, then nothing until the next growth.
            event = await c.event()
            assert event["event"] == "member.activity" and event["rates"] == {}
            await asyncio.sleep(cc.ACTIVITY_POLL_S * 3)
            assert (await c.call("status"))["ok"]
            # A truncated file counts its new size as growth.
            out.write_bytes(b"z" * 10)
            event = await c.event()
            assert event["event"] == "member.activity" and list(event["rates"]) == ["/a"]

    _run(scenario())


def test_a_client_stalled_past_the_byte_limit_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "QUEUE_BYTES", 1024)

    async def scenario():
        coord, commands = _commands(["/n"])
        states = commands._states
        async with _Channel(commands, states, tmp_path) as c:
            await c.event()
            for pid in range(50):  # never read by the client; the queue fills past the limit
                states.on_event(ev.Started(name="/n", pid=pid))
            await asyncio.sleep(0.1)
            assert c.channel._clients == set()

    _run(scenario())


def test_channel_replaces_a_stale_socket_file(tmp_path):
    async def scenario():
        coord, commands = _commands()
        c = _Channel(commands, commands._states, tmp_path)
        c.channel.path.write_text("stale")
        async with c:
            assert (await c.event())["event"] == "hello"

    _run(scenario())
