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

"""TypeAlpha on one daemon per compute unit, in every Docker topology of the system.

Tests run in file order on one environment per topology: the units are launched once, observed,
cut off and brought back where they are on a device of their own, then shut down one by one. Every
unit is driven through the main daemon, and what a test knows of a topology it reads from the
environment: the unit's service, its address, and whether it is behind an SSH forward.
"""

from __future__ import annotations

import pytest
from conftest import SYSTEM, UNITS, Environment, RpcError, wait_for

pytestmark = pytest.mark.docker

# Per unit, a node and one of its inputs published on another unit.
LISTENERS = {
    "main_ecu": ("/perception/traffic_light_recognition/classification", "/planning/mission_planning/route"),
    "sub_ecu_1": ("/localization", "/sensing/lidar/concatenated/pointcloud"),
    "sub_ecu_2": ("/planning", "/perception/object_recognition/objects"),
}
MIN_RATE_HZ = 5.0  # the inputs arrive at 10 Hz or more
# Processes a launch of the units leaves behind when it does not end cleanly.
NODES = "autoware_system_dummy_modules|component_container|robot_state_publisher"
# An SSH forward gone silent ends within 15 s and is tried again within 15 s.
LINK_TIMEOUT_S = 90


def _remote_only(environment: Environment, unit: str) -> None:
    if not environment.remote(unit):
        pytest.skip(f"{unit} shares the device of the main unit in topology {environment.topology}")


def _receives(environment: Environment, unit: str) -> bool:
    return environment.main.input_rate(unit, *LISTENERS[unit]) >= MIN_RATE_HZ


def _wait_status(environment: Environment, unit: str, status: str) -> None:
    wait_for(lambda: environment.main.unit_status(unit) == status, LINK_TIMEOUT_S, f"{unit} {status}")


def _assert_cut_off(environment: Environment, unit: str) -> None:
    """The main daemon reports ``unit`` unreachable, refuses requests for it and keeps the other units."""
    _wait_status(environment, unit, "unreachable")
    assert environment.peer(unit)["state"] == "unreachable"
    with pytest.raises(RpcError) as refused:
        environment.main.rpc("launch.status", compute_unit=unit)
    assert refused.value.code == "unavailable"
    for other in UNITS:
        if other != unit:
            assert environment.main.unit_status(other) == "running"


def test_main_daemon_serves_its_unit_and_has_the_others_as_peers(environment: Environment) -> None:
    main = environment.main
    health = main.rpc("health")
    assert health["served_units"] == [environment.main_unit]
    wait_for(
        lambda: all(peer["state"] == "connected" for peer in main.rpc("health")["peers"]),
        LINK_TIMEOUT_S,
        "peers connected",
    )
    peers = main.rpc("health")["peers"]
    assert sorted(unit for peer in peers for unit in peer["units"]) == sorted(set(UNITS) - {environment.main_unit})
    assert [peer["mismatch"] for peer in peers] == [[], []]
    for peer in peers:
        # A unit at another address than the main unit's is on another device, behind an SSH forward.
        address = environment.address[peer["units"][0]]
        remote = address != environment.address[environment.main_unit]
        assert peer["ssh"] == (address if remote else None)
        assert (peer["tunnel"] is not None) == remote
    for unit in UNITS:
        assert main.unit_status(unit) == "available"


def test_launch_runs_every_unit(launched: Environment) -> None:
    main = launched.main
    for unit in UNITS:
        assert main.unit_status(unit) == "running"
        session = main.session(unit)
        assert session["connected"]
        assert session["job"].startswith(f"{unit}/") == (unit != launched.main_unit)
    assert sorted(s["compute_unit"] for s in main.rpc("launch.status")["sessions"]) == sorted(UNITS)
    launches = [job for job in main.rpc("jobs.list")["jobs"] if job["kind"] == "launch"]
    assert sorted(job["params"]["compute_unit"] for job in launches) == sorted(UNITS)


def test_topics_cross_units(launched: Environment) -> None:
    for unit in UNITS:
        wait_for(lambda: _receives(launched, unit), 30, f"messages on {LISTENERS[unit][0]}", period=0)


def test_member_log_over_api(launched: Environment) -> None:
    main = launched.main
    for unit, (listener, _) in LISTENERS.items():

        def heard() -> bool:
            tail = main.rpc("launch.follow", compute_unit=unit, path=listener)
            return any("heard " in line for line in tail["out"] + tail["err"])

        try:
            wait_for(heard, 15, f"output of {listener}")
        finally:
            main.rpc("launch.unfollow", compute_unit=unit)


