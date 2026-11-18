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

"""The driver's own ROS endpoints, on the runtime's rclpy node: one publisher per fed bag
topic and one subscription per recorded output. Messages stay serialized on both paths.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable, Mapping, Optional

from .bag import BagInfo
from .link import DURABILITY_TRANSIENT_LOCAL, PUBLISHER, RELIABILITY_BEST_EFFORT, SUBSCRIPTION, Endpoint
from .recorder import OutputRecorder

if TYPE_CHECKING:
    from ..ros2.launchers.container import RosWorker
    from .driver import ReplayDriver

logger = logging.getLogger(__name__)

# Messages a transient-local publisher of the driver keeps for a late subscription.
LATCH_DEPTH_LIMIT = 100


def unknown_types(types: Mapping[str, str]) -> dict[str, str]:
    """Topics whose message type the workspace cannot load, with the reason."""
    from rosidl_runtime_py.utilities import get_message

    failed: dict[str, str] = {}
    for topic, msg_type in types.items():
        try:
            get_message(msg_type)
        except Exception as exc:  # noqa: BLE001
            failed[topic] = f"{msg_type}: {type(exc).__name__}: {exc}"
    return failed


class RosReplayIO:
    def __init__(
        self,
        worker: "RosWorker",
        fed_topics: Mapping[str, str],
        bag: BagInfo,
        recorder: OutputRecorder,
        *,
        on_scope_up: Optional[Callable[[list[Endpoint]], None]] = None,
    ) -> None:
        self._worker = worker
        self.fed_topics = dict(fed_topics)
        self._bag = bag
        self._recorder = recorder
        self._on_scope_up = on_scope_up
        self._publishers: dict[str, object] = {}
        self._subscriptions: list = []

    async def prepare(self, driver: "ReplayDriver") -> None:
        """Creates the endpoints once the scope is up, with the quality of service its endpoints ask for."""
        endpoints = driver.endpoints()
        if self._on_scope_up is not None:
            self._on_scope_up(endpoints)
        loop = asyncio.get_running_loop()
        latched = {
            ep.topic for ep in endpoints if ep.kind == SUBSCRIPTION and ep.durability == DURABILITY_TRANSIENT_LOCAL
        }
        best_effort = {
            ep.topic for ep in endpoints if ep.kind == PUBLISHER and ep.reliability == RELIABILITY_BEST_EFFORT
        }

        def heard(topic: str, data: bytes) -> None:
            self._recorder.heard(topic, data)
            driver.wake()

        def create() -> None:
            from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
            from rosidl_runtime_py.utilities import get_message

            node = self._worker.node
            for topic, msg_type in sorted(self.fed_topics.items()):
                recorded = self._bag.topics.get(topic)
                latch = topic in latched or (recorded is not None and recorded.transient_local)
                depth = min(max(recorded.count if recorded else 1, 1), LATCH_DEPTH_LIMIT) if latch else 10
                qos = QoSProfile(
                    history=HistoryPolicy.KEEP_LAST,
                    depth=depth,
                    reliability=ReliabilityPolicy.RELIABLE,
                    durability=DurabilityPolicy.TRANSIENT_LOCAL if latch else DurabilityPolicy.VOLATILE,
                )
                self._publishers[topic] = node.create_publisher(get_message(msg_type), topic, qos)
            for topic, msg_type in sorted(self._recorder.topics.items()):
                qos = QoSProfile(
                    history=HistoryPolicy.KEEP_ALL,
                    reliability=(ReliabilityPolicy.BEST_EFFORT if topic in best_effort else ReliabilityPolicy.RELIABLE),
                    durability=DurabilityPolicy.VOLATILE,
                )
                self._subscriptions.append(
                    node.create_subscription(
                        get_message(msg_type),
                        topic,
                        lambda data, topic=topic: loop.call_soon_threadsafe(heard, topic, bytes(data)),
                        qos,
                        raw=True,
                    )
                )

        await self._worker.run_on_ros_thread(create, timeout=30.0)
        logger.info(
            "replay: %d bag topic(s) fed, %d output topic(s) recorded", len(self._publishers), len(self._subscriptions)
        )

    # A publish and a matched count take the publisher's own lock; neither waits for the ros thread.
    async def publish(self, topic: str, data: bytes) -> None:
        self._publishers[topic].publish(data)

    async def subscription_count(self, topic: str) -> int:
        publisher = self._publishers.get(topic)
        return int(publisher.get_subscription_count()) if publisher is not None else 0
