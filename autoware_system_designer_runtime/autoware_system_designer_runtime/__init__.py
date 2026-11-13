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

import importlib

# Public name -> implementing module, imported on first use: the file-contract modules
# (``export_tree``, ``records``, ``rates_record``, ``run_log``) load without the actor runtime.
_EXPORTS = {
    "populate_builder": "._impl.ros2.builder",
    "ActorConfig": "._impl.core.config",
    "RosWorker": "._impl.ros2.launchers.container",
    "Coordinator": "._impl.core.coordinator",
    "CoordinatorBuilder": "._impl.core.coordinator",
    "MemberHandle": "._impl.core.coordinator",
    "ensure_output_dir": "._impl.core.coordinator",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value
