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

"""Direct ROS 2 launch from system_structure JSON without intermediate file generation."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_GLOG_PKG = "autoware_glog_component"
_GLOG_PLUGIN = "autoware::glog_component::GlogComponent"
_GLOG_NAME = "glog_component"


def _collect_all_nodes(
    entity: dict[str, Any],
    results: list | None = None,
    ecu: str | None = None,
) -> list[dict[str, Any]]:
    """Recursively collect all node entities that have a launcher from the system tree.

    When *ecu* is given, only nodes whose ``compute_unit`` matches that value are collected.
    """
    if results is None:
        results = []
    if entity.get("entity_type") == "node" and entity.get("launcher"):
        if ecu is None or entity.get("compute_unit") == ecu:
            results.append(
                {
                    "name": entity.get("name", ""),
                    "namespace": entity.get("namespace", "/"),
                    "path": entity.get("path", ""),
                    "launcher": entity["launcher"],
                    "parameters": entity.get("parameters", []),
                }
            )
    for child in entity.get("children", []):
        _collect_all_nodes(child, results, ecu=ecu)
    return results


_BOOL_TYPES = {"bool", "boolean"}
_INT_TYPES = {"int", "integer", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64", "short", "long"}
_FLOAT_TYPES = {"float", "double", "float32", "float64"}


def _resolve_value(value: Any, type_hint: str | None = None) -> Any:
    """Convert a JSON param value to a Python launch-ready value.

    Uses *type_hint* (the declared ROS 2 parameter type) when available so that
    string values like "true" are not misinterpreted as booleans, and numeric
    strings are coerced to the correct Python type.

    Falls back to yaml.safe_load inference when no type hint is given, matching
    the behaviour of the ROS 2 XML launch parser.
    """
    import yaml

    # Native JSON types that need no further parsing.
    # bool must be checked before int because bool is a subclass of int.
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, list)):
        return value

    s = str(value)

    # XML launch syntax: $(command 'cmd string' 'output_level')
    m = re.match(r"^\$\(command '([^']+)'(?:\s+'[^']*')?\)$", s)
    if m:
        from launch.substitutions import Command

        return Command(m.group(1))

    hint = (type_hint or "").strip().lower()

    # Explicit bool coercion.
    if hint in _BOOL_TYPES:
        return s.lower() not in ("false", "0", "no", "off", "")

    # Explicit int coercion.
    if hint in _INT_TYPES:
        try:
            return int(s)
        except ValueError:
            pass

    # Explicit float coercion.
    if hint in _FLOAT_TYPES:
        try:
            return float(s)
        except ValueError:
            pass

    # For string hints and no-hint cases, attempt YAML inference so that
    # numeric strings like "-1.1" or "true" coming from resolved $(var ...)
    # substitutions are converted to their proper Python types — mirroring
    # what the ROS 2 XML launch parser does for every <param value="...">.
    # Only suppress inference when the hint is explicitly "string" AND the
    # value is not parseable as a primitive (i.e. it really is a plain string).
    try:
        parsed = yaml.safe_load(s)
        if isinstance(parsed, (bool, int, float, list)):
            return parsed
    except Exception:
        pass
    return s


def _params_dict(params: list[dict]) -> dict[str, Any]:
    """Build a parameter dict from a list of parameter entries (each entry has name/value/type)."""
    return {p["name"]: _resolve_value(p["value"], p.get("type")) for p in params}


def _param_list(node_spec: dict) -> list:
    """Build the parameters list for a Node or ComposableNode.

    Uses the top-level ``parameters`` array (priority-resolved, type-correct) for
    inline values, and ``parameter_files_all`` (excluding DEFAULT_FILE entries whose
    values are already expanded into ``parameters``) for runtime file references.
    """
    inline = _params_dict(node_spec.get("parameters", []))
    files = [
        f["path"]
        for f in node_spec.get("parameter_files_all", [])
        if f.get("parameter_type") != "DEFAULT_FILE"
    ]
    return ([inline] if inline else []) + files


def _build_remaps(ports: list[dict]) -> list[tuple[str, str]]:
    """Build remap tuples (from, to) from launcher ports."""
    return [
        (p["remap_target"], p["topic"])
        for p in ports
        if p.get("remap_target") and p.get("topic")
    ]


def _build_include(node_spec: dict):
    from ament_index_python.packages import get_package_share_directory
    from launch.actions import IncludeLaunchDescription
    from launch.launch_description_sources import AnyLaunchDescriptionSource

    launcher = node_spec["launcher"]
    pkg_share = get_package_share_directory(launcher["package"])
    launch_file = str(Path(pkg_share) / "launch" / launcher["ros2_launch_file"])
    str_args = {k: str(v) for k, v in _params_dict(node_spec.get("parameters", [])).items()}
    return IncludeLaunchDescription(
        AnyLaunchDescriptionSource(launch_file),
        launch_arguments=str_args.items(),
    )


def _build_node(node_spec: dict):
    from launch_ros.actions import Node

    launcher = node_spec["launcher"]
    remaps = _build_remaps(launcher.get("ports", []))
    args_str = launcher.get("args", "")
    return Node(
        package=launcher["package"],
        executable=launcher["executable"],
        name=node_spec["name"],
        namespace=node_spec["namespace"],
        output=launcher.get("node_output", "screen"),
        parameters=_param_list(node_spec),
        remappings=remaps,
        arguments=args_str.split() if args_str else [],
    )


def _build_container(node_spec: dict):
    """Create a ComposableNodeContainer with autoware_glog_component pre-loaded."""
    from launch_ros.actions import ComposableNodeContainer
    from launch_ros.descriptions import ComposableNode

    launcher = node_spec["launcher"]
    ns = node_spec["namespace"]
    glog = ComposableNode(
        package=_GLOG_PKG,
        plugin=_GLOG_PLUGIN,
        name=_GLOG_NAME,
        namespace=ns,
    )
    return ComposableNodeContainer(
        name=node_spec["name"],
        namespace=ns,
        package=launcher["package"],
        executable=launcher["executable"],
        output=launcher.get("node_output", "both"),
        composable_node_descriptions=[glog],
    )


def _build_composable_node(node_spec: dict):
    from launch_ros.descriptions import ComposableNode

    launcher = node_spec["launcher"]
    remaps = _build_remaps(launcher.get("ports", []))
    extra_args = [{"use_intra_process_comms": True}] if launcher.get("use_intra_process_comms") else []
    return ComposableNode(
        package=launcher["package"],
        plugin=launcher["plugin"],
        name=node_spec["name"],
        namespace=node_spec["namespace"],
        parameters=_param_list(node_spec),
        remappings=remaps,
        extra_arguments=extra_args,
    )


def build_launch_description(system_data: dict, ecu: str | None = None):
    """Build a LaunchDescription directly from parsed system_structure JSON data.

    Mirrors the behavior of the XML launcher templates:
    - ros2_launch_file  → IncludeLaunchDescription with all param_values as args
    - single_node       → Node with typed params and topic remaps
    - node_container    → ComposableNodeContainer with glog_component pre-loaded,
                          then LoadComposableNodes for all targeting composable nodes
    - composable_node   → ComposableNode loaded via LoadComposableNodes into container_target
    """
    from launch import LaunchDescription
    from launch_ros.actions import LoadComposableNodes

    all_nodes = _collect_all_nodes(system_data, ecu=ecu)

    # Group composable nodes by container_target path
    composables_by_target: dict[str, list] = defaultdict(list)
    for n in all_nodes:
        if n["launcher"]["launch_state"] == "composable_node":
            target = n["launcher"].get("container_target", "")
            if target:
                composables_by_target[target].append(_build_composable_node(n))

    actions = []
    for n in all_nodes:
        state = n["launcher"]["launch_state"]
        name = n.get("name", "")

        if state == "ros2_launch_file":
            actions.append(_build_include(n))

        elif state == "single_node":
            if not name or not n["launcher"].get("executable"):
                continue
            actions.append(_build_node(n))

        elif state == "node_container":
            if not name:
                continue
            actions.append(_build_container(n))
            composables = composables_by_target.get(n["path"], [])
            if composables:
                actions.append(
                    LoadComposableNodes(
                        target_container=n["path"],
                        composable_node_descriptions=composables,
                    )
                )

        # composable_node: consumed above via LoadComposableNodes, no top-level action needed

    return LaunchDescription(actions)


def launch_from_json(json_path: str, ecu: str | None = None) -> int:
    """Parse a system_structure JSON file and launch the system via ROS 2 Python launch API."""
    from launch import LaunchService

    with open(json_path) as f:
        data = json.load(f)
    launch_description = build_launch_description(data["data"], ecu=ecu)
    ls = LaunchService()
    ls.include_launch_description(launch_description)
    return ls.run()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Launch an Autoware system directly from a system_structure JSON file."
    )
    parser.add_argument(
        "json_file",
        help="Path to system_structure JSON (e.g. .../system_structure/LoggingSimulation.json)",
    )
    parser.add_argument(
        "--ecu",
        default=None,
        help="Only launch nodes whose compute_unit matches this value (e.g. main_ecu, dummy_ecu)."
        " When omitted, all nodes are launched.",
    )
    args = parser.parse_args()
    sys.exit(launch_from_json(args.json_file, ecu=args.ecu))
