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

"""Global parameters of a system: values every node receives at the lowest priority.

A designed node holds them as GLOBAL parameters. A launch-file node holds them apart from its
launch arguments; its launcher sets them on the nodes the launch file starts.
"""

import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Iterator, List

from autoware_system_designer.common.exceptions import ParameterConfigurationError, error_context
from autoware_system_designer.common.parameter_types import coerce_numeric_value, normalize_type_name
from autoware_system_designer.common.source_location import format_source, source_from_config
from autoware_system_designer.model.execution import LaunchState
from autoware_system_designer.model.parameters import (
    ParameterType,
    canonical_parameter_type,
    normalize_parameter_value,
)
from autoware_system_designer.parser.yaml_parser import yaml_parser

if TYPE_CHECKING:
    from autoware_system_designer.builder.config.config_registry import ConfigRegistry
    from autoware_system_designer.builder.instances.instances import Instance

logger = logging.getLogger(__name__)


def apply_global_parameters(instance: "Instance", config_registry: "ConfigRegistry") -> None:
    """Give every node under a system instance the system's global parameters."""
    declared = getattr(instance.configuration, "global_parameters", None)
    if not declared:
        return

    with error_context("applying global parameters"):
        parameters = _resolve_global_parameters(instance, declared, config_registry)
    if not parameters:
        return

    nodes = list(_iter_nodes(instance))
    logger.info(f"Applying {len(parameters)} global parameter(s) to {len(nodes)} node(s)")
    for node in nodes:
        if node.launch_manager.launch_config.launch_state == LaunchState.ROS2_LAUNCH_FILE:
            node.launch_manager.global_parameters = [
                {"name": p["name"], "value": normalize_parameter_value(p["value"]), "type": p["type"]}
                for p in parameters
            ]
            continue
        for p in parameters:
            node.parameter_manager.parameters.set_parameter(
                p["name"],
                p["value"],
                data_type=p["type"],
                parameter_type=ParameterType.GLOBAL,
                source=p["source"],
            )


def _iter_nodes(instance: "Instance") -> Iterator["Instance"]:
    if instance.entity_type == "node" and getattr(instance, "launch_manager", None):
        yield instance
    for child in instance.children.values():
        yield from _iter_nodes(child)


def _resolve_global_parameters(
    instance: "Instance", declared: Dict[str, Any], config_registry: "ConfigRegistry"
) -> List[Dict[str, Any]]:
    """Declared global parameters by name: the values of the files in order, then the direct values."""
    resolver = instance.parameter_resolver
    manager = instance.parameter_manager
    merged: Dict[str, Dict[str, Any]] = {}

    for idx, path in enumerate(declared.get("param_files") or []):
        source = source_from_config(instance.configuration, f"/global_parameters/param_files/{idx}")
        resolved = resolver.resolve_parameter_file_path(path, source=source) if resolver else path
        if not os.path.isabs(resolved) or "$(" in resolved:
            raise ParameterConfigurationError(
                f"Global parameter file is not resolved at build time: {resolved}{format_source(source)}"
            )
        if not os.path.exists(resolved):
            counterpart = config_registry.source_counterpart(resolved) if config_registry else None
            if not counterpart or not os.path.exists(counterpart):
                raise ParameterConfigurationError(f"Global parameter file not found: {resolved}{format_source(source)}")
            resolved = counterpart
        data = yaml_parser.load_config(resolved) or {}
        section = data.get("/**") if isinstance(data, dict) else None
        ros_parameters = section.get("ros__parameters") if isinstance(section, dict) else None
        if not isinstance(ros_parameters, dict):
            raise ParameterConfigurationError(
                f"Global parameter file has no '/**' ros__parameters: {resolved}{format_source(source)}"
            )
        for name, value in manager._flatten_parameters(ros_parameters).items():
            if resolver:
                value = resolver.resolve_parameter_value(value, source=source)
            merged[name] = {
                "name": name,
                "value": value,
                "type": canonical_parameter_type(None, value),
                "source": source,
            }

    for idx, entry in enumerate(declared.get("param_values") or []):
        source = source_from_config(instance.configuration, f"/global_parameters/param_values/{idx}")
        if not isinstance(entry, dict) or not entry.get("name") or entry.get("value") is None:
            raise ParameterConfigurationError(f"Global parameter needs 'name' and 'value'{format_source(source)}")
        value = resolver.resolve_parameter_value(entry["value"], source=source) if resolver else entry["value"]
        type_name = normalize_type_name(entry.get("type"))
        if type_name:
            try:
                value = coerce_numeric_value(value, type_name)
            except ValueError as exc:
                raise ParameterConfigurationError(f"{exc}{format_source(source)}") from exc
        merged[entry["name"]] = {
            "name": entry["name"],
            "value": value,
            "type": canonical_parameter_type(entry.get("type"), value),
            "source": source,
        }

    return list(merged.values())
