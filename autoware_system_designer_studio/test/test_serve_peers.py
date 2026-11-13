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

"""Several daemons in one process, one per compute unit: the main one serves the others' units through them."""

import asyncio
import json
import socket
import threading
import time

import pytest

from autoware_system_designer_studio import cli
from autoware_system_designer_studio.serve import api, jobs, launch, peers
from autoware_system_designer_studio.serve.server import ServeDaemon

from .test_serve import PKG, SYSTEM, UNITS, _await_job_output, _install_tree, _Running
from .test_serve_launch import (  # noqa: F401 - fixture
    SHEBANG,
    _fake_launcher,
    _greeted,
    _launch_env,
    _Recording,
    _run_tree,
    run_dir,
)

SYSTEM_ID = f"{PKG}:{SYSTEM}"


class _Daemon(_Running):
    """A daemon on its own event loop thread, on a chosen port."""

    def __init__(self, install, port=0, **kwargs):
        self.loop = asyncio.new_event_loop()
        self.daemon = ServeDaemon(install, port=port, **kwargs)
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.port = asyncio.run_coroutine_threadsafe(self.daemon.start(), self.loop).result(5)
        self.events = _Recording(self.daemon.hub)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def call(self, method, **params):
        status, body = self.rpc({"id": 1, "method": method, "params": params})
        assert status == 200, body
        return body["result"]

    def error(self, method, **params):
        status, body = self.rpc({"id": 1, "method": method, "params": params})
        assert status != 200, body
        return body["error"]["code"]

    def launch(self, **params):
        """Starts a launch of a unit of this daemon; returns once its runtime handles signals."""
        session = self.call("launch.start", system=SYSTEM_ID, **params)
        _await_job_output(self, session["job"])
        return session

    def unit(self, name):
        (system,) = self.call("systems.list")["systems"]
        return next(unit for unit in system["compute_units"] if unit["name"] == name)

    def peer(self):
        (view,) = self.call("health")["peers"]
        return view


def _until(condition, what, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.02)
    pytest.fail(f"{what} did not come")


def _freeze(running):
    """Blocks the daemon's loop, as a frozen process; setting the returned event lets it run again."""
    release = threading.Event()
    running.loop.call_soon_threadsafe(release.wait, 60)
    return release


@pytest.fixture(autouse=True)
def quick_reconnects(monkeypatch):
    monkeypatch.setattr(peers, "RECONNECT_MIN_S", 0.05)
    monkeypatch.setattr(peers, "RECONNECT_MAX_S", 0.2)


class _Units:
    """A main daemon serving ``main_ecu`` and a peer serving ``sub_ecu``, over one install tree."""

    def __init__(self, tmp_path, *, sub_heartbeat_s=60):
        self.install = _install_tree(tmp_path, compute_units=UNITS)
        structures = self.install / PKG / "share" / PKG / "exports" / SYSTEM / "system_structure"
        (structures / "Simulation.json").write_text((structures / "Runtime.json").read_text())
        self.env = _launch_env(_fake_launcher(tmp_path))
        self.tmp_path = tmp_path
        self.sub_heartbeat_s = sub_heartbeat_s
        self.sub = self.start_sub()
        self.main = _Daemon(
            self.install,
            served_units=["main_ecu"],
            launch_env=self.env,
            heartbeat_s=60,
            log_base=tmp_path / "logs_main",
            peer_specs=[peers.PeerSpec("sub_ecu", self.sub.url)],
        )
        self.wait_peer(peers.PEER_CONNECTED)

    def start_sub(self, port=0, **kwargs):
        options = {"served_units": ["sub_ecu"], "launch_env": self.env, "heartbeat_s": self.sub_heartbeat_s}
        return _Daemon(self.install, port, log_base=self.tmp_path / "logs_sub", **{**options, **kwargs})

    def wait_peer(self, state):
        return _until(lambda: self.main.peer()["state"] == state and self.main.peer(), f"peer {state}")

    def launch_sub(self, **params):
        session = self.main.call("launch.start", system=SYSTEM_ID, compute_unit="sub_ecu", **params)
        _await_job_output(self.sub, session["job"].partition("/")[2])
        return session

    def close(self):
        self.main.close()
        if self.sub is not None:
            self.sub.close()


@pytest.fixture
def units(tmp_path, run_dir):  # noqa: F811 - the fixture of the same name
    running = _Units(tmp_path)
    yield running
    running.close()


# -- bindings ------------------------------------------------------------------


