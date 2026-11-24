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

"""Resource readings of member process groups from /proc."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from autoware_system_designer_runtime._impl.measure.resources import GpuReader, ResourceMonitor, format_resources


@pytest.fixture
def group():
    """A process group led by a shell whose child spins."""
    proc = subprocess.Popen(
        ["bash", "-c", f"{sys.executable} -c 'while True: pass' & wait"],
        start_new_session=True,
    )
    time.sleep(0.5)
    yield proc
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def _monitor(leaders):
    return ResourceMonitor(lambda: dict(leaders), gpu=GpuReader())


def test_a_member_sums_the_processes_of_its_group(group):
    monitor = _monitor({"/spin": group.pid})
    before = monitor.read()
    time.sleep(0.5)
    usage = monitor.usage(before, monitor.read())
    entry = usage["members"]["/spin"]
    assert entry["pid"] == group.pid and entry["procs"] == 2
    assert entry["cpu"] > 0.5
    assert entry["rss_kb"] > 0 and entry["pss_kb"] > 0
    assert usage["host"]["cpus"] >= 1 and usage["host"]["mem_total_kb"] > 0
    assert usage["gpu"] == {"source": None, "per_process": False, "note": "no GPU found", "devices": []}
    assert any("/spin" in line for line in format_resources(usage))


def test_the_first_reading_has_memory_but_no_cpu(group):
    monitor = _monitor({"/spin": group.pid})
    usage = monitor.usage(None, monitor.read())
    assert "cpu" not in usage["members"]["/spin"] and usage["interval_s"] is None


def test_a_gone_member_is_left_out():
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    monitor = _monitor({"/gone": proc.pid})
    assert monitor.usage(None, monitor.read())["members"] == {}


def test_pss_reads_are_spread_over_the_readings(group, monkeypatch):
    from autoware_system_designer_runtime._impl.measure import resources

    reads = []
    monkeypatch.setattr(resources, "_read_pss_kb", lambda pid: reads.append(pid) or 100)
    monkeypatch.setattr(resources, "PSS_SHARES", 2)
    monitor = _monitor({"/spin": group.pid})
    first = monitor.read()
    assert len(reads) == 1 and "pss_kb" not in monitor.usage(None, first)["members"]["/spin"]
    second = monitor.read()
    assert len(set(reads)) == 2 and monitor.usage(first, second)["members"]["/spin"]["pss_kb"] == 200
    monitor.read()
    assert len(reads) == 2
