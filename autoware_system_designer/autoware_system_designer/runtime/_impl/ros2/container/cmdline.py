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

"""Command-line builder for node_container launch type."""

from __future__ import annotations

from typing import Mapping

from ..common.executable import build_cmd
from ..common.params import _ros_args


def container_cmdline(spec: Mapping) -> list[str]:
    launcher = spec["launcher"]
    cmd = build_cmd(launcher)
    cmd += _ros_args(
        name=spec["name"],
        namespace=spec["namespace"],
        inline_params={},
        param_files=[],
        remaps=[],
    )
    return cmd
