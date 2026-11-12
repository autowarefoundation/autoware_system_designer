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

"""MultiUnitDemo on one daemon per compute unit, in both Docker topologies.

Tests run in file order on one environment per topology: the units are launched once, observed,
then shut down one by one. Where the environment is peered, every unit is driven through the daemon
of ``main_ecu``; elsewhere through its own.
"""

from __future__ import annotations

import pytest
from conftest import SYSTEM, UNITS, Environment, wait_for

pytestmark = pytest.mark.docker

# Listener of each unit and the unit publishing its topic.
LISTENERS = {
    "main_ecu": ("/report/listener", "sub_ecu_2"),
    "sub_ecu_1": ("/command/listener_1", "main_ecu"),
    "sub_ecu_2": ("/command/listener_2", "main_ecu"),
}
MIN_RATE_HZ = 0.5  # talkers publish at 1 Hz


def test_each_daemon_serves_its_unit(environment: Environment) -> None:
    for unit, daemon in environment.daemons.items():
        assert daemon.rpc("health")["served_units"] == [unit]
        peered = environment.peered and unit == "main_ecu"
        if peered:
            wait_for(
                lambda: all(peer["state"] == "connected" for peer in daemon.rpc("health")["peers"]),
                30,
                "peers connected",
            )
            peers = daemon.rpc("health")["peers"]
            assert sorted(u for peer in peers for u in peer["units"]) == ["sub_ecu_1", "sub_ecu_2"]
            assert [peer["mismatch"] for peer in peers] == [[], []]
        for other in UNITS:
            expected = "available" if other == unit or peered else "remote_unsupported"
            assert daemon.unit_status(other) == expected


def test_launch_runs_every_unit(launched: Environment) -> None:
    for unit in UNITS:
        daemon = launched.controller(unit)
        assert daemon.unit_status(unit) == "running"
        session = daemon.session(unit)
        assert session["connected"]
        assert session["job"].startswith(f"{unit}/") == (daemon.unit != unit)
    if launched.peered:
        main = launched.daemons["main_ecu"]
        assert sorted(s["compute_unit"] for s in main.rpc("launch.status")["sessions"]) == sorted(UNITS)
        assert sorted(job["params"]["compute_unit"] for job in main.rpc("jobs.list")["jobs"]) == sorted(UNITS)


def test_topics_cross_units(launched: Environment) -> None:
    for unit, (listener, _) in LISTENERS.items():
        daemon = launched.controller(unit)
        wait_for(lambda: daemon.input_rate(listener, unit=unit) >= MIN_RATE_HZ, 30, f"messages on {listener}", period=0)


def test_member_log_over_api(launched: Environment) -> None:
    for unit, (listener, _) in LISTENERS.items():
        daemon = launched.controller(unit)

        def heard() -> bool:
            tail = daemon.rpc("launch.follow", compute_unit=unit, path=listener)
            return any("I heard" in line for line in tail["out"] + tail["err"])

        try:
            wait_for(heard, 15, f"output of {listener}")
        finally:
            daemon.rpc("launch.unfollow", compute_unit=unit)


def test_paused_unit_silences_its_topics(launched: Environment) -> None:
    if launched.topology != "bridge":
        pytest.skip("units of the host topology share the host")
    main = launched.daemons["main_ecu"]
    listener, publisher = LISTENERS["main_ecu"]
    assert launched.compose("pause", publisher).returncode == 0
    try:
        wait_for(lambda: main.input_rate(listener) == 0.0, 30, f"silence on {listener}", period=0)
    finally:
        assert launched.compose("unpause", publisher).returncode == 0
    wait_for(lambda: main.input_rate(listener) >= MIN_RATE_HZ, 30, f"messages on {listener} again", period=0)
    assert launched.all_running()


def test_shutdown_stops_one_unit(launched: Environment) -> None:
    remaining = list(UNITS)
    for unit in ("sub_ecu_1", "sub_ecu_2", "main_ecu"):
        daemon = launched.controller(unit)
        daemon.shutdown(unit)
        wait_for(lambda: daemon.unit_status(unit) == "available", 30, f"{unit} available again")
        remaining.remove(unit)
        assert daemon.session(unit)["state"] == "exited"
        assert launched.processes(unit, "demo_nodes_cpp|autoware-system-designer-launch") == []
        for other in remaining:
            assert launched.controller(other).unit_status(other) == "running"
            assert launched.processes(other, "demo_nodes_cpp")


def test_past_runs_listed_per_unit(launched: Environment) -> None:
    for unit, daemon in launched.daemons.items():
        runs = daemon.rpc("launch.runs", system=SYSTEM)["runs"]
        listed = sorted(UNITS) if launched.peered and unit == "main_ecu" else [unit]
        assert sorted(run["compute_unit"] for run in runs) == listed
        for run in runs:
            assert run["run"].startswith(f"{run['compute_unit']}/") == (run["compute_unit"] != unit)
            members = daemon.rpc("launch.run", run=run["run"])["members"]
            assert members and daemon.rpc("launch.run_tail", run=run["run"], member=members[0]["slug"])["run"]
