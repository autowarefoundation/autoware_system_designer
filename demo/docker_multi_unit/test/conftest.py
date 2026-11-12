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

"""Fixtures of the multi-unit Docker tests: one environment per topology, driven through its main daemon."""

from __future__ import annotations

import json
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
SYSTEM = "autoware_system_designer_demo_systems:TypeAlpha"
DEPLOY = "vehicle_x"
UNITS = ("main_ecu", "sub_ecu_1", "sub_ecu_2")


def _topologies() -> list:
    """Topologies of the system, one per compose file beside it."""
    listed = subprocess.run([str(RUN), "topologies"], capture_output=True, text=True, check=False)
    return listed.stdout.split()


TOPOLOGIES = _topologies()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "docker: needs a Docker daemon; skipped without one")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--keep", action="store_true", help="leave the containers of the last topology up after the tests")


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
    """Request client of the main daemon, which serves every unit."""

    def __init__(self, port: int):
        self.port = port

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

    def unit_status(self, unit: str) -> str:
        system = next(s for s in self.rpc("systems.list")["systems"] if s["id"] == SYSTEM)
        return next(u["status"] for u in system["compute_units"] if u["name"] == unit)

    def session(self, unit: str) -> Optional[dict]:
        try:
            sessions = self.rpc("launch.status", compute_unit=unit)["sessions"]
        except RpcError as exc:
            if exc.code != "not_found":
                raise
            return None
        return sessions[0] if sessions else None

    def launch(self, unit: str) -> None:
        self.rpc("launch.start", system=SYSTEM, compute_unit=unit, mode="default", deploy=DEPLOY)

    def shutdown(self, unit: str) -> None:
        self.rpc("launch.shutdown", compute_unit=unit)

    def input_rate(self, unit: str, node: str, topic: str, interval: float = 3.0) -> float:
        """Message rate of ``topic`` at ``node``, 0 while the node reports none."""
        sample = self.rpc("monitor.sample", compute_unit=unit, interval=interval)
        for entry in sample["nodes"]:
            if entry["node"] == node:
                return next((record["hz"] for record in entry["inputs"] if record["topic"] == topic), 0.0)
        return 0.0


def wait_for(condition: Callable[[], bool], timeout: float, what: str, period: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")
        time.sleep(period)


class Environment:
    """One running topology; its main daemon serves the other units through theirs."""

    def __init__(self, topology: str):
        self.topology = topology
        self.project = ""
        self.service: Dict[str, str] = {}
        self.address: Dict[str, str] = {}
        self.main_unit = ""
        self.main = Daemon(0)

    def read(self) -> None:
        """Project name and, per unit, service, address and role, as the topology binds them."""
        self.project = json.loads(self.compose("config", "--format", "json").stdout)["name"]
        listed = self.run("units", self.topology, timeout=120.0)
        assert listed.returncode == 0, listed.stderr
        for line in listed.stdout.splitlines():
            unit, service, port, address, role = line.split()
            self.service[unit] = service
            self.address[unit] = address
            if role == "main":
                self.main_unit, self.main = unit, Daemon(int(port))
        assert sorted(self.service) == sorted(UNITS) and self.main_unit

    def peer(self, unit: str) -> dict:
        """The main daemon's record of the peer serving ``unit``."""
        return next(peer for peer in self.main.rpc("health")["peers"] if unit in peer["units"])

    def remote(self, unit: str) -> bool:
        """Whether ``unit`` is on another device than the main unit, behind an SSH forward."""
        return unit != self.main_unit and self.peer(unit)["ssh"] is not None

    def docker(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60.0, check=False)

    def run(self, *args: str, timeout: float = 600.0) -> subprocess.CompletedProcess:
        return subprocess.run([str(RUN), *args], capture_output=True, text=True, timeout=timeout, check=False)

    def compose(self, *args: str) -> subprocess.CompletedProcess:
        return self.run("compose", self.topology, *args, timeout=120.0)

    def processes(self, unit: str, pattern: str) -> list:
        """Command lines in the unit's container matching ``pattern``."""
        result = self.compose("exec", "-T", self.service[unit], "pgrep", "-af", pattern)
        return [line for line in result.stdout.splitlines() if "pgrep" not in line]

    def all_running(self) -> bool:
        for unit in UNITS:
            session = self.main.session(unit)
            if session is None or session["state"] != "running" or not session["launch_ready"]:
                return False
            if any(member["state"] not in ("running", "loaded") for member in session["members"]):
                return False
        return True


@pytest.fixture(scope="module", params=TOPOLOGIES)
def environment(request: pytest.FixtureRequest) -> Iterator[Environment]:
    env = Environment(request.param)
    up = env.run("up", env.topology)
    if up.returncode != 0:
        env.run("down", env.topology)
        pytest.fail(f"run.sh up {env.topology} failed:\n{up.stdout}\n{up.stderr}")
    try:
        env.read()
        yield env
    finally:
        if not request.config.getoption("--keep"):
            env.run("down", env.topology)


@pytest.fixture(scope="module")
def launched(environment: Environment) -> Environment:
    """The environment with every unit launched and all members running."""
    for unit in UNITS:
        environment.main.launch(unit)
    wait_for(environment.all_running, 60, "every member running")
    return environment
