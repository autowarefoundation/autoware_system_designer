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

"""Regular-node actor.

Mirrors ``src/play_launch/src/member_actor/regular_node_actor.rs``: one
asyncio task per supervised member, owning a Popen handle, transitioning
through :class:`~.state.NodePending`, :class:`~.state.NodeRunning`,
:class:`~.state.NodeRespawning`, :class:`~.state.NodeStopped`,
:class:`~.state.NodeFailed`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from . import events as ev
from .config import ActorConfig
from .process import graceful_kill, spawn_pgrp
from .state import (
    NodeFailed,
    NodePending,
    NodeRespawning,
    NodeRunning,
    NodeStopped,
    is_terminal_node,
)

logger = logging.getLogger(__name__)


@dataclass
class NodeSpec:
    """Static description of a regular node (or container) for the actor."""

    name: str  # unique, used for routing and log directory
    cmd: Sequence[str]
    env: Optional[Mapping[str, str]] = None
    cwd: Optional[Path] = None
    # Resolved with the PID the first time the process enters Running;
    # awaited by composable actors before submitting LoadNode.
    on_first_running: Optional["asyncio.Future[int]"] = field(default=None, repr=False)


class RegularNodeActor:
    """Asyncio actor supervising one OS process."""

    def __init__(
        self,
        spec: NodeSpec,
        config: ActorConfig,
        control_rx: asyncio.Queue,
        state_tx: asyncio.Queue,
        shutdown: asyncio.Event,
    ) -> None:
        self._spec = spec
        self._config = config
        self._control_rx = control_rx
        self._state_tx = state_tx
        self._shutdown = shutdown
        self._state = NodePending()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._respawn_enabled = config.respawn_enabled
        self._first_running_signaled = False
        self._respawn_count: int = 0

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def state(self):
        return self._state

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        try:
            while not is_terminal_node(self._state):
                if isinstance(self._state, NodePending):
                    await self._handle_pending()
                elif isinstance(self._state, NodeRunning):
                    await self._handle_running()
                elif isinstance(self._state, NodeRespawning):
                    await self._handle_respawning()
        finally:
            # put_nowait fallback: CancelledError must not silently drop the Terminated event.
            try:
                await self._emit(ev.Terminated(name=self.name))
            except (asyncio.CancelledError, Exception):
                try:
                    self._state_tx.put_nowait(ev.Terminated(name=self.name))
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    async def _handle_pending(self) -> None:
        if self._shutdown.is_set():
            self._transition_stopped(None)
            return

        # Drain stale Stop commands that arrived while we were not Running.
        while not self._control_rx.empty():
            cmd = self._control_rx.get_nowait()
            if isinstance(cmd, ev.Stop):
                self._transition_stopped(None)
                return
            # All other commands (Restart, KillSignal, …) are irrelevant before spawn.

        node_dir = self._config.output_dir / _slug(self.name)
        node_dir.mkdir(parents=True, exist_ok=True)
        (node_dir / "cmdline").write_text(" ".join(self._spec.cmd) + "\n")

        try:
            self._proc = await spawn_pgrp(
                self._spec.cmd,
                cwd=self._spec.cwd,
                env=self._spec.env,
                stdout_path=node_dir / "out",
                stderr_path=node_dir / "err",
            )
        except FileNotFoundError as e:
            logger.error("[%s] executable not found: %s", self.name, e)
            await self._emit(ev.Failed(name=self.name, error=f"spawn failed: {e}"))
            self._state = NodeFailed(error=str(e))
            return
        except OSError as e:
            logger.error("[%s] spawn failed: %s", self.name, e)
            if not self._respawn_enabled:
                await self._emit(ev.Failed(name=self.name, error=f"spawn failed: {e}"))
                self._state = NodeFailed(error=str(e))
            else:
                await self._transition_respawning(None)
            return

        pid = self._proc.pid
        (node_dir / "pid").write_text(f"{pid}\n")
        self._state = NodeRunning(pid=pid)
        await self._emit(ev.Started(name=self.name, pid=pid))

        if not self._first_running_signaled and self._spec.on_first_running is not None:
            self._first_running_signaled = True
            if not self._spec.on_first_running.done():
                self._spec.on_first_running.set_result(pid)

    async def _handle_running(self) -> None:
        assert self._proc is not None
        wait_task = asyncio.create_task(self._proc.wait())
        ctrl_task = asyncio.create_task(self._control_rx.get())
        shutdown_task = asyncio.create_task(self._shutdown.wait())

        try:
            done, _pending = await asyncio.wait(
                {wait_task, ctrl_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (wait_task, ctrl_task, shutdown_task):
                if not t.done():
                    t.cancel()

        if wait_task in done:
            exit_code = wait_task.result()
            # Requeue a simultaneous control command; don't discard it on process exit.
            if ctrl_task in done:
                self._control_rx.put_nowait(ctrl_task.result())
            await self._emit(ev.Exited(name=self.name, exit_code=exit_code))
            if self._respawn_enabled and not self._shutdown.is_set():
                await self._transition_respawning(exit_code)
            else:
                self._transition_stopped(exit_code)
            return

        if shutdown_task in done:
            if ctrl_task in done:
                self._control_rx.put_nowait(ctrl_task.result())
            await self._stop_proc()
            return

        # Control event
        cmd = ctrl_task.result()
        await self._handle_control(cmd)

    async def _handle_respawning(self) -> None:
        assert isinstance(self._state, NodeRespawning)
        await self._emit(
            ev.Respawning(
                name=self.name,
                attempt=self._state.attempt,
                delay=self._config.respawn_delay,
            )
        )
        delay_task = asyncio.create_task(asyncio.sleep(self._config.respawn_delay))
        ctrl_task = asyncio.create_task(self._control_rx.get())
        shutdown_task = asyncio.create_task(self._shutdown.wait())
        try:
            done, _ = await asyncio.wait(
                {delay_task, ctrl_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (delay_task, ctrl_task, shutdown_task):
                if not t.done():
                    t.cancel()

        if shutdown_task in done:
            self._transition_stopped(self._state.exit_code)
            return

        if ctrl_task in done:
            cmd = ctrl_task.result()
            if isinstance(cmd, ev.Stop):
                self._transition_stopped(self._state.exit_code)
                return
            # Other commands (Restart, KillSignal, …) — proceed to spawn.

        # Delay elapsed or non-Stop command received; re-enter Pending to spawn.
        self._state = NodePending()

    # ------------------------------------------------------------------
    # Control handling
    # ------------------------------------------------------------------

    async def _handle_control(self, cmd) -> None:
        if isinstance(cmd, ev.Stop):
            await self._stop_proc()
        elif isinstance(cmd, ev.Restart):
            self._respawn_count = 0  # user-initiated restart resets the counter
            await self._stop_proc(then_respawn=True)
        elif isinstance(cmd, ev.ToggleRespawn):
            self._respawn_enabled = cmd.enabled
        elif isinstance(cmd, ev.KillSignal):
            await self._send_signal(cmd.signum)
        else:
            logger.debug("[%s] ignoring control %r in Running", self.name, cmd)

    async def _send_signal(self, signum: int) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            os.killpg(self._proc.pid, signum)
        except OSError as e:
            logger.warning("[%s] kill signal %d failed: %s", self.name, signum, e)

    async def _stop_proc(self, *, then_respawn: bool = False) -> None:
        if self._proc is None:
            self._transition_stopped(None)
            return
        exit_code = await graceful_kill(
            self._proc,
            name=self.name,
            timeout=self._config.graceful_shutdown_timeout,
        )
        await self._emit(ev.Exited(name=self.name, exit_code=exit_code))
        if then_respawn and not self._shutdown.is_set():
            await self._transition_respawning(exit_code)
        else:
            self._transition_stopped(exit_code)

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def _transition_stopped(self, exit_code: Optional[int]) -> None:
        self._state = NodeStopped(exit_code=exit_code)

    async def _transition_respawning(self, exit_code: Optional[int]) -> None:
        max_attempts = self._config.max_respawn_attempts
        if max_attempts is not None and self._respawn_count >= max_attempts:
            err = f"max respawn attempts ({max_attempts}) reached"
            await self._emit(ev.Failed(name=self.name, error=err))
            self._state = NodeFailed(error=err)
            return
        self._state = NodeRespawning(exit_code=exit_code, attempt=self._respawn_count)
        self._respawn_count += 1

    async def _emit(self, event) -> None:
        try:
            await self._state_tx.put(event)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] state emit failed: %s", self.name, e)


def _slug(name: str) -> str:
    """Sanitize a member name into a filesystem-safe path segment."""
    safe = []
    for ch in name:
        if ch.isalnum() or ch in ("_", "-", "."):
            safe.append(ch)
        else:
            safe.append("_")
    out = "".join(safe).lstrip("_")
    return out or "_unnamed_"
