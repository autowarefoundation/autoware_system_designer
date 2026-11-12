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


"""A member ends with its whole process group, whatever its leader does."""

import asyncio
import os
import signal
import sys
import time

from autoware_system_designer_runtime._impl.core import events as ev
from autoware_system_designer_runtime._impl.core import process
from autoware_system_designer_runtime._impl.core.config import ActorConfig
from autoware_system_designer_runtime._impl.core.coordinator import CoordinatorBuilder
from autoware_system_designer_runtime._impl.core.regular_actor import NodeSpec

# A child that takes no notice of SIGTERM, as a node stuck in its shutdown.
STUBBORN_CHILD = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(60)"

# A leader that prints the pid of its child once the child ignores SIGTERM, then runs *then*.
LEADER = (
    "import subprocess, sys, time\n"
    f"p = subprocess.Popen([sys.executable, '-c', {STUBBORN_CHILD!r}], stdout=subprocess.PIPE)\n"
    "p.stdout.readline()\n"
    "print(p.pid, flush=True)\n"
    "{then}\n"
)


def _leader(then):
    return [sys.executable, "-c", LEADER.format(then=then)]


def _running(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rpartition(")")[2].split()[0] != "Z"
    except OSError:
        return False


async def _child_pid(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        text = path.read_text().strip() if path.exists() else ""
        if text:
            return int(text)
        await asyncio.sleep(0.02)
    raise AssertionError("the leader did not report its child")


def test_stop_kills_the_children_a_leader_leaves_behind(tmp_path):
    async def scenario():
        # The leader ends on SIGTERM at once, as a launch service does, with its child still running.
        proc = await process.spawn_pgrp(_leader("time.sleep(60)"), stdout_path=tmp_path / "out")
        child = await _child_pid(tmp_path / "out")
        started = time.monotonic()
        exit_code = await process.graceful_kill(proc, name="member", timeout=0.5)
        return proc.pid, child, exit_code, time.monotonic() - started

    pgid, child, exit_code, took = asyncio.run(scenario())
    assert exit_code == -signal.SIGTERM
    assert not _running(child) and not process.group_alive(pgid)
    assert 0.5 <= took < 5


def test_stop_returns_once_the_group_is_gone(tmp_path):
    async def scenario():
        proc = await process.spawn_pgrp([sys.executable, "-c", "import time; time.sleep(60)"])
        started = time.monotonic()
        exit_code = await process.graceful_kill(proc, name="member", timeout=5.0)
        return proc.pid, exit_code, time.monotonic() - started

    pgid, exit_code, took = asyncio.run(scenario())
    assert exit_code == -signal.SIGTERM and not process.group_alive(pgid)
    assert took < 2


def test_a_leader_that_exits_by_itself_takes_its_group_with_it(tmp_path):
    config = ActorConfig(output_dir=tmp_path, graceful_shutdown_timeout=0.5)
    builder = CoordinatorBuilder(default_config=config)
    builder.add_node(NodeSpec(name="/wrapper", cmd=_leader("sys.exit(3)")))
    seen = []
    builder.add_state_hook(seen.append)

    async def run():
        coord = builder.build()
        run_task = asyncio.ensure_future(coord.run())
        child = await _child_pid(tmp_path / "wrapper" / "out")
        deadline = time.monotonic() + 10
        while not any(isinstance(event, ev.Exited) for event in seen):
            assert time.monotonic() < deadline, "the member did not exit"
            await asyncio.sleep(0.02)
        alive_at_exit = _running(child)
        coord.request_shutdown()
        await asyncio.wait_for(run_task, timeout=10)
        return child, alive_at_exit

    child, alive_at_exit = asyncio.run(run())
    assert not alive_at_exit and not _running(child)
    assert [event.exit_code for event in seen if isinstance(event, ev.Exited)] == [3]


def test_a_zombie_does_not_keep_a_group_alive():
    # The child is a zombie of this process until it is waited for.
    pid = os.fork()
    if pid == 0:
        os.setsid()
        os._exit(0)
    deadline = time.monotonic() + 5
    while _running(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert os.path.exists(f"/proc/{pid}") and not process.group_alive(pid)
    finally:
        os.waitpid(pid, 0)
