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

"""Fixtures of the multi-unit Docker tests: one environment per topology, one client per unit."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional

import pytest

HERE = Path(__file__).resolve().parent.parent
RUN = HERE / "run.sh"
SYSTEM = "autoware_system_design_examples:MultiUnitDemo"
PORT_ENV = {"main_ecu": "ASD_PORT_MAIN_ECU", "sub_ecu_1": "ASD_PORT_SUB_ECU_1", "sub_ecu_2": "ASD_PORT_SUB_ECU_2"}
DEFAULT_PORT = {"main_ecu": 8781, "sub_ecu_1": 8782, "sub_ecu_2": 8783}
UNITS = tuple(DEFAULT_PORT)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "docker: needs a Docker daemon; skipped without one")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--keep", action="store_true", help="leave the containers up after the tests")


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if _docker_available():
        return
    skip = pytest.mark.skip(reason="no Docker daemon")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)


class RpcError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class Daemon:
    """Request client of one unit's daemon."""

    def __init__(self, unit: str):
        self.unit = unit
        self.port = int(os.environ.get(PORT_ENV[unit], DEFAULT_PORT[unit]))

    def rpc(self, method: str, timeout: float = 20.0, **params) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/rpc",
            data=json.dumps({"id": 1, "method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json", "X-System-Designer-Client": "multi-unit-test"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = json.load(response)
        except urllib.error.HTTPError as exc:
            reply = json.load(exc)
        if not reply["ok"]:
            raise RpcError(reply["error"]["code"], reply["error"]["message"])
        return reply["result"]

    def unit_status(self, unit: Optional[str] = None) -> str:
        system = next(s for s in self.rpc("systems.list")["systems"] if s["id"] == SYSTEM)
        return next(u["status"] for u in system["compute_units"] if u["name"] == (unit or self.unit))

    def session(self, unit: Optional[str] = None) -> Optional[dict]:
        try:
            sessions = self.rpc("launch.status", compute_unit=unit or self.unit)["sessions"]
        except RpcError as exc:
            if exc.code != "not_found":
                raise
            return None
        return sessions[0] if sessions else None

    def launch(self, unit: Optional[str] = None) -> None:
        self.rpc("launch.start", system=SYSTEM, compute_unit=unit or self.unit, mode="default")

    def shutdown(self, unit: Optional[str] = None) -> None:
        self.rpc("launch.shutdown", compute_unit=unit or self.unit)

    def input_rate(self, node: str, interval: float = 3.0, unit: Optional[str] = None) -> float:
        """Message rate on the only input of ``node``, 0 while the node reports none."""
        sample = self.rpc("monitor.sample", compute_unit=unit or self.unit, interval=interval)
        for entry in sample["nodes"]:
            if entry["node"] == node and entry["inputs"]:
                return entry["inputs"][0]["hz"]
        return 0.0


def wait_for(condition: Callable[[], bool], timeout: float, what: str, period: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")
        time.sleep(period)


class Environment:
    """One running topology; ``peered`` when the daemon of ``main_ecu`` serves the other units through theirs."""

    def __init__(self, topology: str):
        self.topology = topology
        self.peered = topology == "host"
        self.daemons: Dict[str, Daemon] = {unit: Daemon(unit) for unit in UNITS}

    def controller(self, unit: str) -> Daemon:
        """The daemon a client drives ``unit`` through."""
        return self.daemons["main_ecu" if self.peered else unit]

    def run(self, *args: str, timeout: float = 600.0) -> subprocess.CompletedProcess:
        return subprocess.run([str(RUN), *args], capture_output=True, text=True, timeout=timeout, check=False)

    def compose(self, *args: str) -> subprocess.CompletedProcess:
        return self.run("compose", self.topology, *args, timeout=120.0)

    def processes(self, unit: str, pattern: str) -> list:
        """Command lines in the unit's container matching ``pattern``."""
        result = self.compose("exec", "-T", unit, "pgrep", "-af", pattern)
        return [line for line in result.stdout.splitlines() if "pgrep" not in line]

    def all_running(self) -> bool:
        for unit in UNITS:
            session = self.controller(unit).session(unit)
            if session is None or session["state"] != "running" or not session["launch_ready"]:
                return False
            if any(member["state"] != "running" for member in session["members"]):
                return False
        return True


@pytest.fixture(scope="module", params=["host", "bridge"])
def environment(request: pytest.FixtureRequest) -> Iterator[Environment]:
    env = Environment(request.param)
    up = env.run("up", env.topology)
    if up.returncode != 0:
        env.run("down", env.topology)
        pytest.fail(f"run.sh up {env.topology} failed:\n{up.stdout}\n{up.stderr}")
    try:
        yield env
    finally:
        if not request.config.getoption("--keep"):
            env.run("down", env.topology)


@pytest.fixture(scope="module")
def launched(environment: Environment) -> Environment:
    """The environment with every unit launched and all members running."""
    for unit in UNITS:
        environment.controller(unit).launch(unit)
    wait_for(environment.all_running, 30, "every member running")
    return environment
