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

"""Request/response envelope and method registry of the daemon API.

Envelope (shared with the ExteVerse sidecar JSON-RPC shape):
request ``{"id", "method", "params"}`` -> ``{"id", "ok": true, "result"}`` or
``{"id", "ok": false, "error": {"code", "message"[, "data"]}}``. A GET alias of a method answers
with the same body minus ``id``. Error codes are stable strings; each maps to one HTTP status.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, Awaitable, Callable, Optional, Union

RequestId = Union[str, int, None]
Handler = Callable[[dict[str, Any]], Awaitable[Any]]

BAD_REQUEST = "bad_request"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
METHOD_NOT_ALLOWED = "method_not_allowed"
UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
PAYLOAD_TOO_LARGE = "payload_too_large"
UNKNOWN_METHOD = "unknown_method"
INVALID_PARAMS = "invalid_params"
CONFLICT = "conflict"
# A runtime action refused by its safe-state interlock.
REFUSED = "refused"
UNAVAILABLE = "unavailable"
INTERNAL = "internal"

ERROR_STATUS = {
    BAD_REQUEST: HTTPStatus.BAD_REQUEST,
    FORBIDDEN: HTTPStatus.FORBIDDEN,
    NOT_FOUND: HTTPStatus.NOT_FOUND,
    METHOD_NOT_ALLOWED: HTTPStatus.METHOD_NOT_ALLOWED,
    UNSUPPORTED_MEDIA_TYPE: HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    PAYLOAD_TOO_LARGE: HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    UNKNOWN_METHOD: HTTPStatus.NOT_FOUND,
    INVALID_PARAMS: HTTPStatus.BAD_REQUEST,
    CONFLICT: HTTPStatus.CONFLICT,
    REFUSED: HTTPStatus.CONFLICT,
    UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    INTERNAL: HTTPStatus.INTERNAL_SERVER_ERROR,
}

# Transport-level rejections reported in the same envelope.
_CODE_BY_STATUS = {
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: PAYLOAD_TOO_LARGE,
    HTTPStatus.SERVICE_UNAVAILABLE: UNAVAILABLE,
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    @property
    def status(self) -> HTTPStatus:
        return ERROR_STATUS.get(self.code, HTTPStatus.INTERNAL_SERVER_ERROR)

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


def code_for_status(status: HTTPStatus) -> str:
    return _CODE_BY_STATUS.get(status, BAD_REQUEST)


_NO_ID = object()


def ok_body(result: Any, request_id: Any = _NO_ID) -> dict[str, Any]:
    body: dict[str, Any] = {} if request_id is _NO_ID else {"id": request_id}
    body.update(ok=True, result=result)
    return body


def error_body(error: ApiError, request_id: Any = _NO_ID) -> dict[str, Any]:
    body: dict[str, Any] = {} if request_id is _NO_ID else {"id": request_id}
    body.update(ok=False, error=error.to_dict())
    return body


def parse_rpc(body: bytes) -> tuple[RequestId, str, dict[str, Any]]:
    """Validated (id, method, params) of one request envelope."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(BAD_REQUEST, f"request body is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ApiError(BAD_REQUEST, "request body must be a JSON object")
    request_id = payload.get("id")
    if request_id is not None and (isinstance(request_id, bool) or not isinstance(request_id, (str, int))):
        raise ApiError(BAD_REQUEST, "id must be a string, an integer or null")
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise ApiError(BAD_REQUEST, "method must be a non-empty string")
    params = payload.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ApiError(INVALID_PARAMS, "params must be a JSON object")
    return request_id, method, params


class MethodRegistry:
    """Named API methods; some are also reachable as ``GET /api/<path>`` with empty params."""

    def __init__(self) -> None:
        self._methods: dict[str, Handler] = {}
        self._get_aliases: dict[str, str] = {}

    def register(self, name: str, handler: Handler, *, get_path: Optional[str] = None) -> None:
        if name in self._methods:
            raise ValueError(f"API method {name!r} registered twice")
        self._methods[name] = handler
        if get_path is not None:
            self._get_aliases[get_path] = name

    def method_for_get(self, path: str) -> Optional[str]:
        return self._get_aliases.get(path)

    def names(self) -> list[str]:
        return sorted(self._methods)

    async def call(self, name: str, params: dict[str, Any]) -> Any:
        handler = self._methods.get(name)
        if handler is None:
            raise ApiError(UNKNOWN_METHOD, f"unknown method {name!r}")
        return await handler(params)
