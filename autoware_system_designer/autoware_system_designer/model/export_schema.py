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

"""On-disk system structure payload contract.

The record schema is declared once, on the model dataclasses (ports, events,
links, parameters); ``model.serde`` derives the JSON from those declarations.
The aliases below name the payload shapes for readers of the exported JSON.
"""

from __future__ import annotations

from typing import Any, Dict

# Version for the on-disk system structure JSON payload.
SCHEMA_VERSION = "1.0"

# Serialized record shapes (JSON objects), keyed as written by model.serde.
EventData = Dict[str, Any]
PortData = Dict[str, Any]
ParameterData = Dict[str, Any]
ParameterFileData = Dict[str, Any]
LinkData = Dict[str, Any]

# Launcher payload assembled by the launch manager for one node.
LauncherData = Dict[str, Any]
LauncherPortData = Dict[str, Any]
LauncherParamValueData = Dict[str, Any]
LauncherParamFileData = Dict[str, Any]

# One instance-tree node: identity, ports, links, events, parameters, children.
InstanceData = Dict[str, Any]

# schema_version / metadata / data envelope of one exported mode.
SystemStructureMetadata = Dict[str, Any]
SystemStructurePayload = Dict[str, Any]

DeploymentDataByMode = Dict[str, InstanceData]

__all__ = [
    "SCHEMA_VERSION",
    "EventData",
    "PortData",
    "ParameterData",
    "ParameterFileData",
    "LinkData",
    "LauncherData",
    "LauncherPortData",
    "LauncherParamValueData",
    "LauncherParamFileData",
    "InstanceData",
    "SystemStructureMetadata",
    "SystemStructurePayload",
    "DeploymentDataByMode",
]
