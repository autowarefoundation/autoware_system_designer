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

"""Control surface of a running system, shared by the stdin console and the control channel."""

from __future__ import annotations

import signal
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

from . import events as ev

if TYPE_CHECKING:
    from ..measure.session import MeasureSession
    from .coordinator import Coordinator, CoordinatorBuilder

MEMBER_ACTIONS = ("stop", "restart", "kill")
MEASURE_VERBS = ("start", "stop", "status")


class CommandError(Exception):
    """A refused or malformed command; ``code`` is a stable string."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Guard(Protocol):
    """Interlock consulted before a member or system action; returns the refusal reason, if any."""

    def check(self, action: str) -> Optional[str]: ...


@dataclass
class MemberRecord:
    """Run state of one member: ``state`` token, its detail, and the wall-clock time of the change.

    ``restarts`` counts the starts after the first within the run, ``respawns`` the respawns among them.
    """

    path: str
    state: str
    detail: dict[str, Any] = field(default_factory=dict)
    time: float = 0.0
    restarts: int = 0
    respawns: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "state": self.state,
            "detail": dict(self.detail),
            "time": self.time,
            "restarts": self.restarts,
            "respawns": self.respawns,
        }


class MemberStates:
    """Latest run state per member, derived from the coordinator's state events; fans out each change."""

    def __init__(self) -> None:
        self._records: dict[str, MemberRecord] = {}
        # Member path -> [starts, respawns] over the whole run.
        self._counts: dict[str, list[int]] = {}
        self._listeners: list[Callable[[MemberRecord], None]] = []

    def install(self, builder: "CoordinatorBuilder") -> None:
        builder.add_state_hook(self.on_event)

    def subscribe(self, listener: Callable[[MemberRecord], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    def records(self) -> list[MemberRecord]:
        return list(self._records.values())

    def get(self, path: str) -> Optional[MemberRecord]:
        return self._records.get(path)

    def on_event(self, event: object) -> None:
        record = self._translate(event)
        if record is None:
            return
        counts = self._counts.setdefault(record.path, [0, 0])
        if isinstance(event, (ev.Started, ev.LoadStarted)):
            counts[0] += 1
        elif isinstance(event, ev.Respawning):
            counts[1] += 1
        record.restarts, record.respawns = max(counts[0] - 1, 0), counts[1]
        self._records[record.path] = record
        for listener in list(self._listeners):
            listener(record)

    def _translate(self, event: object) -> Optional[MemberRecord]:
        now = time.time()
        if isinstance(event, ev.Started):
            return MemberRecord(event.name, "running", {"pid": event.pid}, now)
        if isinstance(event, ev.Exited):
            return MemberRecord(event.name, "stopped", {"exit_code": event.exit_code}, now)
        if isinstance(event, ev.Respawning):
            return MemberRecord(event.name, "respawning", {"attempt": event.attempt, "delay": event.delay}, now)
        if isinstance(event, ev.Failed):
            return MemberRecord(event.name, "failed", {"error": event.error}, now)
        if isinstance(event, ev.LoadStarted):
            return MemberRecord(event.name, "loading", {}, now)
        if isinstance(event, ev.LoadSucceeded):
            return MemberRecord(event.name, "loaded", {"unique_id": event.unique_id, "node": event.full_node_name}, now)
        if isinstance(event, ev.LoadFailed):
            return MemberRecord(event.name, "failed", {"error": event.error}, now)
        if isinstance(event, ev.Terminated):
            current = self._records.get(event.name)
            # A composable actor's task ends once the node is loaded; the node itself lives on.
            if current is not None and current.state in ("loaded", "failed"):
                return None
            return MemberRecord(event.name, "done", dict(current.detail) if current else {}, now)
        return None


class RuntimeCommands:
    """Commands over a running system: status, member actions, the measurement window, shutdown."""

    def __init__(
        self,
        coord: "Coordinator",
        states: MemberStates,
        *,
        measure: "Optional[MeasureSession]" = None,
        guard: Optional[Guard] = None,
    ) -> None:
        self._coord = coord
        self._states = states
        self._measure = measure
        self._guard = guard

    @property
    def coord(self) -> "Coordinator":
        return self._coord

    @property
    def measure_armed(self) -> bool:
        return self._measure is not None

    def status(self) -> dict[str, Any]:
        known = {record.path: record for record in self._states.records()}
        names = self._coord.names()
        members = [(known.get(name) or MemberRecord(name, "pending")).as_dict() for name in names]
        members += [record.as_dict() for path, record in known.items() if path not in names]
        return {
            "members": members,
            "launch_ready": self._coord.launch_ready.is_set(),
            "shutting_down": self._coord.shutdown_event.is_set(),
        }

    def match(self, pattern: object, *, exact: bool = False) -> list[str]:
        """The member named *pattern*, else, unless *exact*, every member whose name contains it."""
        if not isinstance(pattern, str) or not pattern:
            raise CommandError("invalid_params", "a member name or substring is required")
        names = [name for name in self._coord.names() if pattern in name]
        if pattern in names:
            names = [pattern]
        elif exact:
            names = []
        if not names:
            raise CommandError("not_found", f"no member {'named' if exact else 'matches'} {pattern!r}")
        return names

    async def member_action(self, action: str, pattern: object, *, exact: bool = False) -> list[str]:
        if action not in MEMBER_ACTIONS:
            raise CommandError("invalid_params", f"unknown member action {action!r}")
        names = self.match(pattern, exact=exact)
        self._guard_check(action)
        for name in names:
            handle = self._coord.handle(name)
            if action == "stop":
                await handle.stop()
            elif action == "restart":
                await handle.restart()
            else:
                await handle.kill(signal.SIGKILL)
        return names

    async def measure(self, verb: str) -> dict[str, Any]:
        if self._measure is None:
            raise CommandError("unavailable", "measurement is not armed; launch with --measure")
        if verb == "start":
            message = await self._measure.start()
        elif verb == "stop":
            message = await self._measure.close("control stop")
            if self._measure.state == "closed":
                message = f"{message}; {await self._measure.analyze()}"
        elif verb == "status":
            message = self._measure.status()
        else:
            raise CommandError("invalid_params", f"measure needs start, stop or status, not {verb!r}")
        return {"state": self._measure.state, "message": message}

    def shutdown(self) -> None:
        self._guard_check("shutdown")
        self._coord.request_shutdown()

    def _guard_check(self, action: str) -> None:
        if self._guard is None:
            return
        reason = self._guard.check(action)
        if reason:
            raise CommandError("refused", reason)
