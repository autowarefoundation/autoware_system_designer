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

"""Command-line builder for ros2_launch_file launch type."""

from __future__ import annotations

import json
import logging
import sys
from typing import Mapping, Optional

from ..common.params import params_dict

logger = logging.getLogger(__name__)


def include_cmdline(spec: Mapping, global_files: Optional[list[str]] = None) -> list[str]:
    """Command to run a ros2_launch_file via launch_runner with global param injection."""
    launcher = spec["launcher"]
    cmd = [
        sys.executable,
        "-m",
        "autoware_system_designer.runtime._impl.ros2.launch_file.runner",
        "--pkg",
        launcher["package"],
        "--file",
        launcher["ros2_launch_file"],
    ]
    for k, v in params_dict(spec.get("parameters", [])).items():
        if v is None or v == "":
            logger.debug("skipping empty launch arg %r for %s", k, launcher["ros2_launch_file"])
            continue
        v_str = json.dumps(v) if isinstance(v, list) else str(v)
        cmd += ["--launch-arg", f"{k}:={v_str}"]
    for f in global_files or []:
        cmd += ["--global-params-file", f]
    return cmd
