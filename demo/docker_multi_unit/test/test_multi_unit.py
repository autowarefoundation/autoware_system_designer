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

"""TypeAlpha on the daemons of its compute units, in every Docker topology of the system.

Tests run in file order on one environment per topology: the units are launched once, observed,
cut off and brought back where they are on a device of their own, then shut down one by one. Every
unit is driven through the main daemon, and what a test knows of a topology it reads from the
environment: the units, the service and device of each, its address, and the main daemon's units.
A fault is made on a device, and takes every unit of the device with it.
"""

from __future__ import annotations

import os

import pytest
from conftest import SYSTEM, Environment, RpcError, wait_for

pytestmark = pytest.mark.docker

# Per unit, a node and one of its inputs published on another unit.
LISTENERS = {
    "sensing_perception": ("/perception/traffic_light_recognition/classification", "/planning/mission_planning/route"),
    "map_localization": ("/localization", "/sensing/lidar/concatenated/pointcloud"),
    "planning_control": ("/planning", "/perception/object_recognition/objects"),
}
MIN_RATE_HZ = 5.0  # the inputs arrive at 10 Hz or more
# Processes a launch of the units leaves behind when it does not end cleanly.
NODES = "autoware_system_designer_demo_nodes|component_container|robot_state_publisher"
# An SSH forward gone silent ends within 15 s and is tried again within 15 s.
LINK_TIMEOUT_S = 90
# Fast DDS participants frozen past their discovery lease do not always match their peers again.
REMATCHES_AFTER_FREEZE = os.environ.get("ASD_RMW_IMPLEMENTATION") == "rmw_cyclonedds_cpp"


def _remote_device(environment: Environment, unit: str) -> str:
    """The device of ``unit``; the test is skipped when it is the main daemon's."""
    if not environment.remote(unit):
        pytest.skip(f"{unit} shares the device of the main daemon in topology {environment.topology}")
    return environment.device[unit]


def _receives(environment: Environment, unit: str) -> bool:
    return environment.main.input_rate(unit, *LISTENERS[unit]) >= MIN_RATE_HZ


def _wait_status(environment: Environment, units: list, status: str) -> None:
    for unit in units:
        wait_for(lambda: environment.main.unit_status(unit) == status, LINK_TIMEOUT_S, f"{unit} {status}")


def _relaunch(environment: Environment, units: list) -> None:
    """A new launch of ``units``, whose DDS participants are discovered anew."""
    for unit in units:
        environment.main.shutdown(unit)
    _wait_status(environment, units, "available")
    for unit in units:
        environment.main.launch(unit)
    wait_for(environment.all_running, 60, "every member running again")


def _assert_cut_off(environment: Environment, device: str) -> None:
    """The main daemon reports the device's units unreachable, refuses requests for them and keeps the others."""
    cut = environment.units_on(device)
    _wait_status(environment, cut, "unreachable")
    for unit in cut:
        assert environment.peer(unit)["state"] == "unreachable"
        with pytest.raises(RpcError) as refused:
            environment.main.rpc("launch.status", compute_unit=unit)
        assert refused.value.code == "unavailable"
    for other in environment.units:
        if other not in cut:
            assert environment.main.unit_status(other) == "running"


def test_main_daemon_serves_its_units_and_has_the_others_as_peers(environment: Environment) -> None:
    main = environment.main
    health = main.rpc("health")
    assert sorted(health["served_units"]) == sorted(environment.main_units)
    wait_for(
        lambda: all(peer["state"] == "connected" for peer in main.rpc("health")["peers"]),
        LINK_TIMEOUT_S,
        "peers connected",
    )
    peers = main.rpc("health")["peers"]
    others = sorted(set(environment.units) - set(environment.main_units))
    assert sorted(unit for peer in peers for unit in peer["units"]) == others
    # Units of one daemon share a link.
    assert len(peers) == len({environment.service[unit] for unit in others})
    for peer in peers:
        assert peer["mismatch"] == []
        for unit in peer["units"]:
            # A unit on another device than the main daemon's is behind an SSH forward from that device.
            assert peer["ssh"] == environment.ssh[unit]
            assert (peer["tunnel"] is not None) == environment.remote(unit)
    for unit in environment.units:
        assert main.unit_status(unit) == "available"


def test_launch_runs_every_unit(launched: Environment) -> None:
    main = launched.main
    for unit in launched.units:
        assert main.unit_status(unit) == "running"
        session = main.session(unit)
        assert session["connected"]
        assert session["job"].startswith(f"{unit}/") == (unit not in launched.main_units)
    assert sorted(s["compute_unit"] for s in main.rpc("launch.status")["sessions"]) == sorted(launched.units)
    launches = [job for job in main.rpc("jobs.list")["jobs"] if job["kind"] == "launch"]
    assert sorted(job["params"]["compute_unit"] for job in launches) == sorted(launched.units)


def test_topics_cross_units(launched: Environment) -> None:
    for unit in launched.units:
        wait_for(lambda: _receives(launched, unit), 30, f"messages on {LISTENERS[unit][0]}", period=0)


