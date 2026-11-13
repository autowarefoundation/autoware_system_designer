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

"""Background tasks of one owner."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Optional

logger = logging.getLogger(__name__)

CANCEL_WAIT_S = 1.0


class TaskSet:
    """Tasks held from their start to their end, so that their owner can wait for or cancel them."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coroutine: Awaitable[Any], *, failure: Optional[str] = None) -> asyncio.Task:
        """Runs ``coroutine``; an exception it ends with is logged as ``failure`` when one is named."""
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if failure is not None:
            task.add_done_callback(lambda done: _log_failure(done, failure))
        return task

    async def wait(self, timeout: float) -> None:
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)

    async def cancel(self, timeout: float = CANCEL_WAIT_S) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.wait(timeout)


def _log_failure(task: asyncio.Task, failure: str) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("%s failed: %s", failure, exc)
