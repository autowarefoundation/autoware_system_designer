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

"""ROS worker — shared rclpy node + LoadNode service clients.

rclpy runs on a dedicated thread; ``load_node()`` bridges to asyncio.
This is the only module that imports ``rclpy`` / ``composition_interfaces``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


class RosWorker:
    """Owns the rclpy node and serves LoadNode requests on a dedicated thread."""

    NODE_NAME = "autoware_system_designer_launcher"

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._node = None  # rclpy.node.Node
        self._executor = None  # rclpy.executors.Executor
        self._clients: "dict[str, object]" = {}
        self._clients_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._thread is not None:
            return
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._thread = threading.Thread(target=self._run, name="ros-worker", daemon=True)
        self._thread.start()
        # Wait for the rclpy node to come up without blocking the event loop.
        ok = await loop.run_in_executor(None, self._ready.wait, 10.0)
        if not ok:
            raise RuntimeError("rclpy worker failed to initialize within 10s")

    async def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        if self._executor is not None:
            try:
                self._executor.wake()
            except Exception:  # noqa: BLE001
                pass
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._thread.join, 5.0)
        self._thread = None

    def _run(self) -> None:
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
        except ImportError as e:
            logger.error("rclpy not available: %s", e)
            self._ready.set()
            return

        try:
            if not rclpy.ok():
                rclpy.init()
            self._node = rclpy.create_node(self.NODE_NAME)
            self._executor = SingleThreadedExecutor()
            self._executor.add_node(self._node)
            self._ready.set()

            while not self._stop.is_set():
                self._executor.spin_once(timeout_sec=0.1)
        except Exception:  # noqa: BLE001
            logger.exception("ros worker crashed")
            self._ready.set()
        finally:
            try:
                if self._node is not None:
                    self._node.destroy_node()
                # rclpy.shutdown() sends DDS goodbye immediately rather than waiting for the lease timeout.
                if rclpy.ok():
                    rclpy.shutdown()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # LoadNode dispatch
    # ------------------------------------------------------------------

    async def load_node(
        self,
        *,
        container_fqn: str,
        package: str,
        plugin: str,
        node_name: str,
        node_namespace: str,
        remap_rules: Iterable[str],
        parameters: list,
        extra_arguments: list,
        log_level: int = 0,
        service_wait_timeout: float = 30.0,
        load_call_timeout: float = 30.0,
    ) -> int:
        from composition_interfaces.srv import LoadNode

        # Build request on the calling thread (cheap, no ROS state involved).
        req = LoadNode.Request()
        req.package_name = package
        req.plugin_name = plugin
        req.node_name = node_name
        req.node_namespace = node_namespace
        req.log_level = log_level
        req.remap_rules = list(remap_rules)
        req.parameters = parameters
        req.extra_arguments = extra_arguments

        client = self._client_for(container_fqn)

        # Wait for the container's load_node service to come online.
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, client.wait_for_service, service_wait_timeout)
        if not ok:
            raise TimeoutError(
                f"service {container_fqn}/_container/load_node " f"not available within {service_wait_timeout}s"
            )

        # Bridge rclpy Future to asyncio.
        rcl_future = client.call_async(req)
        aio_future: "asyncio.Future" = loop.create_future()

        def _on_done(f) -> None:
            try:
                result = f.result()
            except Exception as e:  # noqa: BLE001
                loop.call_soon_threadsafe(aio_future.set_exception, e)
                return
            loop.call_soon_threadsafe(aio_future.set_result, result)

        rcl_future.add_done_callback(_on_done)

        try:
            response = await asyncio.wait_for(aio_future, timeout=load_call_timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"LoadNode call for {node_namespace}/{node_name} " f"timed out after {load_call_timeout}s"
            )

        if not response.success:
            raise RuntimeError(f"LoadNode rejected: {response.error_message or '(no error message)'}")

        return int(response.unique_id)

    def _client_for(self, container_fqn: str):
        from composition_interfaces.srv import LoadNode

        service_name = f"{container_fqn.rstrip('/')}/_container/load_node"
        with self._clients_lock:
            client = self._clients.get(service_name)
            if client is None:
                if self._node is None:
                    raise RuntimeError("ros worker not running")
                client = self._node.create_client(LoadNode, service_name)
                self._clients[service_name] = client
            return client
