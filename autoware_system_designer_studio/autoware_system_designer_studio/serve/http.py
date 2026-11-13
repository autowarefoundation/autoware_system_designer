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

"""Minimal HTTP/1.1 on asyncio streams: one request per connection, fixed-length bodies only.

Shares the event loop with the runtime's actors, so a long-lived response (the event stream)
costs a task, not a thread.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from email.utils import formatdate
from http import HTTPStatus
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit

LOOPBACK_HOST_NAMES = ("127.0.0.1", "localhost")
CLIENT_HEADER = "X-System-Designer-Client"
MAX_HEADER_BYTES = 16 * 1024
MAX_BODY_BYTES = 64 * 1024
HEADER_TIMEOUT_S = 10.0
BODY_TIMEOUT_S = 10.0
WRITE_CHUNK_BYTES = 64 * 1024

# Sent on every response; the daemon's pages are never embedded or sniffed.
BASE_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Connection", "close"),
)


class HttpError(Exception):
    """Request rejected before routing; carries the status the connection answers with."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes = b""

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


@dataclass
class Response:
    status: HTTPStatus
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""

    @classmethod
    def json(cls, status: HTTPStatus, payload: Any, headers: Optional[list[tuple[str, str]]] = None) -> "Response":
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        return cls(
            status,
            [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"), *(headers or [])],
            body,
        )


async def read_request(reader: asyncio.StreamReader) -> Request:
    """Parse one request head and its Content-Length body; chunked request bodies are refused."""
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_S)
    except asyncio.LimitOverrunError as exc:
        raise HttpError(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE, "request head too large") from exc
    except asyncio.IncompleteReadError as exc:
        raise ConnectionError("connection closed before the request head") from exc

    try:
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ")
    except ValueError as exc:
        raise HttpError(HTTPStatus.BAD_REQUEST, "malformed request line") from exc
    if version not in ("HTTP/1.0", "HTTP/1.1"):
        raise HttpError(HTTPStatus.HTTP_VERSION_NOT_SUPPORTED, "unsupported HTTP version")

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name or name != name.strip():
            raise HttpError(HTTPStatus.BAD_REQUEST, "malformed header")
        key = name.lower()
        if key in headers and key in ("host", "content-length", "content-type", "origin"):
            raise HttpError(HTTPStatus.BAD_REQUEST, f"duplicate {name} header")
        headers[key] = value.strip()

    if "transfer-encoding" in headers:
        raise HttpError(HTTPStatus.NOT_IMPLEMENTED, "Transfer-Encoding is not supported")
    body = b""
    if "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from exc
        if length < 0:
            raise HttpError(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
        if length > MAX_BODY_BYTES:
            raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
        try:
            body = await asyncio.wait_for(reader.readexactly(length), BODY_TIMEOUT_S)
        except asyncio.IncompleteReadError as exc:
            raise ConnectionError("connection closed inside the request body") from exc

    if not target.startswith("/"):
        raise HttpError(HTTPStatus.BAD_REQUEST, "only origin-form request targets are accepted")
    split = urlsplit(target)
    return Request(method, unquote(split.path), parse_qs(split.query), headers, body)


def response_head(status: HTTPStatus, headers: list[tuple[str, str]]) -> bytes:
    lines = [f"HTTP/1.1 {status.value} {status.phrase}", f"Date: {formatdate(usegmt=True)}"]
    lines += [f"{name}: {value}" for name, value in (*headers, *BASE_HEADERS)]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


async def write_response(writer: asyncio.StreamWriter, response: Response, *, head_only: bool = False) -> None:
    headers = [*response.headers, ("Content-Length", str(len(response.body)))]
    writer.write(response_head(response.status, headers))
    if not head_only:
        for start in range(0, len(response.body), WRITE_CHUNK_BYTES):
            writer.write(response.body[start : start + WRITE_CHUNK_BYTES])
            await writer.drain()
    await writer.drain()
