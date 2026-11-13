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

"""Safe-state interlock on the Autoware operation mode."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .launchers.container import RosWorker

logger = logging.getLogger(__name__)

TOPIC = "/api/operation_mode/state"
MSG_TYPE = "autoware_adapi_v1_msgs/msg/OperationModeState"
MODE_AUTONOMOUS = 2
GUARDED_ACTIONS = ("stop", "restart", "kill", "shutdown")
POLICIES = ("guard", "open")
STAMP_TOLERANCE_NS = 10 * 1_000_000_000


def is_replayed(stamp_ns: int, published_ns: Optional[int]) -> bool:
    """A report stamped far from its publication time is a replayed recording or comes from a
    system on simulated time; either way it does not describe the vehicle. An unknown publication
    time leaves the report trusted."""
    if not published_ns:
        return False
    return abs(published_ns - stamp_ns) > STAMP_TOLERANCE_NS


class OperationModeGuard:
    """Refuses member and system actions while the vehicle is in, or moving through, autonomous mode.

    The latest live state of the ADAPI operation mode topic decides; replayed reports are ignored
    and without any report, actions pass.
    """

    def __init__(self, worker: "RosWorker") -> None:
        self._worker = worker
        self._lock = threading.Lock()
        self._mode: Optional[int] = None
        self._in_transition = False
        self._subscription = None
        self._replay_logged = False

    async def install(self) -> bool:
        def _create() -> None:
            from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
            from rosidl_runtime_py.utilities import get_message

            qos = QoSProfile(
                depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL
            )
            self._subscription = self._worker.node.create_subscription(
                get_message(MSG_TYPE), TOPIC, self._on_message, qos
            )

        try:
            await self._worker.run_on_ros_thread(_create)
        except Exception as exc:  # noqa: BLE001
            logger.warning("operation mode guard unavailable (%s); member and system actions are not interlocked", exc)
            return False
        return True

    def _on_message(self, msg, info) -> None:
        # Both arguments are required: rclpy passes message info only to a callback that cannot be
        # called with the message alone.
        stamp_ns = int(msg.stamp.sec) * 1_000_000_000 + int(msg.stamp.nanosec)
        published_ns = (
            info.get("source_timestamp") if isinstance(info, dict) else getattr(info, "source_timestamp", None)
        )
        if is_replayed(stamp_ns, published_ns):
            if not self._replay_logged:
                self._replay_logged = True
                logger.info(
                    "operation mode report stamped %.0f s from its publication ignored " "(replayed or simulated time)",
                    (published_ns - stamp_ns) / 1e9,
                )
            return
        self.observe(int(msg.mode), bool(msg.is_in_transition))

    def observe(self, mode: int, in_transition: bool = False) -> None:
        with self._lock:
            self._mode = mode
            self._in_transition = in_transition

    def check(self, action: str) -> Optional[str]:
        if action not in GUARDED_ACTIONS:
            return None
        with self._lock:
            mode, in_transition = self._mode, self._in_transition
        if mode == MODE_AUTONOMOUS:
            return f"{action} refused: operation mode is autonomous"
        if in_transition:
            return f"{action} refused: operation mode is in transition"
        return None
