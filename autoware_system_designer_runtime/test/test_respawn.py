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


"""Respawn policy of the regular node actor."""

import asyncio
import time

import pytest

from autoware_system_designer_runtime._impl.core import events as ev
from autoware_system_designer_runtime._impl.core import regular_actor
from autoware_system_designer_runtime._impl.core.config import ActorConfig
from autoware_system_designer_runtime._impl.core.coordinator import CoordinatorBuilder
from autoware_system_designer_runtime._impl.core.regular_actor import NodeSpec

pytestmark = [pytest.mark.usefixtures("fake_process_groups"), pytest.mark.timing]


def _run_exiting_node(monkeypatch, tmp_path, exit_code, until):
    """Run one armed node whose process exits at once, until ``until`` holds of the event type names seen
    and a few respawn delays have passed; return the names."""

    class _ExitingProc:
        pid = 4242
        returncode = None

        async def wait(self):
            return exit_code

    async def fake_spawn(cmd, *, env=None, stdout_path=None, stderr_path=None, cwd=None):
        return _ExitingProc()

    monkeypatch.setattr(regular_actor, "spawn_pgrp", fake_spawn)
    config = ActorConfig(output_dir=tmp_path, respawn_enabled=True, respawn_delay=0.01)
    builder = CoordinatorBuilder(default_config=config)
    builder.add_node(NodeSpec(name="/n", cmd=["true"]))
    seen = []

    async def run():
        coord = builder.build()
        run_task = asyncio.ensure_future(coord.run())
        deadline = time.monotonic() + 10
        while not until(seen):
            assert time.monotonic() < deadline, f"events seen: {seen}"
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        coord.request_shutdown()
        await asyncio.wait_for(run_task, timeout=10)

    builder.add_state_hook(lambda event: seen.append(type(event).__name__))
    asyncio.run(run())
    return seen


def test_clean_exit_is_not_respawned(monkeypatch, tmp_path):
    seen = _run_exiting_node(monkeypatch, tmp_path, 0, lambda seen: ev.Exited.__name__ in seen)
    assert seen.count(ev.Started.__name__) == 1
    assert ev.Respawning.__name__ not in seen


@pytest.mark.parametrize("exit_code", [1, -6])
def test_abnormal_exit_is_respawned(monkeypatch, tmp_path, exit_code):
    seen = _run_exiting_node(monkeypatch, tmp_path, exit_code, lambda seen: seen.count(ev.Started.__name__) > 1)
    assert seen.count(ev.Started.__name__) > 1
    assert ev.Respawning.__name__ in seen
