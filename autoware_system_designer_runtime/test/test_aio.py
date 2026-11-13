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

"""Tests for the shared asyncio helpers."""

from __future__ import annotations

import asyncio

import pytest

from autoware_system_designer_runtime._impl.core.aio import cancel_and_wait, first_completed


def test_cancel_and_wait_ends_the_tasks_and_drops_what_they_end_with():
    async def fails():
        raise RuntimeError("boom")

    async def scenario():
        sleeper = asyncio.ensure_future(asyncio.sleep(60))
        failed = asyncio.ensure_future(fails())
        await asyncio.sleep(0)
        await cancel_and_wait(sleeper, None, failed)
        return sleeper.cancelled(), failed.done()

    assert asyncio.run(scenario()) == (True, True)


def test_cancel_and_wait_raises_a_cancellation_of_its_caller():
    async def stubborn(release: asyncio.Event):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await release.wait()

    async def scenario():
        release = asyncio.Event()
        task = asyncio.ensure_future(stubborn(release))
        await asyncio.sleep(0)
        caller = asyncio.ensure_future(cancel_and_wait(task))
        await asyncio.sleep(0)
        caller.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller

    asyncio.run(scenario())


def test_first_completed_cancels_the_tasks_still_running():
    async def scenario():
        quick = asyncio.ensure_future(asyncio.sleep(0, "done"))
        slow = asyncio.ensure_future(asyncio.sleep(60))
        done = await first_completed(quick, slow)
        await asyncio.sleep(0)
        return done == {quick}, slow.cancelled()

    assert asyncio.run(scenario()) == (True, True)
