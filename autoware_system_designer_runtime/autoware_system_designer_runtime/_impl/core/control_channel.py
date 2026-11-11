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

"""Control channel of a running system: a Unix socket speaking JSON lines.

One object per line. The runtime sends events ``{"event": ...}``: ``hello`` on connect, ``member``
on every state change, ``member.log`` for new stdout/stderr lines of a member the client follows,
``member.activity`` with the output growth rate of every member whose files grew in the last window,
``monitor`` with per-node topic and timer rates from the tracer's counters to the clients that follow them.
A client sends requests ``{"id", "method", "params"}`` and receives ``{"id", "ok", "result" | "error"}``.

Member output stays in the per-member ``out``/``err`` files the actors write; the channel tails a
file only while some client follows that member, so an idle channel costs nothing per member. The
activity scan sizes every member's files once per window while a client is connected; the counters
are read only while some client follows the rates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from .commands import CommandError, MemberRecord, MemberStates, RuntimeCommands
from .regular_actor import _slug

logger = logging.getLogger(__name__)

METHODS = (
    "status",
    "stop",
    "restart",
    "kill",
    "log.follow",
    "log.unfollow",
    "measure.start",
    "measure.stop",
    "measure.status",
    "monitor.sample",
    "monitor.follow",
    "monitor.unfollow",
    "shutdown",
)
# Methods that wait: answered from a task of their own, so the requests behind them are not held up.
DETACHED_METHODS = ("monitor.sample",)
STREAMS = ("out", "err")
MAX_LINE = 64 * 1024
LOG_POLL_S = 0.2
LOG_READ_MAX = 1 << 20
# Window of the output growth scan behind ``member.activity``.
ACTIVITY_POLL_S = 2.0
# Tail of a file reported by ``log.follow``: at most this many lines from the last bytes of it.
BACKFILL_LINES = 200
BACKFILL_BYTES = 64 * 1024
# Outgoing bytes a client may have queued before it counts as stalled.
QUEUE_BYTES = 8 << 20
# Interval of the ``monitor`` rates sent to following clients.
MONITOR_POLL_S = 2.0


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}


def _encode(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode() + b"\n"


class _Client:
    """One connection; encoded objects queue up and leave in order, a client stalled past the byte limit is dropped."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
        self._queued = 0
        self.dropped = False
        self.follows: set[str] = set()
        self.follows_rates = False

    def send(self, obj: dict[str, Any]) -> None:
        self.send_bytes(_encode(obj))

    def send_bytes(self, data: bytes) -> None:
        if self.dropped:
            return
        if self._queued + len(data) > QUEUE_BYTES:
            self.dropped = True
            logger.warning("control client stalled with %d bytes queued, disconnecting", self._queued)
            self._writer.close()
            return
        self._queued += len(data)
        self._queue.put_nowait(data)

    async def run(self) -> None:
        try:
            while True:
                data = await self._queue.get()
                if data is None:
                    break
                self._queued -= len(data)
                self._writer.write(data)
                await self._writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._writer.close()

    def close(self) -> None:
        self.dropped = True
        self._queue.put_nowait(None)


