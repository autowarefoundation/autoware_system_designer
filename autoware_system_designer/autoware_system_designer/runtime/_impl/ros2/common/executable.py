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

"""ROS 2 executable resolution shared by single_node and container launch types."""

from __future__ import annotations

import logging
from typing import Mapping, Optional

logger = logging.getLogger(__name__)


def _ros2_executable_path(package: str, executable: str) -> Optional[str]:
    """Return the direct binary path for a ROS 2 executable, or None to fall back to ros2 run.

    Direct spawn delivers SIGTERM to rclcpp, not to an intermediate Python wrapper.
    """
    try:
        from ros2run.api import get_executable_path

        path = get_executable_path(package_name=package, executable_name=executable)
        if path:
            return path
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_executable_path(%r, %r) failed: %s", package, executable, exc)
    return None


def build_cmd(launcher: Mapping[str, str]) -> list[str]:
    """Return [binary_path] or ['ros2', 'run', pkg, exe] for a launcher spec."""
    exec_path = _ros2_executable_path(launcher["package"], launcher["executable"])
    if exec_path:
        return [exec_path]
    logger.warning(
        "could not resolve executable path for %s/%s, falling back to ros2 run",
        launcher["package"],
        launcher["executable"],
    )
    return ["ros2", "run", launcher["package"], launcher["executable"]]
