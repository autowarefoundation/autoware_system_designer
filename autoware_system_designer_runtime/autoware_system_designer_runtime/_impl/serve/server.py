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

"""Loopback daemon: static visualization pages, the request API and the event stream.

Routes:
  GET  /                 -> redirect to the systems index
  GET  /<servable path>  -> install-tree file (see exports.static_file)
  GET  /api/<alias>      -> registered method with empty params (health, systems)
  GET  /api/events       -> Server-Sent Events stream
  POST /api/rpc          -> request envelope (see api)

Access rules: the listener is bound to 127.0.0.1; every request must name this listener in Host
(DNS rebinding); API requests flagged cross-site by the browser are refused; POST requires
``Content-Type: application/json`` and the ``X-System-Designer-Client`` header, which a cross-origin
page cannot send without a preflight this daemon never grants.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Iterable, Optional

from . import api, events, exports
from .http import MAX_HEADER_BYTES, HttpError, Request, Response, read_request, response_head, write_response

logger = logging.getLogger(__name__)

BIND_HOST = "127.0.0.1"
LOOPBACK_HOST_NAMES = ("127.0.0.1", "localhost")
CLIENT_HEADER = "X-System-Designer-Client"
RPC_PATH = "/api/rpc"
EVENTS_PATH = "/api/events"
API_PREFIX = "/api/"

MAX_CONNECTIONS = 64
MAX_EVENT_SUBSCRIBERS = 16
HEARTBEAT_INTERVAL_S = 15.0
SHUTDOWN_GRACE_S = 2.0

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _content_type(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _package_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version("autoware_system_designer_runtime")
    except Exception:  # noqa: BLE001 - metadata is informational only
        return None


def _error_response(error: api.ApiError, request_id: Any = None, *, with_id: bool = False) -> Response:
    body = api.error_body(error, request_id) if with_id else api.error_body(error)
    headers = [("Allow", "GET, HEAD, POST")] if error.code == api.METHOD_NOT_ALLOWED else []
    return Response.json(error.status, body, headers)


class ServeDaemon:
    """One daemon instance; ``served_units`` None means every onboard unit is local to it."""

    def __init__(
        self,
        install_root: Path,
        *,
        port: int,
        served_units: Optional[Iterable[str]] = None,
        heartbeat_s: float = HEARTBEAT_INTERVAL_S,
        max_connections: int = MAX_CONNECTIONS,
        max_event_subscribers: int = MAX_EVENT_SUBSCRIBERS,
    ) -> None:
        self.install_root = install_root
        self.served_units = None if served_units is None else sorted(set(served_units))
        self._requested_port = port
        self._max_connections = max_connections
        self.hub = events.EventHub(max_event_subscribers, heartbeat_s)
        self.methods = api.MethodRegistry()
        self.methods.register("health", self._health, get_path="health")
        self.methods.register("systems.list", self._systems_list, get_path="systems")
        self._server: Optional[asyncio.AbstractServer] = None
        self._heartbeat: Optional[asyncio.Task] = None
        self._connections: set[asyncio.Task] = set()
        self._started_at = time.time()
        self.port = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> int:
        # The reader limit bounds the request head.
        self._server = await asyncio.start_server(
            self._on_connection, BIND_HOST, self._requested_port, limit=MAX_HEADER_BYTES
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self._heartbeat = asyncio.ensure_future(self.hub.run_heartbeat())
        self._started_at = time.time()
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._heartbeat is not None:
            self._heartbeat.cancel()
        self.hub.close()
        if self._connections:
            _, pending = await asyncio.wait(set(self._connections), timeout=SHUTDOWN_GRACE_S)
            for task in pending:
                task.cancel()

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(f"{name}:{self.port}" for name in LOOPBACK_HOST_NAMES)

    # -- methods -----------------------------------------------------------

    async def _health(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": _package_version(),
            "pid": os.getpid(),
            "started_at": self._started_at,
            "install_root": str(self.install_root),
            "served_units": self.served_units,
            "event_subscribers": self.hub.subscriber_count,
            "methods": self.methods.names(),
        }

    async def _systems_list(self, params: dict[str, Any]) -> dict[str, Any]:
        systems, errors = await asyncio.to_thread(exports.discover_systems, self.install_root)
        views = await asyncio.to_thread(
            lambda: [system.to_view(self.install_root, self.served_units) for system in systems]
        )
        return {
            "install_root": str(self.install_root),
            "systems": views,
            "errors": [{"path": e.path, "message": e.message} for e in errors],
        }

    # -- connection handling -----------------------------------------------

    async def _on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._connections.add(task)
        try:
            if len(self._connections) > self._max_connections:
                await write_response(
                    writer, _error_response(api.ApiError(api.UNAVAILABLE, "too many open connections"))
                )
                return
            await self._serve_one(reader, writer)
        except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one connection must not take the daemon down
            logger.exception("unhandled error while serving a connection")
        finally:
            self._connections.discard(task)
            writer.close()

    async def _serve_one(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_request(reader)
        except HttpError as exc:
            error = api.ApiError(api.code_for_status(exc.status), exc.message)
            await write_response(writer, Response.json(exc.status, api.error_body(error)))
            return
        logger.debug("%s %s", request.method, request.path)
        try:
            self._check_host(request)
            if request.path == EVENTS_PATH and request.method == "GET":
                self._check_api_fetch(request)
                await self._stream_events(reader, writer)
                return
            response = await self._route(request)
        except api.ApiError as exc:
            response = _error_response(exc)
        await write_response(writer, response, head_only=request.method == "HEAD")

    def _check_host(self, request: Request) -> None:
        if request.header("host") not in self.allowed_hosts:
            raise api.ApiError(api.FORBIDDEN, "Host header does not name this daemon")

    def _check_api_fetch(self, request: Request) -> None:
        if request.header("sec-fetch-site") in ("cross-site", "same-site"):
            raise api.ApiError(api.FORBIDDEN, "cross-site API requests are refused")
        origin = request.header("origin")
        if origin and origin not in {f"http://{host}" for host in self.allowed_hosts}:
            raise api.ApiError(api.FORBIDDEN, f"origin {origin!r} is not allowed")

    async def _route(self, request: Request) -> Response:
        if request.path.startswith(API_PREFIX):
            self._check_api_fetch(request)
            if request.path == RPC_PATH:
                if request.method != "POST":
                    raise api.ApiError(api.METHOD_NOT_ALLOWED, f"{RPC_PATH} accepts POST")
                return await self._rpc(request)
            name = self.methods.method_for_get(request.path[len(API_PREFIX) :])
            if name is None:
                raise api.ApiError(api.NOT_FOUND, f"no API endpoint at {request.path}")
            if request.method not in ("GET", "HEAD"):
                raise api.ApiError(api.METHOD_NOT_ALLOWED, f"{request.path} accepts GET")
            return Response.json(HTTPStatus.OK, api.ok_body(await self._call(name, {})))

        if request.method not in ("GET", "HEAD"):
            raise api.ApiError(api.METHOD_NOT_ALLOWED, "static files accept GET")
        if request.path == "/":
            return Response(HTTPStatus.FOUND, [("Location", "/" + exports.SYSTEMS_INDEX_PATH)])
        path = exports.static_file(self.install_root, request.path)
        if path is None:
            raise api.ApiError(api.NOT_FOUND, f"nothing served at {request.path}")
        body = await asyncio.to_thread(path.read_bytes)
        return Response(HTTPStatus.OK, [("Content-Type", _content_type(path)), ("Cache-Control", "no-cache")], body)

    async def _rpc(self, request: Request) -> Response:
        media_type = request.header("content-type").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise api.ApiError(api.UNSUPPORTED_MEDIA_TYPE, "requests must be application/json")
        if not request.header(CLIENT_HEADER):
            raise api.ApiError(api.FORBIDDEN, f"missing {CLIENT_HEADER} header")
        request_id: api.RequestId = None
        try:
            request_id, method, params = api.parse_rpc(request.body)
            result = await self._call(method, params)
        except api.ApiError as exc:
            return _error_response(exc, request_id, with_id=True)
        return Response.json(HTTPStatus.OK, api.ok_body(result, request_id))

    async def _call(self, name: str, params: dict[str, Any]) -> Any:
        try:
            return await self.methods.call(name, params)
        except api.ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the caller as an internal error
            logger.exception("API method %s failed", name)
            raise api.ApiError(api.INTERNAL, f"{name} failed: {exc}") from exc

    async def _stream_events(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        subscriber = self.hub.subscribe()
        if subscriber is None:
            await write_response(writer, _error_response(api.ApiError(api.UNAVAILABLE, "too many event streams")))
            return
        eof = asyncio.ensure_future(reader.read(1))
        try:
            writer.write(
                response_head(
                    HTTPStatus.OK,
                    [("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-store")],
                )
            )
            hello = {"pid": os.getpid(), "started_at": self._started_at, "last_seq": self.hub.last_seq}
            writer.write(f"retry: {events.RECONNECT_DELAY_MS}\n\n".encode())
            writer.write(f"event: {events.TOPIC_HELLO}\ndata: {json.dumps(hello, separators=(',', ':'))}\n\n".encode())
            await writer.drain()
            while True:
                get = asyncio.ensure_future(subscriber.queue.get())
                done, _ = await asyncio.wait({get, eof}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                    return
                event = get.result()
                if event is None:
                    return
                writer.write(event.encode())
                await writer.drain()
        finally:
            eof.cancel()
            self.hub.unsubscribe(subscriber)
