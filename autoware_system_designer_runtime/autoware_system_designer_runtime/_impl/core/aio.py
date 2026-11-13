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

"""asyncio helpers shared by the actors, the coordinator and the control surfaces."""

from __future__ import annotations

import asyncio
from typing import Any, Optional


def resolve(future: asyncio.Future, result: Any, exc: Optional[BaseException] = None) -> None:
    """Sets the result or the exception of ``future``; a future already done is left as it is."""
    if future.done():
        return
    if exc is not None:
        future.set_exception(exc)
    else:
        future.set_result(result)


async def first_completed(*tasks: asyncio.Future) -> set[asyncio.Future]:
    """The tasks done once the first one ends; the others are cancelled, as all are when the wait is."""
    try:
        done, _ = await asyncio.wait(set(tasks), return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    return done


async def cancel_and_wait(*tasks: Optional[asyncio.Future]) -> None:
    """Cancels the tasks and waits for each to end; what a task ends with is dropped."""
    live = [task for task in tasks if task is not None]
    for task in live:
        if not task.done():
            task.cancel()
    for task in live:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
