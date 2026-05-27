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

"""ComposableSpec builders for composable_node launch type."""

from __future__ import annotations

from typing import Mapping, Optional

from ..common.namespace import parent_namespace, unique_node_name
from ..common.params import parameter_files, params_dict, remap_pairs
from .actor import ComposableSpec

_GLOG_PKG = "autoware_glog_component"
_GLOG_PLUGIN = "autoware::glog_component::GlogComponent"
_GLOG_NAME = "glog_component"


def composable_spec(spec: Mapping, extra_param_files: Optional[list[str]] = None) -> ComposableSpec:
    launcher = spec["launcher"]
    inline = params_dict(spec.get("parameters", []))
    extra = {"use_intra_process_comms": True} if launcher.get("use_intra_process_comms") else {}
    ns = parent_namespace(spec.get("namespace"), spec.get("name"))
    return ComposableSpec(
        name=unique_node_name(spec),
        package=launcher["package"],
        plugin=launcher["plugin"],
        node_name=spec["name"],
        namespace=ns,
        target_container_fqn=launcher.get("container_target", ""),
        remap_rules=remap_pairs(launcher.get("ports", [])),
        parameter_files=parameter_files(spec) + (extra_param_files or []),
        inline_parameters=inline,
        extra_arguments=extra,
    )


def glog_spec_for(container_target_fqn: str) -> ComposableSpec:
    ns_parts = container_target_fqn.rsplit("/", 1)
    container_ns = ns_parts[0] or "/"
    return ComposableSpec(
        name=f"{container_target_fqn}/{_GLOG_NAME}",
        package=_GLOG_PKG,
        plugin=_GLOG_PLUGIN,
        node_name=_GLOG_NAME,
        namespace=container_ns,
        target_container_fqn=container_target_fqn,
    )
