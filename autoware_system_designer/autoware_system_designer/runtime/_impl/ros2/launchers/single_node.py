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

"""Command-line builder for single_node launch type."""

from __future__ import annotations

from typing import Mapping, Optional

from ..common.params import _ros_args, build_cmd, parameter_files, params_dict, remap_pairs


def node_cmdline(spec: Mapping, extra_param_files: Optional[list[str]] = None) -> list[str]:
    launcher = spec["launcher"]
    inline = params_dict(spec.get("parameters", []))
    extra_args = launcher.get("args", "")
    cmd = build_cmd(launcher)
    if extra_args:
        cmd += extra_args.split()
    cmd += _ros_args(
        name=spec["name"],
        namespace=spec["namespace"],
        inline_params=inline,
        param_files=parameter_files(spec) + (extra_param_files or []),
        remaps=remap_pairs(launcher.get("ports", [])),
    )
    return cmd
