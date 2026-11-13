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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402

SYSTEM = "autoware_system_designer_demo_systems:TypeAlpha"
DEPLOY = "vehicle_x"
TOPOLOGIES = run.topologies()


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


def pytest_collection_finish(session: pytest.Session) -> None:
    last = session.items[-1] if session.items else None
    session.config.last_topology = getattr(last, "callspec", None) and last.callspec.params.get("environment")


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
        bindings = run.Topology(topology)
        self.project = bindings.project
        self.units = [unit.name for unit in bindings.units]
        self.service = {unit.name: unit.service for unit in bindings.units}
        self.address = {unit.name: unit.address for unit in bindings.units}
        self.device = {unit.name: unit.device for unit in bindings.units}
        self.netns = {unit.name: unit.netns for unit in bindings.units}
        # SSH destination of the units on another device than the main daemon's, None for the others.
        self.ssh = {unit.name: unit.destination if bindings.remote(unit) else None for unit in bindings.units}
        self.main_units = [unit.name for unit in bindings.units if unit.main]
        self.main = Daemon(bindings.main.port)

    def peer(self, unit: str) -> dict:
        """The main daemon's record of the peer serving ``unit``."""
        return next(peer for peer in self.main.rpc("health")["peers"] if unit in peer["units"])

    def remote(self, unit: str) -> bool:
        """Whether ``unit`` is on another device than the main daemon, behind an SSH forward."""
        return self.ssh[unit] is not None

    def units_on(self, device: str) -> list:
        return [unit for unit in self.units if self.device[unit] == device]

    def services_on(self, device: str) -> list:
        """Services of a device, the one holding its network namespace first."""
        services = {self.service[unit]: self.netns[unit] for unit in self.units_on(device)}
        return sorted(services, key=lambda service: service != services[service])

    def shares_service(self, unit: str, others: list) -> bool:
        """Whether one of ``others`` runs in the service of ``unit``, under the same daemon."""
        return any(self.service[other] == self.service[unit] for other in others if other != unit)

    def docker(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60.0, check=False)

    def run(self, command: str) -> subprocess.CompletedProcess:
        """``run.py up`` or ``down`` of the topology."""
        argv = [sys.executable, run.__file__, command, self.topology]
        return subprocess.run(argv, capture_output=True, text=True, timeout=600.0, check=False)

    def compose(self, *args: str) -> subprocess.CompletedProcess:
        return run.compose(self.topology, *args, capture_output=True, text=True, timeout=120.0)

    def processes(self, unit: str, pattern: str) -> list:
        """Command lines in the unit's container matching ``pattern``."""
        result = self.compose("exec", "-T", self.service[unit], "pgrep", "-af", pattern)
        return [line for line in result.stdout.splitlines() if "pgrep" not in line]

    def all_running(self) -> bool:
        for unit in self.units:
            session = self.main.session(unit)
            if session is None or session["state"] != "running" or not session["launch_ready"]:
                return False
            if any(member["state"] not in ("running", "loaded") for member in session["members"]):
                return False
        return True


@pytest.fixture(scope="module", params=TOPOLOGIES)
def environment(request: pytest.FixtureRequest) -> Iterator[Environment]:
    env = Environment(request.param)
    up = env.run("up")
    if up.returncode != 0:
        env.run("down")
        pytest.fail(f"run.py up {env.topology} failed:\n{up.stdout}\n{up.stderr}")
    try:
        yield env
    finally:
        if not (request.config.getoption("--keep") and request.param == request.config.last_topology):
            env.run("down")


@pytest.fixture(scope="module")
def launched(environment: Environment) -> Environment:
    """The environment with every unit launched and all members running."""
    for unit in environment.units:
        environment.main.launch(unit)
    wait_for(environment.all_running, 60, "every member running")
    return environment