def test_a_peer_binding_names_a_unit_and_an_http_address():
    assert peers.parse_peer("sub_ecu=http://127.0.0.1:8782/") == peers.PeerSpec("sub_ecu", "http://127.0.0.1:8782")
    assert peers.parse_peer("sub_ecu=http://[::1]:8782").url == "http://[::1]:8782"
    for text in (
        "sub_ecu",
        "=http://127.0.0.1:1",
        "sub_ecu=https://127.0.0.1:1",
        "sub_ecu=http://127.0.0.1",
        "sub_ecu=http://127.0.0.1:1/api",
        "sub_ecu=http://user@127.0.0.1:1",
        "sub_ecu=http://127.0.0.1:port",
    ):
        with pytest.raises(peers.PeerConfigError):
            peers.parse_peer(text)


def test_the_site_file_binds_units_and_flags_replace_its_bindings(tmp_path, monkeypatch):
    site = tmp_path / "bench.site.yaml"
    site.write_text("peers:\n  sub_ecu: http://127.0.0.1:8782\n  sub_ecu_2: {url: 'http://127.0.0.1:8783'}\n")
    assert peers.load_site_file(site) == [
        peers.PeerSpec("sub_ecu", "http://127.0.0.1:8782"),
        peers.PeerSpec("sub_ecu_2", "http://127.0.0.1:8783"),
    ]
    monkeypatch.setenv(peers.SITE_FILE_ENV, str(site))
    args = cli._parse_args(["serve", "--peer", "sub_ecu=http://127.0.0.1:9000"])
    assert args.peer_specs == [
        peers.PeerSpec("sub_ecu", "http://127.0.0.1:9000"),
        peers.PeerSpec("sub_ecu_2", "http://127.0.0.1:8783"),
    ]
    other = tmp_path / "other.site.yaml"
    other.write_text("")
    assert cli._parse_args(["serve", "--site-file", str(other)]).peer_specs == []

    for content in ("- a\n", "peers: [a]\n", "peers:\n  sub_ecu: 8782\n", "peers:\n  sub_ecu: ftp://h:1\n", "peers: {"):
        other.write_text(content)
        with pytest.raises(peers.PeerConfigError):
            peers.load_site_file(other)
    with pytest.raises(peers.PeerConfigError):
        peers.load_site_file(tmp_path / "absent.site.yaml")