def test_member_log_over_api(launched: Environment) -> None:
    main = launched.main
    for unit in launched.units:
        listener = LISTENERS[unit][0]

        def heard() -> bool:
            tail = main.rpc("launch.follow", compute_unit=unit, path=listener)
            return any("heard " in line for line in tail["out"] + tail["err"])

        try:
            wait_for(heard, 15, f"output of {listener}")
        finally:
            main.rpc("launch.unfollow", compute_unit=unit)


def test_slow_lossy_link_keeps_a_unit_reachable(launched: Environment) -> None:
    unit = "planning_control"
    _remote_device(launched, unit)
    netem = ("exec", "-T", "-u", "root", launched.netns[unit], "tc", "qdisc")
    added = launched.compose(*netem, "add", "dev", "eth0", "root", "netem", "delay", "100ms", "loss", "5%")
    assert added.returncode == 0, added.stderr
    try:
        for _ in range(3):
            assert launched.main.session(unit)["connected"]
        wait_for(lambda: _receives(launched, unit), 30, f"messages on {LISTENERS[unit][0]}", period=0)
        assert launched.peer(unit)["state"] == "connected"
    finally:
        assert launched.compose(*netem, "del", "dev", "eth0", "root").returncode == 0


def test_frozen_device_is_unreachable_then_resynced(launched: Environment) -> None:
    unit = "planning_control"
    device = _remote_device(launched, unit)
    services = launched.services_on(device)
    assert launched.compose("pause", *services).returncode == 0
    try:
        # sensing_perception listens to a topic the frozen unit publishes.
        listener = "sensing_perception"
        wait_for(lambda: launched.main.input_rate(listener, *LISTENERS[listener]) == 0.0, 30, "silence", period=0)
        _assert_cut_off(launched, device)
    finally:
        assert launched.compose("unpause", *services).returncode == 0
    _wait_status(launched, launched.units_on(device), "running")
    assert launched.all_running()
    if not REMATCHES_AFTER_FREEZE:
        _relaunch(launched, launched.units_on(device))
    for each in ["sensing_perception", *launched.units_on(device)]:
        wait_for(lambda: _receives(launched, each), 60, f"messages on {LISTENERS[each][0]} again", period=0)


def test_device_off_the_network_is_unreachable_then_resynced(launched: Environment) -> None:
    unit = "map_localization"
    device = _remote_device(launched, unit)
    units = launched.units_on(device)
    network, container = f"{launched.project}_vehicle", f"{launched.project}-{launched.netns[unit]}-1"
    assert launched.docker("network", "disconnect", network, container).returncode == 0
    try:
        _assert_cut_off(launched, device)
    finally:
        connected = launched.docker("network", "connect", "--ip", launched.address[unit], network, container)
        assert connected.returncode == 0, connected.stderr
    _wait_status(launched, units, "running")
    assert launched.all_running()
    # Docker removed the device's interface, which its DDS participants do not find again; a launch
    # started anew does.
    _relaunch(launched, units)
    for each in units:
        wait_for(lambda: _receives(launched, each), 60, f"messages on {LISTENERS[each][0]} again", period=0)


def test_killed_device_comes_back_available(launched: Environment) -> None:
    unit = "map_localization"
    device = _remote_device(launched, unit)
    units, services = launched.units_on(device), launched.services_on(device)
    assert launched.compose("kill", *services).returncode == 0
    _assert_cut_off(launched, device)
    # The service holding the network namespace starts first.
    for service in services:
        started = launched.compose("start", service)
        assert started.returncode == 0, started.stderr
    # The units' launches ended with their containers; the daemons that answer again report none.
    _wait_status(launched, units, "available")
    for each in units:
        assert launched.processes(each, f"{NODES}|autoware-system-designer-launch") == []
    for each in units:
        launched.main.launch(each)
    wait_for(launched.all_running, 30, "every member running again")
    for each in units:
        wait_for(lambda: _receives(launched, each), 60, f"messages on {LISTENERS[each][0]} again", period=0)


def test_shutdown_stops_one_unit(launched: Environment) -> None:
    main = launched.main
    remaining = list(launched.units)
    # The main daemon's units last.
    for unit in sorted(launched.units, key=lambda unit: unit in launched.main_units):
        main.shutdown(unit)
        wait_for(lambda: main.unit_status(unit) == "available", 30, f"{unit} available again")
        remaining.remove(unit)
        assert main.session(unit)["state"] == "exited"
        # A unit sharing its daemon's container leaves the processes of the others there.
        if not launched.shares_service(unit, remaining):
            assert launched.processes(unit, f"{NODES}|autoware-system-designer-launch") == []
        for other in remaining:
            assert main.unit_status(other) == "running"
            assert launched.processes(other, NODES)


def test_past_runs_listed_per_unit(launched: Environment) -> None:
    main = launched.main
    runs = main.rpc("launch.runs", system=SYSTEM)["runs"]
    assert {run["compute_unit"] for run in runs} == set(launched.units)
    for run in runs:
        assert run["run"].startswith(f"{run['compute_unit']}/") == (run["compute_unit"] not in launched.main_units)
        members = main.rpc("launch.run", run=run["run"])["members"]
        assert members and main.rpc("launch.run_tail", run=run["run"], member=members[0]["slug"])["run"]
