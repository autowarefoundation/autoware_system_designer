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

"""Actor-based runtime for supervising system_structure node processes.

Inspired by play_launch (https://github.com/tier4/play_launch) — each managed
member (regular node, container, composable node, launch-file include) is a
self-contained asyncio task with an explicit state machine, control queue,
and state-event stream. The coordinator multiplexes events and dispatches
control commands.

Public surface:

- :func:`populate_builder` translates a system_structure dict into a
  populated :class:`CoordinatorBuilder` and a paired :class:`RosWorker`.
- :class:`Coordinator` runs the actors and pumps state events.
- :class:`ActorConfig` carries per-actor settings (respawn, output dir).
"""

from ._impl.core.config import ActorConfig
from ._impl.core.coordinator import Coordinator, CoordinatorBuilder, MemberHandle, ensure_output_dir
from ._impl.ros2.builder import populate_builder
from ._impl.ros2.launchers.container import RosWorker

__all__ = [
    "populate_builder",
    "ActorConfig",
    "RosWorker",
    "Coordinator",
    "CoordinatorBuilder",
    "MemberHandle",
    "ensure_output_dir",
]