def test_a_unit_is_local_or_bound_to_a_peer_not_both(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli._parse_args(["serve", "--compute-unit", "sub_ecu", "--peer", "sub_ecu=http://127.0.0.1:1"])
    assert "both local and bound to a peer" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli._parse_args(["serve", "--peer", "sub_ecu=127.0.0.1:1"])
    with pytest.raises(peers.PeerConfigError):
        ServeDaemon(tmp_path, port=0, served_units=["a"], peer_specs=[peers.PeerSpec("a", "http://127.0.0.1:1")])


def test_a_peer_answers_on_this_loopback_at_another_port(tmp_path, capsys):
    with pytest.raises(peers.PeerConfigError, match="127.0.0.1"):
        peers.validate_specs([peers.PeerSpec("a", "http://10.0.0.2:8782")])
    with pytest.raises(SystemExit):
        cli._parse_args(["serve", "--port", "8781", "--peer", "a=http://localhost:8781"])
    assert "own port 8781" in capsys.readouterr().err
    with pytest.raises(peers.PeerConfigError, match="own port"):
        ServeDaemon(tmp_path, port=8781, peer_specs=[peers.PeerSpec("a", "http://127.0.0.1:8781", "ecu-a")])
    peers.validate_specs([peers.PeerSpec("a", "http://127.0.0.1:8782")], port=8781)


def test_units_naming_one_address_share_a_link(tmp_path):
    spec = [peers.PeerSpec("b", "http://127.0.0.1:1"), peers.PeerSpec("a", "http://127.0.0.1:1")]
    daemon = ServeDaemon(tmp_path, port=0, peer_specs=[*spec, peers.PeerSpec("c", "http://127.0.0.1:2")])
    assert [link.units for link in daemon.peers.links] == [["a", "b"], ["c"]]
    assert daemon.peers.link_of("b") is daemon.peers.links[0]
    assert daemon.peers.link_of_id("b/launch-3") == (daemon.peers.links[0], "launch-3")
    assert daemon.peers.link_of_id("launch-3") is None
    assert daemon.peers.link_of_id("z/launch-3") is None


def test_an_ssh_destination_binds_a_peer_on_this_loopback(tmp_path):
    site = tmp_path / "bench.site.yaml"
    site.write_text("peers:\n  a: {url: 'http://127.0.0.1:8782', ssh: ecu-a}\n  b: http://127.0.0.1:8783\n")
    args = cli._parse_args(["serve", "--site-file", str(site), "--peer-ssh", "b=asd@ecu-b"])
    assert args.peer_specs == [
        peers.PeerSpec("a", "http://127.0.0.1:8782", "ecu-a"),
        peers.PeerSpec("b", "http://127.0.0.1:8783", "asd@ecu-b"),
    ]
    for text in ("b", "=ecu", "b=", "b=-oProxyCommand=x", "b=ecu b"):
        with pytest.raises(peers.PeerConfigError):
            peers.parse_peer_ssh(text)
    with pytest.raises(peers.PeerConfigError, match="has no peer"):
        peers.with_ssh([], [("a", "ecu-a")])
    with pytest.raises(peers.PeerConfigError, match="127.0.0.1"):
        peers.validate_specs([peers.PeerSpec("a", "http://10.0.0.2:8782", "ecu-a")])
    with pytest.raises(peers.PeerConfigError, match="different SSH destinations"):
        peers.validate_specs(
            [peers.PeerSpec("a", "http://127.0.0.1:1", "ecu-a"), peers.PeerSpec("b", "http://127.0.0.1:1")]
        )
    site.write_text("peers:\n  a: {url: 'http://127.0.0.1:8782', ssh: 5}\n")
    with pytest.raises(peers.PeerConfigError):
        peers.load_site_file(site)


FAKE_SSH = """\
import signal, sys, time
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
print("argv: " + " ".join(sys.argv[1:]), flush=True)
while True:
    time.sleep(0.1)
"""


def test_a_forwarded_peer_keeps_an_ssh_tunnel_job_running(tmp_path, monkeypatch):
    ssh = tmp_path / "ssh"
    ssh.write_text(SHEBANG + FAKE_SSH)
    ssh.chmod(0o755)
    monkeypatch.setattr(peers, "SSH", str(ssh))
    monkeypatch.setattr(peers, "TUNNEL_RESTART_MIN_S", 0.05)
    install = _install_tree(tmp_path, compute_units=UNITS)
    sub = _Daemon(install, served_units=["sub_ecu"], heartbeat_s=60)
    spec = peers.PeerSpec("sub_ecu", sub.url, "ecu-sub")
    main = _Daemon(install, served_units=["main_ecu"], heartbeat_s=60, peer_specs=[spec])
    try:
        view = _until(lambda: main.peer()["tunnel"] and main.peer(), "tunnel job")
        assert view["ssh"] == "ecu-sub" and view["tunnel"] == "tunnel-1"
        _until(lambda: main.call("jobs.get", job="tunnel-1")["log"]["lines"], "tunnel output")
        got = main.call("jobs.get", job="tunnel-1")
        forward = f"127.0.0.1:{sub.port}:127.0.0.1:{sub.port}"
        assert got["log"]["lines"] == [
            "argv: -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=5 "
            f"-o ServerAliveCountMax=3 -L {forward} -- ecu-sub"
        ]
        assert got["job"]["kind"] == peers.TUNNEL_JOB_KIND and got["job"]["state"] == jobs.STATE_RUNNING
        assert got["job"]["params"] == {"destination": "ecu-sub", "port": sub.port}

        # A tunnel that ends is started again, and only the running one stays listed.
        main.call("jobs.cancel", job="tunnel-1")
        _until(lambda: main.peer()["tunnel"] == "tunnel-2", "tunnel restarted")
        assert [job["id"] for job in main.call("jobs.list")["jobs"]] == ["tunnel-2"]
        _await_job_output(main, "tunnel-2")
        process = main.daemon.jobs.get("tunnel-2").process
    finally:
        main.close()
        sub.close()
    assert process.returncode is not None


# -- status ----------------------------------------------------------------------


def test_a_peer_unit_reports_the_status_its_daemon_gives(units):
    health = units.main.call("health")
    assert health["peers"] == [
        {
            "units": ["sub_ecu"],
            "url": units.sub.url,
            "state": peers.PEER_CONNECTED,
            "error": None,
            "version": health["version"],
            "mismatch": [],
            "ssh": None,
            "tunnel": None,
        }
    ]
    assert health["heartbeat_s"] == 60
    unit = units.main.unit("sub_ecu")
    assert unit["status"] == "available" and unit["daemon"] == health["peers"][0]
    assert units.main.unit("main_ecu") == {
        "name": "main_ecu",
        "kind": "onboard",
        "description": "main",
        "status": "available",
    }
    assert units.main.unit("infra")["status"] == "offboard"
    # A daemon without the peer still names the unit as another daemon's.
    assert units.sub.unit("main_ecu")["status"] == "remote_unsupported"
    assert units.sub.call("health")["peers"] == []


def test_a_peer_differing_from_this_daemon_is_flagged(tmp_path, run_dir):  # noqa: F811
    install = _install_tree(tmp_path / "main", compute_units=UNITS)
    other = _install_tree(tmp_path / "other", compute_units=UNITS)
    manifest = other / PKG / "share" / PKG / "exports" / SYSTEM / "deployment.json"
    content = json.loads(manifest.read_text())
    content["mode_keys"] = ["Runtime"]
    content["deploy_variants"] = []
    manifest.write_text(json.dumps(content))
    # The peer serves another unit than the one bound to it.
    sub = _Daemon(other, served_units=["main_ecu"], heartbeat_s=60)
    main = _Daemon(install, served_units=["main_ecu"], heartbeat_s=60, peer_specs=[peers.PeerSpec("sub_ecu", sub.url)])
    try:
        _until(lambda: main.peer()["state"] == peers.PEER_CONNECTED, "peer connected")
        assert main.peer()["mismatch"] == [
            "does not serve compute unit sub_ecu",
            f"{SYSTEM_ID}: modes differ",
            f"{SYSTEM_ID}: deploy variants differ",
        ]
        assert main.unit("sub_ecu")["status"] == "remote_unsupported"
        # A rebuilt export on the peer is compared again.
        content["mode_keys"] = ["Runtime", "Simulation"]
        manifest.write_text(json.dumps(content))
        sub.loop.call_soon_threadsafe(sub.daemon.hub.publish, "systems.changed", {"package": PKG})
        _until(lambda: len(main.peer()["mismatch"]) == 2, "second comparison")
    finally:
        main.close()
        sub.close()


# -- routing ---------------------------------------------------------------------


def test_a_peer_unit_is_launched_followed_and_shut_down_through_this_daemon(units):
    main, sub = units.main, units.sub
    session = units.launch_sub(mode="Runtime", deploy="car_a", measure=True)
    assert session["compute_unit"] == "sub_ecu" and session["job"] == "sub_ecu/launch-1"
    assert sub.daemon.launch.session("sub_ecu").job.id == "launch-1"
    assert main.daemon.launch.sessions == {}
    assert main.error("launch.start", system=SYSTEM_ID, compute_unit="sub_ecu") == api.CONFLICT

    def connected():
        (listed,) = main.call("launch.status")["sessions"]
        return _greeted(listed) and listed

    listed = _until(connected, "connection")
    assert listed["job"] == "sub_ecu/launch-1" and listed["control"]["measure_armed"] is True
    assert {m["path"]: m["state"] for m in listed["members"]} == {
        "/demo/talker": "running",
        "/demo/listener": "pending",
    }
    assert main.call("launch.status", compute_unit="sub_ecu")["sessions"] == [listed]
    assert main.unit("sub_ecu")["status"] == "running"
    assert main.unit("main_ecu")["status"] == "available"

    # Events of the peer's unit arrive on this daemon's stream, job ids named as this daemon names them.
    _until(lambda: main.events.of(launch.TOPIC_MEMBER_ACTIVITY), "relayed member.activity")
    assert main.events.of(launch.TOPIC_MEMBER)[0] == sub.events.of(launch.TOPIC_MEMBER)[0]
    assert main.events.of(launch.TOPIC_MEMBER)[0]["compute_unit"] == "sub_ecu"
    assert {view["job"] for view in main.events.of(launch.TOPIC_LAUNCH)} == {"sub_ecu/launch-1"}
    assert main.events.of(launch.TOPIC_LAUNCH)[-1]["state"] == launch.SESSION_RUNNING
    assert {job["id"] for job in main.events.of(jobs.TOPIC_JOB)} == {"sub_ecu/launch-1"}
    _until(lambda: main.events.of(jobs.TOPIC_JOB_LOG), "relayed job.log")
    assert main.events.of(jobs.TOPIC_JOB_LOG)[0]["job"] == "sub_ecu/launch-1"

    # Jobs of the peer are listed and read by their prefixed id.
    assert [job["id"] for job in main.call("jobs.list")["jobs"]] == ["sub_ecu/launch-1"]
    got = main.call("jobs.get", job="sub_ecu/launch-1")
    assert got["job"]["id"] == "sub_ecu/launch-1" and got["job"]["params"]["compute_unit"] == "sub_ecu"
    assert any(line.startswith("argv: ") for line in got["log"]["lines"])
    assert main.error("jobs.get", job="sub_ecu/launch-9") == api.NOT_FOUND
    assert main.error("jobs.get", job="nope/launch-1") == api.NOT_FOUND
    assert main.error("jobs.get", job="launch-1") == api.NOT_FOUND

    # Unit-scoped methods act on the peer's runtime.
    assert main.call("launch.stop", compute_unit="sub_ecu", pattern="/demo/talker", exact=True) == {
        "members": ["/demo/talker"],
        "exact": True,
    }
    assert main.error("launch.stop", compute_unit="sub_ecu", pattern="/guarded") == api.REFUSED
    assert main.call("measure.status", compute_unit="sub_ecu") == {"state": "idle", "message": "measure idle"}
    assert main.call("monitor.sample", compute_unit="sub_ecu", interval=0.5)["nodes"][0]["node"] == "/demo/talker"
    assert main.error("launch.stop", compute_unit="main_ecu", pattern="/demo/talker") == api.NOT_FOUND

    # Follows made through this daemon end with its last event stream.
    assert main.call("launch.follow", compute_unit="sub_ecu", path="/demo/talker")["out"] == ["hello 1"]
    _until(lambda: main.events.of(launch.TOPIC_MEMBER_LOG), "relayed member.log")
    assert main.events.of(launch.TOPIC_MEMBER_LOG) == [
        {"compute_unit": "sub_ecu", "path": "/demo/talker", "stream": "out", "lines": ["hello 2"]}
    ]
    assert main.call("monitor.follow", compute_unit="sub_ecu", client="page-1") == {"interval_s": 2.0}
    _until(lambda: main.events.of(launch.TOPIC_MONITOR), "relayed monitor")
    peer_session = sub.daemon.launch.session("sub_ecu")
    assert peer_session.follows == {"/demo/talker"} and peer_session.monitors == {"page-1"}
    main.loop.call_soon_threadsafe(main.daemon.peers.release_follows)
    _until(lambda: not peer_session.follows and not peer_session.monitors, "released follows")

    assert main.call("launch.shutdown", compute_unit="sub_ecu") == {"shutting_down": True}
    _until(lambda: main.unit("sub_ecu")["status"] == "available", "exit of the peer's launch")
    (exited,) = main.call("launch.status")["sessions"]
    assert exited["state"] == launch.SESSION_EXITED
    assert main.events.of(jobs.TOPIC_JOB)[-1]["state"] == jobs.STATE_SUCCEEDED

    # The peer's past run is listed and read through this daemon.
    (run,) = main.call("launch.runs", system=SYSTEM_ID)["runs"]
    (peer_run,) = sub.call("launch.runs")["runs"]
    assert run == {**peer_run, "run": f"sub_ecu/{peer_run['run']}"}
    members = main.call("launch.run", run=run["run"])
    assert members["run"] == run["run"] and members["members"] == [{"path": "/demo/talker", "slug": "demo_talker"}]
    tail = main.call("launch.run_tail", run=run["run"], member="demo_talker")
    assert tail == {"run": run["run"], "member": "demo_talker", "out": ["hello 1", "hello 2"], "err": ["bye"]}
    assert main.error("launch.run", run="sub_ecu/absent") == api.NOT_FOUND
    assert main.error("launch.run", run=peer_run["run"]) == api.NOT_FOUND


def test_a_build_names_no_peer_unit(tmp_path):
    install = _install_tree(tmp_path, compute_units=UNITS)
    daemon = ServeDaemon(
        install, port=0, workspace=tmp_path, peer_specs=[peers.PeerSpec("sub_ecu", "http://127.0.0.1:1")]
    )
    with pytest.raises(api.ApiError) as info:
        asyncio.run(daemon._build_start({"system": SYSTEM_ID, "target": "workspace", "compute_unit": "sub_ecu"}))
    assert info.value.code == api.INVALID_PARAMS and "its own daemon" in info.value.message


def test_a_system_runs_one_mode_at_a_time_across_daemons(units):
    main = units.main
    nosocket = {"behaviour": "nosocket"}
    main.launch(compute_unit="main_ecu", mode="Runtime", args=nosocket)
    assert main.error("launch.start", system=SYSTEM_ID, compute_unit="sub_ecu", mode="Simulation") == api.CONFLICT
    assert units.sub.daemon.launch.sessions == {}
    units.launch_sub(args=nosocket)
    assert {s["compute_unit"]: s["mode"] for s in main.call("launch.status")["sessions"]} == {
        "main_ecu": "Runtime",
        "sub_ecu": "Runtime",
    }
    assert [job["id"] for job in main.call("jobs.list")["jobs"]] == ["sub_ecu/launch-1", "launch-1"]

    main.call("launch.shutdown", compute_unit="main_ecu")
    _until(lambda: main.unit("main_ecu")["status"] == "available", "exit of the local launch")
    # The peer's unit still runs the mode.
    assert main.error("launch.start", system=SYSTEM_ID, compute_unit="main_ecu", mode="Simulation") == api.CONFLICT
    assert main.call("launch.shutdown", compute_unit="sub_ecu") == {"shutting_down": True, "cancelled": True}
    _until(lambda: main.unit("sub_ecu")["status"] == "available", "exit of the peer's launch")
    main.launch(compute_unit="main_ecu", mode="Simulation", args=nosocket)
    assert main.error("launch.start", system=SYSTEM_ID, compute_unit="sub_ecu", mode="Runtime") == api.CONFLICT
    main.call("launch.shutdown", compute_unit="main_ecu")


def test_a_mode_is_refused_while_another_mode_of_the_system_starts(units):
    main = units.main
    nosocket = {"behaviour": "nosocket"}
    starting = main.daemon._starting
    starting[(SYSTEM_ID, "Runtime")] = ["launch-a", 1]
    assert main.error("launch.start", system=SYSTEM_ID, compute_unit="main_ecu", mode="Simulation") == api.CONFLICT
    assert main.error("launch.start", system=SYSTEM_ID, compute_unit="sub_ecu", mode="Simulation") == api.CONFLICT
    assert units.launch_sub(mode="Runtime", args=nosocket)["launch"] == "launch-a"
    del starting[(SYSTEM_ID, "Runtime")]
    main.call("launch.shutdown", compute_unit="sub_ecu")


def test_the_runs_of_peer_units_do_not_crowd_out_local_runs(units, monkeypatch):
    monkeypatch.setattr(launch, "MAX_RUNS", 1)
    base = units.tmp_path / "logs_main"
    _run_tree(base, "20260101_000000", {"system": SYSTEM, "compute_unit": "main_ecu", "started": 1.0})
    _run_tree(base, "20260102_000000", {"system": SYSTEM, "compute_unit": "sub_ecu", "started": 2.0})
    assert [run["run"] for run in units.main.call("launch.runs")["runs"]] == ["20260101_000000"]


def test_the_units_of_a_system_share_a_launch_until_all_ended(units):
    main, sub = units.main, units.sub
    nosocket = {"behaviour": "nosocket"}
    first = main.launch(compute_unit="main_ecu", args=nosocket)["launch"]
    assert units.launch_sub(args=nosocket)["launch"] == first
    assert sub.daemon.launch.session("sub_ecu").launch_id == first

    # A unit started again while another still runs stays in the launch.
    main.call("launch.shutdown", compute_unit="sub_ecu")
    _until(lambda: main.unit("sub_ecu")["status"] == "available", "exit of the peer's launch")
    assert units.launch_sub(args=nosocket)["launch"] == first
    main.call("launch.shutdown", compute_unit="main_ecu")
    _until(lambda: main.unit("main_ecu")["status"] == "available", "exit of the local launch")
    assert main.launch(compute_unit="main_ecu", args=nosocket)["launch"] == first

    for unit in ("main_ecu", "sub_ecu"):
        main.call("launch.shutdown", compute_unit=unit)
        _until(lambda: main.unit(unit)["status"] == "available", f"exit of {unit}")
    assert main.launch(compute_unit="main_ecu", args=nosocket)["launch"] != first
    main.call("launch.shutdown", compute_unit="main_ecu")


# -- faults ----------------------------------------------------------------------


def test_a_peer_that_goes_away_is_unreachable_until_it_answers_again(units):
    main = units.main
    units.launch_sub(args={"behaviour": "nosocket"})
    port = units.sub.port
    units.sub.close()
    units.sub = None
    view = units.wait_peer(peers.PEER_UNREACHABLE)
    assert view["error"]
    unit = main.unit("sub_ecu")
    assert unit["status"] == "unreachable" and unit["daemon"]["state"] == peers.PEER_UNREACHABLE
    assert {"peer": view} in main.events.of(peers.TOPIC_DAEMON)
    assert main.events.of(peers.TOPIC_SYSTEMS_CHANGED)[-1] == {"peer": view["url"]}

    assert main.error("launch.start", system=SYSTEM_ID, compute_unit="sub_ecu") == api.UNAVAILABLE
    assert main.error("launch.shutdown", compute_unit="sub_ecu") == api.UNAVAILABLE
    assert main.error("jobs.get", job="sub_ecu/launch-1") == api.UNAVAILABLE
    # Lists leave the peer out, but for the session it last reported: the launch it ended while closing.
    stale = main.call("launch.status")["sessions"]
    assert [(s["compute_unit"], s["state"], s["stale"], s["members"]) for s in stale] == [
        ("sub_ecu", "exited", True, [])
    ]
    assert main.call("launch.status", compute_unit="sub_ecu")["sessions"] == stale
    assert main.call("jobs.list")["jobs"] == []
    assert main.call("launch.runs")["runs"] == []

    # A daemon answering again on the address is read anew; the stopped one ended its launch.
    units.sub = units.start_sub(port)
    assert units.wait_peer(peers.PEER_CONNECTED)["error"] is None
    assert main.unit("sub_ecu")["status"] == "available"
    assert units.launch_sub(mode="Simulation", args={"behaviour": "nosocket"})["job"] == "sub_ecu/launch-1"
    main.call("launch.shutdown", compute_unit="sub_ecu")


def test_a_connected_peer_that_gives_no_status_is_listed_as_last_reported(units, monkeypatch):
    main = units.main
    units.launch_sub(mode="Runtime", args={"behaviour": "nosocket"})
    rpc = peers.PeerClient.rpc

    async def no_status(self, method, params=None, *, timeout):
        if method == "launch.status":
            raise peers.PeerUnreachable("no answer")
        return await rpc(self, method, params, timeout=timeout)

    monkeypatch.setattr(peers.PeerClient, "rpc", no_status)
    for params in ({}, {"compute_unit": "sub_ecu"}):
        listed = main.call("launch.status", **params)["sessions"]
        assert [(s["compute_unit"], s["mode"], s["stale"]) for s in listed] == [("sub_ecu", "Runtime", True)]
    assert main.peer()["state"] == peers.PEER_CONNECTED
    monkeypatch.setattr(peers.PeerClient, "rpc", rpc)
    main.call("launch.shutdown", compute_unit="sub_ecu")


def test_a_change_of_the_peers_own_links_is_not_relayed(units):
    main, sub = units.main, units.sub
    before = len(main.events.of(peers.TOPIC_SYSTEMS_CHANGED))
    publish = sub.daemon.hub.publish
    sub.loop.call_soon_threadsafe(publish, peers.TOPIC_SYSTEMS_CHANGED, {"peer": main.url})
    sub.loop.call_soon_threadsafe(publish, peers.TOPIC_SYSTEMS_CHANGED, {"job": "build-1", "package": PKG})
    _until(lambda: len(main.events.of(peers.TOPIC_SYSTEMS_CHANGED)) > before, "relayed systems.changed")
    time.sleep(0.3)
    assert main.events.of(peers.TOPIC_SYSTEMS_CHANGED)[before:] == [{"peer": sub.url}]


def test_a_reconnected_peer_keeps_its_launch_and_the_rate_followers(units, monkeypatch):
    main, sub = units.main, units.sub
    units.launch_sub()
    _until(lambda: _greeted(main.call("launch.status")["sessions"][0]), "connection")
    main.call("monitor.follow", compute_unit="sub_ecu", client="page-1")
    main.call("launch.follow", compute_unit="sub_ecu", path="/demo/talker")
    peer_session = sub.daemon.launch.session("sub_ecu")
    assert peer_session.monitors == {"page-1"}

    # The peer loses the stream of this daemon and with it every follow.
    link = main.daemon.peers.links[0]
    main.loop.call_soon_threadsafe(lambda: link._stream.close())
    units.wait_peer(peers.PEER_UNREACHABLE)
    _until(lambda: not peer_session.follows, "follows dropped by the peer")
    units.wait_peer(peers.PEER_CONNECTED)
    _until(lambda: peer_session.monitors == {"page-1"}, "rate follow made again")
    assert peer_session.follows == set()
    assert main.unit("sub_ecu")["status"] == "running"
    assert main.call("launch.status")["sessions"][0]["job"] == "sub_ecu/launch-1"
    main.call("launch.shutdown", compute_unit="sub_ecu")
    _until(lambda: main.unit("sub_ecu")["status"] == "available", "exit of the peer's launch")


def test_a_silent_peer_is_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(peers, "AGGREGATE_TIMEOUT_S", 0.2)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    install = _install_tree(tmp_path, compute_units=UNITS)
    spec = peers.PeerSpec("sub_ecu", f"http://127.0.0.1:{listener.getsockname()[1]}")
    main = _Daemon(install, served_units=["main_ecu"], heartbeat_s=60, peer_specs=[spec])
    try:
        view = _until(lambda: main.peer()["state"] == peers.PEER_UNREACHABLE and main.peer(), "peer unreachable")
        assert "did not answer health" in view["error"]
        assert main.unit("sub_ecu")["status"] == "unreachable"
        assert main.error("launch.shutdown", compute_unit="sub_ecu") == api.UNAVAILABLE
    finally:
        main.close()
        listener.close()


def test_a_peer_whose_stream_falls_silent_is_dropped_and_found_again(tmp_path, run_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(peers, "STREAM_IDLE_MIN_S", 1.0)
    running = _Units(tmp_path, sub_heartbeat_s=0.05)
    thawed = threading.Event()
    try:
        running.launch_sub(mode="Runtime", args={"behaviour": "nosocket"})
        # A frozen peer: its loop sends nothing for longer than the idle limit.
        thawed = _freeze(running.sub)
        view = running.wait_peer(peers.PEER_UNREACHABLE)
        assert "silent" in view["error"]
        # The mode it last reported still holds the system.
        start = {"system": SYSTEM_ID, "compute_unit": "main_ecu", "mode": "Simulation"}
        assert running.main.error("launch.start", **start) == api.CONFLICT
        assert running.main.error("launch.forget", compute_unit="main_ecu") == api.INVALID_PARAMS
        thawed.set()
        running.wait_peer(peers.PEER_CONNECTED)
        assert running.main.unit("sub_ecu")["status"] == "running"
        running.main.call("launch.shutdown", compute_unit="sub_ecu")
    finally:
        thawed.set()
        running.close()


def test_the_last_reported_session_of_an_unanswering_peer_is_forgotten(tmp_path, run_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(peers, "STREAM_IDLE_MIN_S", 1.0)
    running = _Units(tmp_path, sub_heartbeat_s=0.05)
    main = running.main
    nosocket = {"behaviour": "nosocket"}
    thawed = threading.Event()
    try:
        running.launch_sub(mode="Runtime", args=nosocket)
        assert main.error("launch.forget", compute_unit="sub_ecu") == api.CONFLICT
        thawed = _freeze(running.sub)
        running.wait_peer(peers.PEER_UNREACHABLE)
        start = {"system": SYSTEM_ID, "compute_unit": "main_ecu", "mode": "Simulation", "args": nosocket}
        assert main.error("launch.start", **start) == api.CONFLICT
        stale = main.call("launch.status")["sessions"]
        assert [(s["compute_unit"], s["mode"], s["stale"], s["members"]) for s in stale] == [
            ("sub_ecu", "Runtime", True, [])
        ]

        forgotten = main.call("launch.forget", compute_unit="sub_ecu")["forgotten"]
        assert [(s["compute_unit"], s["mode"], s["stale"]) for s in forgotten] == [("sub_ecu", "Runtime", True)]
        dropped = main.events.of(launch.TOPIC_LAUNCH)[-1]
        assert dropped["forgotten"] and dropped["compute_unit"] == "sub_ecu" and "members" not in dropped
        assert main.call("launch.status")["sessions"] == []
        assert main.call("launch.forget", compute_unit="sub_ecu") == {"forgotten": []}
        main.launch(compute_unit="main_ecu", mode="Simulation", args=nosocket)
        main.call("launch.shutdown", compute_unit="main_ecu")
        # The peer answering again reports the session it still has.
        thawed.set()
        running.wait_peer(peers.PEER_CONNECTED)
        assert main.unit("sub_ecu")["status"] == "running"
        main.call("launch.shutdown", compute_unit="sub_ecu")
    finally:
        thawed.set()
        running.close()
