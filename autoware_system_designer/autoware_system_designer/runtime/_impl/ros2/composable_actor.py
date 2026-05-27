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

"""Composable-node actor: Blocked → Unloaded → Loading → Loaded|Failed."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ..core import events as ev
from ..core.state import (
    BlockReason,
    ComposableBlocked,
    ComposableFailed,
    ComposableLoaded,
    ComposableLoading,
    ComposableUnloaded,
)
from .params import flatten_for_fqn, to_parameter_msgs

logger = logging.getLogger(__name__)


@dataclass
class ComposableSpec:
    """Static description of a composable node load request."""

    name: str  # unique identifier (use FQN: namespace + "/" + node_name)
    package: str
    plugin: str
    node_name: str
    namespace: str
    target_container_fqn: str
    remap_rules: Sequence[tuple[str, str]] = field(default_factory=list)
    parameter_files: Sequence[str] = field(default_factory=list)
    inline_parameters: Mapping[str, Any] = field(default_factory=dict)
    extra_arguments: Mapping[str, Any] = field(default_factory=dict)
    log_level: Optional[int] = None  # composition_interfaces uses uint8


class ComposableNodeActor:
    """Loads one composable node into its container (single-shot, no retry)."""

    def __init__(
        self,
        spec: ComposableSpec,
        *,
        ros_worker: "RosWorker",
        container_ready: "asyncio.Future[int]",
        state_tx: asyncio.Queue,
        shutdown: asyncio.Event,
        service_wait_timeout: float = 30.0,
        load_call_timeout: float = 30.0,
    ) -> None:
        self._spec = spec
        self._ros_worker = ros_worker
        self._container_ready = container_ready
        self._state_tx = state_tx
        self._shutdown = shutdown
        self._service_wait_timeout = service_wait_timeout
        self._load_call_timeout = load_call_timeout
        self._state: Any = ComposableBlocked(reason=BlockReason.NOT_STARTED)

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def state(self):
        return self._state

    async def run(self) -> None:
        try:
            await self._wait_for_container()
            if self._shutdown.is_set():
                self._state = ComposableBlocked(reason=BlockReason.SHUTDOWN)
                return

            self._state = ComposableUnloaded()
            await self._emit(ev.LoadStarted(name=self.name))
            self._state = ComposableLoading(started_at=time.monotonic())

            # Race the load against shutdown so Ctrl-C is not delayed by
            # service-wait and LoadNode call timeouts (up to 30 s each).
            load_task = asyncio.ensure_future(self._load())
            shutdown_task = asyncio.create_task(self._shutdown.wait())
            try:
                await asyncio.wait(
                    {load_task, shutdown_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not shutdown_task.done():
                    shutdown_task.cancel()

            if not load_task.done():
                # Shutdown won the race — cancel the pending load and exit.
                load_task.cancel()
                try:
                    await load_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                self._state = ComposableBlocked(reason=BlockReason.SHUTDOWN)
                return  # run()'s finally block emits ev.Terminated

            try:
                unique_id = load_task.result()
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {e}"
                logger.warning("[%s] load failed: %s", self.name, msg)
                self._state = ComposableFailed(error=msg)
                await self._emit(ev.LoadFailed(name=self.name, error=msg))
                return

            self._state = ComposableLoaded(unique_id=unique_id)
            await self._emit(
                ev.LoadSucceeded(
                    name=self.name,
                    full_node_name=_join_fqn(self._spec.namespace, self._spec.node_name),
                    unique_id=unique_id,
                )
            )
        finally:
            await self._emit(ev.Terminated(name=self.name))

    # ---- internals -------------------------------------------------------

    async def _wait_for_container(self) -> None:
        # Race container_ready against shutdown so Ctrl-C doesn't hang start-up.
        shutdown_task = asyncio.create_task(self._shutdown.wait())
        try:
            await asyncio.wait(
                {shutdown_task, asyncio.ensure_future(self._container_ready)},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not shutdown_task.done():
                shutdown_task.cancel()

    async def _load(self) -> int:
        # Flatten YAML params, then merge inline overrides on top.
        if self._spec.parameter_files:
            target_fqn = _join_fqn(self._spec.namespace, self._spec.node_name)
            flat = flatten_for_fqn(self._spec.parameter_files, target_fqn)
        else:
            flat = {}
        flat.update(self._spec.inline_parameters)

        param_msgs = to_parameter_msgs(flat) if flat else []
        extra_msgs = to_parameter_msgs(self._spec.extra_arguments) if self._spec.extra_arguments else []
        remap_strs = [f"{src}:={dst}" for src, dst in self._spec.remap_rules]

        unique_id = await self._ros_worker.load_node(
            container_fqn=self._spec.target_container_fqn,
            package=self._spec.package,
            plugin=self._spec.plugin,
            node_name=self._spec.node_name,
            node_namespace=self._spec.namespace,
            remap_rules=remap_strs,
            parameters=param_msgs,
            extra_arguments=extra_msgs,
            log_level=self._spec.log_level or 0,
            service_wait_timeout=self._service_wait_timeout,
            load_call_timeout=self._load_call_timeout,
        )
        return unique_id

    async def _emit(self, event) -> None:
        try:
            await self._state_tx.put(event)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] state emit failed: %s", self.name, e)


def _join_fqn(namespace: str, node_name: str) -> str:
    ns = namespace if namespace.startswith("/") else "/" + namespace
    ns = ns.rstrip("/")
    if not node_name:
        return ns or "/"
    if node_name.startswith("/"):
        return node_name
    return f"{ns}/{node_name}" if ns else f"/{node_name}"
