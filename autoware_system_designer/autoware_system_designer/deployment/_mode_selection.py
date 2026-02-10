# Copyright 2025 TIER IV, inc.
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

from __future__ import annotations

from typing import List, Tuple

from ..models.config import SystemConfig


def _select_modes(system_config: SystemConfig) -> Tuple[List[str], str]:
    """Return (mode_names, default_mode) for a SystemConfig."""

    modes_config = system_config.modes or []

    if modes_config:
        mode_names = [m.get("name") for m in modes_config]
        default_mode = next(
            (m.get("name") for m in modes_config if m.get("default")),
            mode_names[0],
        )
        return mode_names, default_mode

    return ["default"], "default"