class ControlChannel:
    """Serves the control socket; forwards member states and followed logs, answers commands."""

    def __init__(
        self,
        commands: RuntimeCommands,
        states: MemberStates,
        *,
        socket_path: Path,
        log_dir: Path,
        policy: str = "guard",
    ) -> None:
        self._commands = commands
        self._states = states
        self._path = Path(socket_path)
        self._log_dir = Path(log_dir)
        self._policy = policy
        self._server: Optional[asyncio.AbstractServer] = None
        self._clients: set[_Client] = set()
        self._tasks: set[asyncio.Task] = set()
        self._tail_task: Optional[asyncio.Task] = None
        self._activity_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._unsubscribe = None
        # Read position and the unterminated tail per followed (member, stream).
        self._offsets: dict[tuple[str, str], int] = {}
        self._partial: dict[tuple[str, str], bytes] = {}
        self._paths: dict[tuple[str, str], Path] = {}
        # Last activity scan: its time and the output size per member; the previous report had rates.
        self._sizes: Optional[tuple[float, dict[str, int]]] = None
        self._reported = False

    @property
    def path(self) -> Path:
        return self._path

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists() or self._path.is_symlink():
            self._path.unlink()
        self._server = await asyncio.start_unix_server(self._serve, path=str(self._path), limit=MAX_LINE)
        os.chmod(self._path, 0o600)
        self._unsubscribe = self._states.subscribe(self._on_state)
        self._tail_task = asyncio.ensure_future(self._tail_logs())
        self._activity_task = asyncio.ensure_future(self._scan_activity())
        if self._commands.monitor is not None:
            self._monitor_task = asyncio.ensure_future(self._report_rates())
        logger.info("control channel: %s", self._path)

    async def close(self) -> None:
        for task in (self._tail_task, self._activity_task, self._monitor_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tail_task = self._activity_task = self._monitor_task = None
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._server is not None:
            self._server.close()
        for client in list(self._clients):
            client.close()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._clients.clear()
        # wait_closed waits for the connections from Python 3.12 on, so they are closed first.
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None
        try:
            self._path.unlink()
        except OSError:
            pass

    # ---- connections -------------------------------------------------------

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = _Client(writer)
        self._clients.add(client)
        sender = asyncio.ensure_future(client.run())
        self._tasks.add(sender)
        try:
            client.send(self._hello())
            while not client.dropped:
                try:
                    line = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    client.send(_error(None, "payload_too_large", f"request exceeds {MAX_LINE} bytes"))
                    break
                if not line:
                    break
                if not line.strip():
                    continue
                reply = await self._handle(client, line)
                if reply is not None:
                    client.send(reply)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(client)
            client.close()
            self._tasks.discard(sender)
            self._prune_follows()

    def _hello(self) -> dict[str, Any]:
        status = self._commands.status()
        return {
            "event": "hello",
            "pid": os.getpid(),
            "methods": list(METHODS),
            "policy": self._policy,
            "measure_armed": self._commands.measure_armed,
            "monitor": self._commands.monitor is not None,
            "log_dir": str(self._log_dir),
            **status,
        }

    def _broadcast(self, obj: dict[str, Any]) -> None:
        data = _encode(obj)
        for client in list(self._clients):
            client.send_bytes(data)

    def _on_state(self, record: MemberRecord) -> None:
        self._broadcast({"event": "member", **record.as_dict()})

    # ---- requests ----------------------------------------------------------

    async def _handle(self, client: _Client, line: bytes) -> Optional[dict[str, Any]]:
        """The reply to one request line; None for a detached method, whose reply its task sends."""
        try:
            request = json.loads(line)
        except ValueError:
            return _error(None, "bad_request", "request is not JSON")
        if not isinstance(request, dict):
            return _error(None, "bad_request", "request must be an object")
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(method, str):
            return _error(request_id, "bad_request", "method must be a string")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(request_id, "invalid_params", "params must be an object")
        if method in DETACHED_METHODS:
            task = asyncio.ensure_future(self._answer_detached(client, request_id, method, params))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return None
        return await self._answer(client, request_id, method, params)

    async def _answer_detached(self, client: _Client, request_id: Any, method: str, params: dict[str, Any]) -> None:
        client.send(await self._answer(client, request_id, method, params))

    async def _answer(self, client: _Client, request_id: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._dispatch(client, method, params)
        except CommandError as exc:
            return _error(request_id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("control method %s failed", method)
            return _error(request_id, "internal", str(exc))
        return {"id": request_id, "ok": True, "result": result}

    async def _dispatch(self, client: _Client, method: str, params: dict[str, Any]) -> Any:
        if method == "status":
            return self._commands.status()
        if method in ("stop", "restart", "kill"):
            exact = params.get("exact", False)
            if not isinstance(exact, bool):
                raise CommandError("invalid_params", "exact must be a boolean")
            return {"members": await self._commands.member_action(method, params.get("pattern"), exact=exact)}
        if method == "log.follow":
            return self._follow(client, params.get("path"))
        if method == "log.unfollow":
            return self._unfollow(client, params.get("path"))
        if method.startswith("measure."):
            return await self._commands.measure(method[len("measure.") :])
        if method == "monitor.sample":
            return await self._commands.rates(params.get("interval", 1.0))
        if method == "monitor.follow":
            if self._commands.monitor is None:
                await self._commands.rates()  # raises the unavailable error
            client.follows_rates = True
            return {"interval_s": MONITOR_POLL_S}
        if method == "monitor.unfollow":
            released = client.follows_rates
            client.follows_rates = False
            return {"released": released}
        if method == "shutdown":
            self._commands.shutdown()
            return {"shutting_down": True}
        raise CommandError("unknown_method", f"unknown method {method!r}")

    # ---- member logs -------------------------------------------------------

    def _follow(self, client: _Client, path: object) -> dict[str, Any]:
        """Starts forwarding a member's new lines to *client*; the result carries the files' current tails."""
        if not isinstance(path, str) or path not in self._commands.coord.names():
            raise CommandError("not_found", f"no member named {path!r}")
        result: dict[str, Any] = {"path": path}
        for stream in STREAMS:
            key = (path, stream)
            file = self._log_path(key)
            if key in self._offsets:
                self._tail_file(key)  # pending lines reach the current followers before the tail is cut
                result[stream], _ = _tail_lines(file, self._offsets[key])
            else:
                try:
                    self._offsets[key] = file.stat().st_size
                except OSError:
                    self._offsets[key] = 0
                result[stream], rest = _tail_lines(file, self._offsets[key])
                if rest:
                    self._partial[key] = rest
        client.follows.add(path)
        return result

    def _unfollow(self, client: _Client, path: object) -> dict[str, Any]:
        """Stops forwarding one member's lines, or every member's when no path is given."""
        if path is None:
            released = sorted(client.follows)
            client.follows.clear()
        elif isinstance(path, str):
            released = [path] if path in client.follows else []
            client.follows.discard(path)
        else:
            raise CommandError("invalid_params", "path must be a member name")
        self._prune_follows()
        return {"released": released}

    def _followed(self) -> set[str]:
        followed: set[str] = set()
        for client in self._clients:
            followed |= client.follows
        return followed

    def _prune_follows(self) -> None:
        followed = self._followed()
        for key in [key for key in self._offsets if key[0] not in followed]:
            self._offsets.pop(key, None)
            self._partial.pop(key, None)

    def _log_path(self, key: tuple[str, str]) -> Path:
        path = self._paths.get(key)
        if path is None:
            path = self._paths[key] = self._log_dir / _slug(key[0]) / key[1]
        return path

    async def _tail_logs(self) -> None:
        """Forwards new lines of every followed member's out/err file to the clients following it."""
        while True:
            await asyncio.sleep(LOG_POLL_S)
            for key in list(self._offsets):
                try:
                    self._tail_file(key)
                except Exception:  # noqa: BLE001
                    logger.exception("tailing %s/%s failed", *key)

    def _tail_file(self, key: tuple[str, str]) -> None:
        path = self._log_path(key)
        try:
            size = path.stat().st_size
        except OSError:
            return
        offset = self._offsets[key]
        if size < offset:
            offset = 0
            self._partial.pop(key, None)
        if size == offset:
            return
        try:
            with open(path, "rb") as handle:
                handle.seek(offset)
                data = handle.read(min(size - offset, LOG_READ_MAX))
        except OSError:
            return
        self._offsets[key] = offset + len(data)
        chunks = (self._partial.pop(key, b"") + data).split(b"\n")
        rest = chunks.pop()
        if rest:
            self._partial[key] = rest
        if not chunks:
            return
        name, stream = key
        lines = [chunk.decode("utf-8", "replace") for chunk in chunks]
        data = _encode({"event": "member.log", "path": name, "stream": stream, "lines": lines})
        for client in list(self._clients):
            if name in client.follows:
                client.send_bytes(data)

    # ---- member activity ---------------------------------------------------

    async def _scan_activity(self) -> None:
        """Reports the output growth of the members to the clients, one map of rates per window."""
        while True:
            await asyncio.sleep(ACTIVITY_POLL_S)
            if not self._clients:
                self._sizes = None
                self._reported = False
                continue
            try:
                self._report_activity()
            except Exception:  # noqa: BLE001
                logger.exception("scanning member output failed")

    def _report_activity(self) -> None:
        """Broadcasts the bytes per second each member's files grew by since the last scan; members
        without growth are left out, and a window with none follows a window with some."""
        now = time.monotonic()
        sizes = {name: self._output_size(name) for name in self._commands.coord.names()}
        previous, self._sizes = self._sizes, (now, sizes)
        if previous is None:
            return
        then, before = previous
        elapsed = max(now - then, 1e-3)
        rates: dict[str, int] = {}
        for name, size in sizes.items():
            grown = size - before.get(name, 0)
            if grown < 0:  # truncated
                grown = size
            if grown > 0:
                rates[name] = max(1, round(grown / elapsed))
        if not rates and not self._reported:
            return
        self._reported = bool(rates)
        self._broadcast({"event": "member.activity", "window": round(elapsed, 3), "rates": rates})

    def _output_size(self, name: str) -> int:
        total = 0
        for stream in STREAMS:
            try:
                total += self._log_path((name, stream)).stat().st_size
            except OSError:
                pass
        return total

    # ---- rates -----------------------------------------------------------------

    async def _report_rates(self) -> None:
        """Sends per-node rates to the clients following them, one snapshot per interval."""
        monitor = self._commands.monitor
        previous = None
        while True:
            await asyncio.sleep(MONITOR_POLL_S)
            followers = [client for client in self._clients if client.follows_rates]
            if not followers:
                previous = None
                continue
            try:
                reading = monitor.read()
                if previous is not None:
                    data = _encode({"event": "monitor", **monitor.rates(previous, reading)})
                    for client in followers:
                        client.send_bytes(data)
                previous = reading
            except Exception:  # noqa: BLE001
                logger.exception("reading the tracer counters failed")


def _tail_lines(path: Path, size: int) -> tuple[list[str], bytes]:
    """The last complete lines of a file's first ``size`` bytes, from at most its final
    ``BACKFILL_BYTES``, and the unterminated bytes after them."""
    if size == 0:
        return [], b""
    start = max(0, size - BACKFILL_BYTES)
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read(size - start)
    except OSError:
        return [], b""
    chunks = data.split(b"\n")
    rest = chunks.pop()
    if start > 0 and chunks:
        chunks.pop(0)  # a line cut by the window start
    return [chunk.decode("utf-8", "replace") for chunk in chunks[-BACKFILL_LINES:]], rest
