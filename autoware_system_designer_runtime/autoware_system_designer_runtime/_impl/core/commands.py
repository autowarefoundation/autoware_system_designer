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

import asyncio
import signal
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

from ...control_protocol import MEASURE_VERBS, MEMBER_ACTIONS
from ..measure.tracer import FLAGS
from . import events as ev

if TYPE_CHECKING:
    from ..measure.monitor import RateMonitor
    from ..measure.resources import ResourceMonitor
    from ..measure.session import MeasureSession
    from ..measure.tracer import TracerSwitches
    from .coordinator import Coordinator, CoordinatorBuilder


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
        monitor: "Optional[RateMonitor]" = None,
        guard: Optional[Guard] = None,
        resources: "Optional[ResourceMonitor]" = None,
        switches: "Optional[TracerSwitches]" = None,
    ) -> None:
        self._coord = coord
        self._states = states
        self._measure = measure
        self._monitor = monitor
        self._guard = guard
        self._resources = resources
        self._switches = switches

    @property
    def coord(self) -> "Coordinator":
        return self._coord

    @property
    def measure_armed(self) -> bool:
        return self._measure is not None

    @property
    def monitor(self) -> "Optional[RateMonitor]":
        return self._monitor

    def tracer_options(self) -> Optional[dict[str, bool]]:
        """State of the tracer's size switch; None without the tracer."""
        return self._switches.state() if self._switches is not None else None

    def want_tracer_options(self, owner: object, params: dict[str, Any]) -> tuple[dict[str, bool], bool]:
        """Sets what ``owner`` wants of the switches named in ``params``; returns the state and whether
        it changed."""
        if self._switches is None:
            raise CommandError("unavailable", "tracer options need the tracer, which this launch did not preload")
        wants: dict[str, bool] = {}
        for name in FLAGS:
            if name in params:
                if not isinstance(params[name], bool):
                    raise CommandError("invalid_params", f"{name} must be a boolean")
                wants[name] = params[name]
        changed = self._switches.want(owner, wants)
        return self._switches.state(), changed

    def release_tracer_options(self, owner: object) -> bool:
        return self._switches.release(owner) if self._switches is not None else False

    @property
    def resources(self) -> "ResourceMonitor":
        """CPU, memory and GPU use of the running members; created on first use."""
        if self._resources is None:
            from ..measure.resources import ResourceMonitor

            self._resources = ResourceMonitor(self.running_pids)
        return self._resources

    def running_pids(self) -> dict[str, int]:
        """Leader pid of every member process that runs."""
        pids: dict[str, int] = {}
        for record in self._states.records():
            pid = record.detail.get("pid")
            if record.state == "running" and isinstance(pid, int):
                pids[record.path] = pid
        return pids

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
        act = getattr(self, f"_member_{action}")
        for name in names:
            await act(self._coord.handle(name))
        return names

    # One ``_member_<action>`` per member action, one ``_measure_<verb>`` per measure verb.

    @staticmethod
    async def _member_stop(handle: Any) -> None:
        await handle.stop()

    @staticmethod
    async def _member_restart(handle: Any) -> None:
        await handle.restart()

    @staticmethod
    async def _member_kill(handle: Any) -> None:
        await handle.kill(signal.SIGKILL)

    async def measure(self, verb: str) -> dict[str, Any]:
        if self._measure is None:
            raise CommandError("unavailable", "measurement needs the tracer, which this launch did not preload")
        if verb not in MEASURE_VERBS:
            raise CommandError("invalid_params", f"measure needs one of {', '.join(MEASURE_VERBS)}, not {verb!r}")
        message = await getattr(self, f"_measure_{verb}")(self._measure)
        return {"state": self._measure.state, "message": message}

    @staticmethod
    async def _measure_start(session: "MeasureSession") -> str:
        return await session.start()

    @staticmethod
    async def _measure_stop(session: "MeasureSession") -> str:
        message = await session.close("control stop")
        if session.state == "closed":
            message = f"{message}; {await session.analyze()}"
        return message

    @staticmethod
    async def _measure_status(session: "MeasureSession") -> str:
        return session.status()

    async def rates(self, interval: float = 1.0) -> dict[str, Any]:
        """Per-node rates over *interval* seconds from the tracer's counters."""
        if self._monitor is None:
            raise CommandError("unavailable", "live rates need the tracer, which this launch did not preload")
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not 0.1 <= interval <= 60:
            raise CommandError("invalid_params", "interval must be a number of seconds in [0.1, 60]")
        before = self._monitor.read()
        await asyncio.sleep(interval)
        return self._monitor.rates(before, self._monitor.read())

    async def resource_usage(self, interval: float = 1.0) -> dict[str, Any]:
        """CPU, memory and GPU use of the running members over *interval* seconds."""
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not 0.1 <= interval <= 60:
            raise CommandError("invalid_params", "interval must be a number of seconds in [0.1, 60]")
        monitor = self.resources
        loop = asyncio.get_running_loop()
        before = await loop.run_in_executor(None, monitor.read)
        await asyncio.sleep(interval)
        return monitor.usage(before, await loop.run_in_executor(None, monitor.read))

    def shutdown(self) -> None:
        self._guard_check("shutdown")
        self._coord.request_shutdown()

    def _guard_check(self, action: str) -> None:
        if self._guard is None:
            return
        reason = self._guard.check(action)
        if reason:
            raise CommandError("refused", reason)
