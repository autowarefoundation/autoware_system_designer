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

"""Coordinator: builder, handle, runner for the actor runtime.

Mirrors ``src/play_launch/src/member_actor/coordinator/`` — separates the
three roles:

- :class:`CoordinatorBuilder` collects member specs before spawn.
- :class:`Coordinator` runs the actors, fan-ins state events, dispatches
  control commands.
- :class:`MemberHandle` exposes external control over one actor.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import events as ev
from .config import ActorConfig
from .regular_actor import NodeSpec, RegularNodeActor

logger = logging.getLogger(__name__)


# ---- Member registration -------------------------------------------------


@dataclass
class _MemberEntry:
    name: str
    spec: NodeSpec
    config: ActorConfig
    control_q: asyncio.Queue = field(default_factory=asyncio.Queue)
    # Set when the underlying process first enters Running. Used by
    # composable-node actors to gate their LoadNode calls.
    ready_signal: "asyncio.Future[int]" = field(default=None)  # type: ignore[assignment]


class MemberHandle:
    """External control surface for one supervised member."""

    def __init__(self, entry: _MemberEntry) -> None:
        self._entry = entry

    @property
    def name(self) -> str:
        return self._entry.name

    async def stop(self) -> None:
        await self._entry.control_q.put(ev.Stop())

    async def restart(self) -> None:
        await self._entry.control_q.put(ev.Restart())

    async def kill(self, signum: int = signal.SIGKILL) -> None:
        await self._entry.control_q.put(ev.KillSignal(signum=signum))

    async def set_respawn(self, enabled: bool) -> None:
        await self._entry.control_q.put(ev.ToggleRespawn(enabled=enabled))


# ---- Coordinator ---------------------------------------------------------


class CoordinatorBuilder:
    """Collects member specs; produces a :class:`Coordinator`."""

    def __init__(self, *, default_config: Optional[ActorConfig] = None) -> None:
        self._default_config = default_config or ActorConfig()
        self._entries: dict[str, _MemberEntry] = {}
        # Post-start hooks schedule additional tasks (e.g. composable loaders) once run() is live.
        self._post_start_hooks: list[Callable[["Coordinator"], Awaitable[None]]] = []

    @property
    def default_config(self) -> ActorConfig:
        return self._default_config

    def add_node(
        self,
        spec: NodeSpec,
        *,
        config: Optional[ActorConfig] = None,
    ) -> _MemberEntry:
        if spec.name in self._entries:
            raise ValueError(f"duplicate member name: {spec.name!r}")
        if spec.on_first_running is None:
            spec.on_first_running = asyncio.get_running_loop().create_future()
        entry = _MemberEntry(
            name=spec.name,
            spec=spec,
            config=config or self._default_config,
            ready_signal=spec.on_first_running,
        )
        self._entries[spec.name] = entry
        return entry

    def add_post_start_hook(self, hook: Callable[["Coordinator"], Awaitable[None]]) -> None:
        """Register a callback invoked once event processing is live."""
        self._post_start_hooks.append(hook)

    def build(self) -> "Coordinator":
        return Coordinator(self._entries, list(self._post_start_hooks))


class Coordinator:
    """Runs all registered actors and pumps state events."""

    def __init__(
        self,
        entries: dict[str, _MemberEntry],
        post_start_hooks: list[Callable[["Coordinator"], Awaitable[None]]],
    ) -> None:
        self._entries = entries
        self._post_start_hooks = post_start_hooks
        self._state_q: asyncio.Queue = asyncio.Queue()
        self._shutdown = asyncio.Event()
        self._actor_tasks: list[asyncio.Task] = []
        self._extra_tasks: list[asyncio.Task] = []
        self._actor_pids: dict[str, int] = {}
        self._had_failure: bool = False

    # ---- public control surface ------------------------------------------

    def handle(self, name: str) -> MemberHandle:
        return MemberHandle(self._entries[name])

    def names(self) -> list[str]:
        return list(self._entries)

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def schedule_task(self, coro: Awaitable[None]) -> asyncio.Task:
        """Track an extra task (e.g. composable-node loader) for shutdown."""
        task = asyncio.ensure_future(coro)
        self._extra_tasks.append(task)
        return task

    def ready_signal(self, name: str) -> "asyncio.Future[int]":
        return self._entries[name].ready_signal

    @property
    def state_queue(self) -> asyncio.Queue:
        """Shared state-event queue, for actors spawned via :meth:`schedule_task`."""
        return self._state_q

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._shutdown

    # ---- main loop -------------------------------------------------------

    async def run(self) -> int:
        self._install_signal_handlers()

        # Spawn every actor first so each member has a queue to receive control.
        for entry in self._entries.values():
            actor = RegularNodeActor(
                spec=entry.spec,
                config=entry.config,
                control_rx=entry.control_q,
                state_tx=self._state_q,
                shutdown=self._shutdown,
            )
            self._actor_tasks.append(asyncio.ensure_future(actor.run()))

        # Now let hooks (containers/composables) attach themselves.
        for hook in self._post_start_hooks:
            try:
                await hook(self)
            except Exception:  # noqa: BLE001
                logger.exception("post-start hook failed")

        # Pump events until every regular actor reports Terminated.
        # Composable actors also emit Terminated — exclude them from the count
        # so a loaded system doesn't shut down when composables finish loading.
        actor_names = set(self._entries.keys())
        terminated: set = set()
        total = len(self._actor_tasks)
        shutdown_deadline: Optional[float] = None
        loop = asyncio.get_running_loop()
        try:
            while len(terminated) < total or self._extra_tasks_alive():
                if not self._actor_tasks and not self._extra_tasks_alive():
                    break

                if self._shutdown.is_set():
                    if shutdown_deadline is None:
                        max_grace = max(
                            (e.config.graceful_shutdown_timeout for e in self._entries.values()),
                            default=5.0,
                        )
                        shutdown_deadline = loop.time() + max_grace + 5.0
                        remaining = sorted(set(actor_names) - terminated)
                        logger.info(
                            "graceful shutdown: waiting for %d actor(s): %s",
                            len(remaining),
                            remaining,
                        )

                    time_left = shutdown_deadline - loop.time()
                    timed_out = time_left <= 0
                    if not timed_out:
                        try:
                            event = await asyncio.wait_for(self._state_q.get(), timeout=time_left)
                        except asyncio.TimeoutError:
                            timed_out = True
                    if timed_out:
                        remaining = sorted(set(actor_names) - terminated)
                        logger.warning(
                            "shutdown timed out; %d actor(s) did not terminate: %s",
                            len(remaining),
                            remaining,
                        )
                        break
                else:
                    event = await self._state_q.get()

                self._log_event(event)
                if isinstance(event, ev.Failed):
                    self._had_failure = True
                    self._shutdown.set()  # cascade: one node failure shuts down everything
                elif isinstance(event, ev.Terminated) and event.name in actor_names:
                    terminated.add(event.name)
                    if self._shutdown.is_set():
                        left = total - len(terminated)
                        if left:
                            logger.info(
                                "shutdown: %d/%d actor(s) terminated, %d remaining",
                                len(terminated),
                                total,
                                left,
                            )
                        else:
                            logger.info("all %d actor(s) terminated", total)
        finally:
            await self._teardown()

        return 1 if self._had_failure else 0

    # ---- helpers ---------------------------------------------------------

    def _extra_tasks_alive(self) -> bool:
        return any(not t.done() for t in self._extra_tasks)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._on_signal, sig)
            except NotImplementedError:
                # Windows / non-default loop — fall back to default handlers
                pass

    def _on_signal(self, sig: int) -> None:
        if not self._shutdown.is_set():
            logger.info("received %s, shutting down actors", _signame(sig))
            print(
                f"\nReceived {_signame(sig)} — graceful shutdown in progress. " "Press Ctrl+C again to force quit.",
                flush=True,
            )
            self._shutdown.set()
        else:
            logger.warning("received second %s, force-killing all actors", _signame(sig))
            print("\nForce-killing all processes.", flush=True)
            for pid in list(self._actor_pids.values()):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
            for t in self._actor_tasks:
                t.cancel()

    def _log_event(self, event) -> None:
        if isinstance(event, ev.Started):
            logger.info("[%s] started pid=%d", event.name, event.pid)
            self._actor_pids[event.name] = event.pid
        elif isinstance(event, ev.Exited):
            logger.info("[%s] exited code=%s", event.name, event.exit_code)
            self._actor_pids.pop(event.name, None)
        elif isinstance(event, ev.Respawning):
            logger.warning(
                "[%s] respawning attempt=%d delay=%.1fs",
                event.name,
                event.attempt,
                event.delay,
            )
        elif isinstance(event, ev.Failed):
            logger.error("[%s] failed: %s", event.name, event.error)
        elif isinstance(event, ev.LoadSucceeded):
            logger.info(
                "[%s] loaded into container (unique_id=%d)",
                event.name,
                event.unique_id,
            )
        elif isinstance(event, ev.LoadFailed):
            logger.error("[%s] load failed: %s", event.name, event.error)
        elif isinstance(event, ev.Blocked):
            logger.debug("[%s] blocked: %s", event.name, event.reason.value)

    async def _teardown(self) -> None:
        # Ensure shutdown is set on any exception path; actors wait on this event.
        self._shutdown.set()

        # Cancel auxiliary tasks (composable loaders, console, etc.).
        for t in self._extra_tasks:
            if not t.done():
                t.cancel()
        for t in self._extra_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # Drain actor tasks, bounded so a hung process can't block teardown.
        if self._actor_tasks:
            max_grace = max(
                (e.config.graceful_shutdown_timeout for e in self._entries.values()),
                default=5.0,
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._actor_tasks, return_exceptions=True),
                    timeout=max_grace + 5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("actor tasks did not complete within teardown timeout, cancelling")
                for t in self._actor_tasks:
                    t.cancel()
                await asyncio.gather(*self._actor_tasks, return_exceptions=True)


def _signame(sig: int) -> str:
    try:
        return signal.Signals(sig).name
    except (ValueError, AttributeError):
        return str(sig)


def ensure_output_dir(base: Optional[Path] = None) -> Path:
    """Create a timestamped log directory under *base* (or /tmp by default)."""
    base = base or Path("/tmp/autoware_system_designer_logs")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = base / stamp
    out.mkdir(parents=True, exist_ok=True)
    return out
