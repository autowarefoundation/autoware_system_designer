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
on every state change, ``member.log`` for new stdout/stderr lines of a member. A client sends
requests ``{"id", "method", "params"}`` and receives ``{"id", "ok", "result" | "error"}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from .commands import CommandError, MemberRecord, MemberStates, RuntimeCommands
from .regular_actor import _slug

logger = logging.getLogger(__name__)

METHODS = ("status", "stop", "restart", "kill", "measure.start", "measure.stop", "measure.status", "shutdown")
STREAMS = ("out", "err")
MAX_LINE = 64 * 1024
LOG_POLL_S = 0.2
LOG_READ_MAX = 1 << 20
QUEUE_LIMIT = 10000


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}


class _Client:
    """One connection; outgoing objects queue up and leave in order, a stalled client is dropped."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._queue: "asyncio.Queue[Optional[dict[str, Any]]]" = asyncio.Queue(QUEUE_LIMIT)
        self.dropped = False

    def send(self, obj: dict[str, Any]) -> None:
        if self.dropped:
            return
        try:
            self._queue.put_nowait(obj)
        except asyncio.QueueFull:
            self.dropped = True
            logger.warning("control client stalled, disconnecting")
            self._writer.close()

    async def run(self) -> None:
        try:
            while True:
                obj = await self._queue.get()
                if obj is None:
                    break
                self._writer.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
                await self._writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._writer.close()

    def close(self) -> None:
        self.dropped = True
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            self._writer.close()


class ControlChannel:
    """Serves the control socket; forwards member states and logs, answers commands."""

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
        self._unsubscribe = None
        self._offsets: dict[tuple[str, str], int] = {}
        self._partial: dict[tuple[str, str], bytes] = {}

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
        logger.info("control channel: %s", self._path)

    async def close(self) -> None:
        if self._tail_task is not None:
            self._tail_task.cancel()
            try:
                await self._tail_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._tail_task = None
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
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
                client.send(await self._handle(line))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(client)
            client.close()
            self._tasks.discard(sender)

    def _hello(self) -> dict[str, Any]:
        status = self._commands.status()
        return {
            "event": "hello",
            "pid": os.getpid(),
            "methods": list(METHODS),
            "policy": self._policy,
            "measure_armed": self._commands.measure_armed,
            **status,
        }

    def _broadcast(self, obj: dict[str, Any]) -> None:
        for client in list(self._clients):
            client.send(obj)

    def _on_state(self, record: MemberRecord) -> None:
        self._broadcast({"event": "member", **record.as_dict()})

    # ---- requests ----------------------------------------------------------

    async def _handle(self, line: bytes) -> dict[str, Any]:
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
        try:
            result = await self._dispatch(method, params)
        except CommandError as exc:
            return _error(request_id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("control method %s failed", method)
            return _error(request_id, "internal", str(exc))
        return {"id": request_id, "ok": True, "result": result}

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "status":
            return self._commands.status()
        if method in ("stop", "restart", "kill"):
            return {"members": await self._commands.member_action(method, params.get("pattern"))}
        if method.startswith("measure."):
            return await self._commands.measure(method[len("measure.") :])
        if method == "shutdown":
            self._commands.shutdown()
            return {"shutting_down": True}
        raise CommandError("unknown_method", f"unknown method {method!r}")

    # ---- member logs -------------------------------------------------------

    async def _tail_logs(self) -> None:
        """Forwards new lines of every member's out/err file; nothing is read while no client listens."""
        while True:
            if self._clients:
                for name in self._commands.coord.names():
                    for stream in STREAMS:
                        self._tail_file(name, stream)
            await asyncio.sleep(LOG_POLL_S)

    def _tail_file(self, name: str, stream: str) -> None:
        key = (name, stream)
        path = self._log_dir / _slug(name) / stream
        try:
            size = path.stat().st_size
        except OSError:
            return
        offset = self._offsets.get(key, 0)
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
        if chunks:
            lines = [chunk.decode("utf-8", "replace") for chunk in chunks]
            self._broadcast({"event": "member.log", "path": name, "stream": stream, "lines": lines})
