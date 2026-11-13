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

"""Command-line builder and ROS worker for node_container launch type.

RosWorker owns the rclpy node and serves LoadNode requests on a dedicated
thread. rclpy runs on a dedicated thread; ``load_node()`` bridges to asyncio.
This is the only module that imports ``rclpy`` / ``composition_interfaces``.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Callable, Iterable, Mapping, Optional

from ...core.aio import resolve
from ..common.params import _ros_args, build_cmd

logger = logging.getLogger(__name__)

# Poll period of the LoadNode service and response waits; bounds how late a shutdown is noticed.
WAIT_POLL_S = 1.0


def container_cmdline(spec: Mapping) -> list[str]:
    launcher = spec["launcher"]
    cmd = build_cmd(launcher)
    cmd += _ros_args(
        name=spec["name"],
        namespace=spec["namespace"],
        parameters=[],
        remaps=[],
    )
    return cmd


class RosWorker:
    """Owns the rclpy node and serves LoadNode requests on a dedicated thread."""

    NODE_NAME = "autoware_system_designer_launcher"

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._crashed = threading.Event()
        self._node = None  # rclpy.node.Node
        self._executor = None  # rclpy.executors.Executor
        self._clients: "dict[str, object]" = {}
        self._clients_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._init_error: Optional[Exception] = None
        # Callables run on the rclpy thread between spins; entity creation happens there.
        self._jobs: "queue.Queue[tuple[Callable[[], object], asyncio.Future]]" = queue.Queue()
        self._probes: list = []

    @property
    def node(self):
        """The rclpy node; entities on it are created on the ros thread."""
        return self._node

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
        ok = await loop.run_in_executor(None, self._ready.wait, 10.0)
        if not ok:
            raise RuntimeError("rclpy worker failed to initialize within 10s")
        if self._init_error is not None:
            raise RuntimeError(f"rclpy worker failed to start: {self._init_error}") from self._init_error

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
            from rclpy.signals import SignalHandlerOptions
        except ImportError as e:
            logger.error("rclpy not available: %s", e)
            self._init_error = RuntimeError(f"rclpy not available: {e}")
            self._ready.set()
            return

        try:
            if not rclpy.ok():
                # The coordinator owns SIGINT/SIGTERM; the context lives until the worker stops.
                rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
            self._node = rclpy.create_node(self.NODE_NAME)
            self._executor = make_executor()
            self._executor.add_node(self._node)
            self._ready.set()

            while not self._stop.is_set():
                self._run_jobs()
                try:
                    self._executor.spin_once(timeout_sec=0.1)
                except Exception:  # noqa: BLE001
                    # A failing callback costs only its own message; a lost context still ends the worker.
                    if not rclpy.ok():
                        raise
                    logger.exception("ros worker callback failed")
        except Exception as exc:  # noqa: BLE001
            logger.exception("ros worker crashed")
            self._init_error = exc
            self._ready.set()
        finally:
            try:
                if self._node is not None:
                    self._node.destroy_node()
                if rclpy.ok():
                    rclpy.shutdown()
            except Exception:  # noqa: BLE001
                pass
            if not self._stop.is_set():
                logger.error("ros worker exited unexpectedly")
                self._crashed.set()

    def _run_jobs(self) -> None:
        while True:
            try:
                fn, future = self._jobs.get_nowait()
            except queue.Empty:
                return
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self._loop.call_soon_threadsafe(resolve, future, None, exc)
            else:
                self._loop.call_soon_threadsafe(resolve, future, result, None)

    async def run_on_ros_thread(self, fn: Callable[[], object], timeout: float = 10.0):
        """Run *fn* on the rclpy thread and return its result."""
        if self._thread is None or self._loop is None:
            raise RuntimeError("ros worker not running")
        if self._crashed.is_set():
            raise RuntimeError("ros worker has crashed")
        future: asyncio.Future = self._loop.create_future()
        self._jobs.put((fn, future))
        try:
            self._executor.wake()
        except Exception:  # noqa: BLE001
            pass
        return await asyncio.wait_for(future, timeout=timeout)

    # ------------------------------------------------------------------
    # Probe subscriptions (latency measurement)
    # ------------------------------------------------------------------

    async def add_probe(self, topic: str, msg_type: str) -> None:
        """Subscribe best-effort/volatile to *topic*; messages are dropped unread."""

        def _create() -> None:
            from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
            from rosidl_runtime_py.utilities import get_message

            qos = QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.BEST_EFFORT,
                durability=DurabilityPolicy.VOLATILE,
            )
            sub = self._node.create_subscription(get_message(msg_type), topic, _discard, qos, raw=True)
            self._probes.append(sub)

        await self.run_on_ros_thread(_create)

    async def remove_probes(self) -> int:
        def _destroy() -> int:
            count = 0
            for sub in self._probes:
                try:
                    self._node.destroy_subscription(sub)
                    count += 1
                except Exception:  # noqa: BLE001
                    pass
            self._probes.clear()
            return count

        return await self.run_on_ros_thread(_destroy)

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
        service_wait_warn_s: float = 30.0,
        load_call_warn_s: float = 30.0,
    ) -> int:
        if self._crashed.is_set():
            raise RuntimeError("ros worker has crashed")
        from composition_interfaces.srv import LoadNode

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
        target = f"{node_namespace.rstrip('/')}/{node_name}"

        # Both waits are unbounded, as in launch_ros: past the warn threshold they log once and keep
        # waiting; only shutdown (task cancellation) or a crashed ros worker ends them.
        loop = asyncio.get_running_loop()
        started = loop.time()
        warned = False
        while not await loop.run_in_executor(None, client.wait_for_service, WAIT_POLL_S):
            if self._crashed.is_set():
                raise RuntimeError("ros worker crashed while waiting for service")
            if not warned and loop.time() - started >= service_wait_warn_s:
                warned = True
                logger.warning(
                    "service %s/_container/load_node not available after %gs for %s; still waiting",
                    container_fqn,
                    service_wait_warn_s,
                    target,
                )

        rcl_future = client.call_async(req)
        aio_future: "asyncio.Future" = loop.create_future()

        def _on_done(f) -> None:
            try:
                result = f.result()
            except Exception as e:  # noqa: BLE001
                loop.call_soon_threadsafe(resolve, aio_future, None, e)
                return
            loop.call_soon_threadsafe(resolve, aio_future, result, None)

        rcl_future.add_done_callback(_on_done)

        started = loop.time()
        warned = False
        try:
            while True:
                done, _ = await asyncio.wait({aio_future}, timeout=WAIT_POLL_S)
                if done:
                    break
                if self._crashed.is_set():
                    raise RuntimeError("ros worker crashed while waiting for LoadNode response")
                if not warned and loop.time() - started >= load_call_warn_s:
                    warned = True
                    logger.warning(
                        "LoadNode call for %s into %s not answered after %gs; still waiting",
                        target,
                        container_fqn,
                        load_call_warn_s,
                    )
        except BaseException:
            rcl_future.cancel()
            raise
        response = aio_future.result()

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


def takes_message_info(callback: Callable) -> bool:
    """Whether rclpy (Iron and later) calls *callback* with the message and its message info."""
    import inspect

    signature = inspect.signature(callback)
    try:
        signature.bind(object())
        return False
    except TypeError:
        pass
    try:
        signature.bind(object(), object())
        return True
    except TypeError:
        return False


def make_executor(context=None):
    """Single-threaded executor that passes message info to two-argument subscription callbacks.

    rclpy does so from Iron on; on Humble the message info that rcl returns with each take reaches
    such callbacks through this executor.
    """
    from rclpy.executors import SingleThreadedExecutor, await_or_execute
    from rclpy.subscription import Subscription

    if hasattr(Subscription, "CallbackType"):
        return SingleThreadedExecutor(context=context)

    class _MessageInfoExecutor(SingleThreadedExecutor):
        def _take_subscription(self, sub):
            with sub.handle:
                return sub.handle.take_message(sub.msg_type, sub.raw)

        async def _execute_subscription(self, sub, taken):
            if taken is None:
                return
            msg, info = taken
            if takes_message_info(sub.callback):
                await await_or_execute(sub.callback, msg, info)
            else:
                await await_or_execute(sub.callback, msg)

    return _MessageInfoExecutor(context=context)


def _discard(_msg) -> None:
    return None
