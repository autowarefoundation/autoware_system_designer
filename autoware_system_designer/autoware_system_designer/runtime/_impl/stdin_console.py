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

"""Minimal stdin REPL for live actor control.

Supported commands (one per line)::

    status                   list every member and its current state
    stop    <name|prefix>    send Stop to matching members
    restart <name|prefix>    send Restart to matching members
    kill    <name|prefix>    send SIGKILL to matching members
    quit                     request shutdown

Names are matched as prefix substrings, so ``stop /perception`` stops
everything under that namespace.
"""

from __future__ import annotations

import asyncio
import logging
import select
import shlex
import signal
import sys
from typing import Optional

from .coordinator import Coordinator

logger = logging.getLogger(__name__)

_POLL_TIMEOUT = 0.1  # select() poll interval; shorter = faster shutdown response


async def run_console(coord: Coordinator) -> None:
    """Loop reading commands from stdin until shutdown or ``quit``."""
    if not sys.stdin.isatty():
        logger.warning("--interactive: stdin is not a TTY — interactive console disabled")
        return

    loop = asyncio.get_running_loop()
    print("[console] type 'status', 'stop <name>', 'restart <name>', 'kill <name>', 'quit'")
    try:
        while not coord.shutdown_event.is_set():
            line = await _readline(loop)
            if line is None:
                continue  # poll timeout — re-check shutdown_event
            if not line:  # EOF (Ctrl+D, SSH disconnect, pipe close)
                logger.info("[console] stdin closed, requesting shutdown")
                coord.request_shutdown()
                return
            await _dispatch(coord, line.strip())
    except asyncio.CancelledError:
        pass


async def _readline(loop: asyncio.AbstractEventLoop) -> Optional[str]:
    """Return next stdin line, ``""`` on EOF, ``None`` on poll timeout."""
    try:
        ready = await loop.run_in_executor(
            None,
            lambda: select.select([sys.stdin], [], [], _POLL_TIMEOUT)[0],
        )
    except (OSError, ValueError):
        return ""  # stdin became invalid (e.g. closed fd)
    if not ready:
        return None  # timeout — caller re-checks shutdown_event
    return sys.stdin.readline()


async def _dispatch(coord: Coordinator, line: str) -> None:
    try:
        parts = shlex.split(line)
    except ValueError as e:
        print(f"[console] parse error: {e}")
        return
    if not parts:
        return

    op = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None

    if op == "status":
        for name in coord.names():
            print(f"  {name}")
        return

    if op == "quit":
        coord.request_shutdown()
        return

    if op in ("stop", "restart", "kill"):
        if not arg:
            print(f"[console] {op} needs a name or prefix")
            return
        targets = _match(coord.names(), arg)
        if not targets:
            print(f"[console] no member matches {arg!r}")
            return
        for name in targets:
            handle = coord.handle(name)
            if op == "stop":
                await handle.stop()
            elif op == "restart":
                await handle.restart()
            else:
                await handle.kill(signal.SIGKILL)
            print(f"[console] {op} -> {name}")
        return

    print(f"[console] unknown command {op!r}")


def _match(names: list[str], pattern: str) -> list[str]:
    return [n for n in names if pattern in n]
