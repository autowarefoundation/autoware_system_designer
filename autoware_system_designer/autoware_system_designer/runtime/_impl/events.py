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

"""Control and state event types exchanged between coordinator and actors.

Mirrors ``src/play_launch/src/member_actor/events.rs``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .state import BlockReason

# ---- Control events (coordinator → actor) -------------------------------


@dataclass
class Stop:
    pass


@dataclass
class Restart:
    pass


@dataclass
class KillSignal:
    signum: int


@dataclass
class ToggleRespawn:
    enabled: bool


# ---- State events (actor → coordinator) ---------------------------------


@dataclass
class Started:
    name: str
    pid: int


@dataclass
class Exited:
    name: str
    exit_code: Optional[int]


@dataclass
class Respawning:
    name: str
    attempt: int
    delay: float


@dataclass
class Terminated:
    name: str


@dataclass
class Failed:
    name: str
    error: str


@dataclass
class LoadStarted:
    name: str


@dataclass
class LoadSucceeded:
    name: str
    full_node_name: str
    unique_id: int


@dataclass
class LoadFailed:
    name: str
    error: str


@dataclass
class Blocked:
    name: str
    reason: BlockReason
