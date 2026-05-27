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

    help / ?                 show command reference
    status                   list every member and its current state
    stop    <name>    send Stop to matching members
    restart <name>    send Restart to matching members
    kill    <name>    send SIGKILL to matching members
    quit                     request shutdown

Names are matched as substrings, so ``stop /perception`` stops everything
under that namespace.

Output while the user is typing is handled by ``_StickyHandler``: each log
record erases the current prompt line (``\\r\\033[K``), prints the message,
then redraws the prompt + partial input so the prompt stays at the bottom.
``readline`` is activated by import so arrow-key editing and history work.
"""

from __future__ import annotations

import asyncio
import logging
import readline  # noqa: F401 — activates readline line editing for input()
import shlex
import signal
import sys
import threading
from typing import Optional

from .coordinator import Coordinator

logger = logging.getLogger(__name__)

_PROMPT = "autoware runtime [? for help] > "
_output_lock = threading.Lock()


def _write_above(text: str) -> None:
    """Erase the current prompt line, print *text*, redraw the prompt."""
    buf = readline.get_line_buffer()
    with _output_lock:
        sys.stdout.write(f"\r\033[K{text}\n{_PROMPT}{buf}")
        sys.stdout.flush()


class _StickyHandler(logging.Handler):
    """Logging handler that keeps the prompt pinned at the bottom of the terminal."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _write_above(self.format(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)


async def run_console(coord: Coordinator) -> None:
    """Loop reading commands from stdin until shutdown or ``quit``."""
    if not sys.stdin.isatty():
        logger.warning("--interactive: stdin is not a TTY — interactive console disabled")
        return

    # Replace root StreamHandlers with the sticky variant (same formatter + filters).
    fmt: Optional[logging.Formatter] = None
    filters: list[logging.Filter] = []
    replaced: list[logging.Handler] = []
    for h in list(logging.root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            fmt = fmt or h.formatter
            filters = filters or list(h.filters)
            logging.root.removeHandler(h)
            replaced.append(h)
    sticky = _StickyHandler()
    if fmt:
        sticky.setFormatter(fmt)
    for f in filters:
        sticky.addFilter(f)
    logging.root.addHandler(sticky)

    try:
        await coord.launch_ready.wait()
    except asyncio.CancelledError:
        return

    try:
        while not coord.shutdown_event.is_set():
            try:
                line = await _input_or_shutdown(coord)
            except EOFError:
                logger.info("[console] stdin closed, requesting shutdown")
                coord.request_shutdown()
                return
            except KeyboardInterrupt:
                continue
            except asyncio.CancelledError:
                return
            if line is None:  # shutdown fired while waiting for input
                return
            await _dispatch(coord, line.strip())
    finally:
        logging.root.removeHandler(sticky)
        for h in replaced:
            logging.root.addHandler(h)


async def _input_or_shutdown(coord: Coordinator) -> Optional[str]:
    """Return the next input line, or ``None`` if shutdown fired first.

    The executor thread blocking in ``input()`` is left running if shutdown
    wins the race; it will unblock when the user next presses Enter or when
    the process exits.
    """
    input_future = asyncio.get_running_loop().run_in_executor(None, input, _PROMPT)
    shutdown_task = asyncio.ensure_future(coord.shutdown_event.wait())
    try:
        done, _ = await asyncio.wait(
            {input_future, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        shutdown_task.cancel()
        if shutdown_task in done:
            return None
        return input_future.result()  # re-raises EOFError / KeyboardInterrupt
    except asyncio.CancelledError:
        shutdown_task.cancel()
        raise


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

    if op in ("help", "?"):
        print(
            "  status              — list all members and their current state\n"
            "  stop    <name>      — send Stop to matching members\n"
            "  restart <name>      — send Restart to matching members\n"
            "  kill    <name>      — send SIGKILL to matching members\n"
            "  quit                — request graceful shutdown\n"
            "  help / ?            — show this message\n"
            "  Names are matched as substrings, e.g. 'stop /perception'."
        )
        return

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
