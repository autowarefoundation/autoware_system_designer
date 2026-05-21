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

"""Actor state machines.

Mirrors play_launch's ``src/play_launch/src/member_actor/state.rs``:

- :class:`NodeState` for regular nodes and containers.
- :class:`ComposableState` for composable nodes loaded into a container.
- :class:`ContainerStatus` for the supervisor's view of a container.
- :class:`BlockReason` annotates why a composable is Blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class BlockReason(str, Enum):
    NOT_STARTED = "not_started"
    STOPPED = "stopped"
    FAILED = "failed"
    SHUTDOWN = "shutdown"


@dataclass
class NodePending:
    pass


@dataclass
class NodeRunning:
    pid: int


@dataclass
class NodeRespawning:
    exit_code: Optional[int]
    attempt: int


@dataclass
class NodeStopped:
    exit_code: Optional[int]


@dataclass
class NodeFailed:
    error: str


NodeState = "NodePending | NodeRunning | NodeRespawning | NodeStopped | NodeFailed"


def is_terminal_node(state) -> bool:
    return isinstance(state, (NodeStopped, NodeFailed))


@dataclass
class ComposableBlocked:
    reason: BlockReason


@dataclass
class ComposableUnloaded:
    pass


@dataclass
class ComposableLoading:
    started_at: float  # monotonic seconds


@dataclass
class ComposableLoaded:
    unique_id: int


@dataclass
class ComposableFailed:
    error: str


ComposableState = (
    "ComposableBlocked | ComposableUnloaded | ComposableLoading | "
    "ComposableLoaded | ComposableFailed"
)


def is_terminal_composable(state) -> bool:
    return isinstance(state, ComposableBlocked) and state.reason == BlockReason.SHUTDOWN


class ContainerStatus(str, Enum):
    """Coarse status used by composable actors to gate load requests."""

    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