def test_slow_lossy_link_keeps_a_unit_reachable(launched: Environment) -> None:
    unit = "sub_ecu_2"
    _remote_only(launched, unit)
    service = launched.service[unit]
    netem = ("exec", "-T", "-u", "root", service, "tc", "qdisc")
    added = launched.compose(*netem, "add", "dev", "eth0", "root", "netem", "delay", "100ms", "loss", "5%")
    assert added.returncode == 0, added.stderr
    try:
        for _ in range(3):
            assert launched.main.session(unit)["connected"]
        wait_for(lambda: _receives(launched, unit), 30, f"messages on {LISTENERS[unit][0]}", period=0)
        assert launched.peer(unit)["state"] == "connected"
    finally:
        assert launched.compose(*netem, "del", "dev", "eth0", "root").returncode == 0


def test_frozen_unit_is_unreachable_then_resynced(launched: Environment) -> None:
    unit = "sub_ecu_2"
    _remote_only(launched, unit)
    assert launched.compose("pause", launched.service[unit]).returncode == 0
    try:
        # main_ecu listens to a topic the frozen unit publishes.
        wait_for(lambda: launched.main.input_rate("main_ecu", *LISTENERS["main_ecu"]) == 0.0, 30, "silence", period=0)
        _assert_cut_off(launched, unit)
    finally:
        assert launched.compose("unpause", launched.service[unit]).returncode == 0
    _wait_status(launched, unit, "running")
    wait_for(lambda: _receives(launched, "main_ecu"), 30, "messages again", period=0)
    assert launched.all_running()


def test_unit_off_the_network_is_unreachable_then_resynced(launched: Environment) -> None:
    unit = "sub_ecu_1"
    _remote_only(launched, unit)
    network, container = f"{launched.project}_vehicle", f"{launched.project}-{launched.service[unit]}-1"
    assert launched.docker("network", "disconnect", network, container).returncode == 0
    try:
        _assert_cut_off(launched, unit)
    finally:
        connected = launched.docker("network", "connect", "--ip", launched.address[unit], network, container)
        assert connected.returncode == 0, connected.stderr
    _wait_status(launched, unit, "running")
    assert launched.all_running()
    # Docker removed the unit's interface, which its DDS participants do not find again; a launch
    # started anew does.
    launched.main.shutdown(unit)
    _wait_status(launched, unit, "available")
    launched.main.launch(unit)
    wait_for(launched.all_running, 60, "every member running again")
    wait_for(lambda: _receives(launched, unit), 60, f"messages on {LISTENERS[unit][0]} again", period=0)


def test_killed_unit_comes_back_available(launched: Environment) -> None:
    unit = "sub_ecu_1"
    _remote_only(launched, unit)
    assert launched.compose("kill", launched.service[unit]).returncode == 0
    _assert_cut_off(launched, unit)
    started = launched.compose("start", launched.service[unit])
    assert started.returncode == 0, started.stderr
    # The unit's launch ended with its container; the daemon that answers again reports none.
    _wait_status(launched, unit, "available")
    assert launched.processes(unit, f"{NODES}|autoware-system-designer-launch") == []
    launched.main.launch(unit)
    wait_for(launched.all_running, 30, "every member running again")
    wait_for(lambda: _receives(launched, unit), 60, f"messages on {LISTENERS[unit][0]} again", period=0)


def test_shutdown_stops_one_unit(launched: Environment) -> None:
    main = launched.main
    remaining = list(UNITS)
    for unit in ("sub_ecu_1", "sub_ecu_2", "main_ecu"):
        main.shutdown(unit)
        wait_for(lambda: main.unit_status(unit) == "available", 30, f"{unit} available again")
        remaining.remove(unit)
        assert main.session(unit)["state"] == "exited"
        assert launched.processes(unit, f"{NODES}|autoware-system-designer-launch") == []
        for other in remaining:
            assert main.unit_status(other) == "running"
            assert launched.processes(other, NODES)


def test_past_runs_listed_per_unit(launched: Environment) -> None:
    main = launched.main
    runs = main.rpc("launch.runs", system=SYSTEM)["runs"]
    assert {run["compute_unit"] for run in runs} == set(UNITS)
    for run in runs:
        assert run["run"].startswith(f"{run['compute_unit']}/") == (run["compute_unit"] != launched.main_unit)
        members = main.rpc("launch.run", run=run["run"])["members"]
        assert members and main.rpc("launch.run_tail", run=run["run"], member=members[0]["slug"])["run"]
