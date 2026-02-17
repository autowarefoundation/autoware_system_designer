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

from typing import Any, Dict, List, Tuple

from ..parameters.parameter_manager import ParameterManager
from .instance_serializer import collect_launcher_data


def collect_component_nodes(component_instance) -> List[Dict[str, Any]]:
    """Collect launcher node payloads from a runtime component instance."""
    nodes: List[Dict[str, Any]] = []

    base_namespace = (getattr(component_instance, "namespace", None) or []).copy()

    def traverse(current_instance, full_namespace_path: List[str]):
        for child_name, child_instance in current_instance.children.items():
            if child_instance.entity_type == "node":
                nodes.append(_extract_node_data(child_instance, full_namespace_path))
            elif child_instance.entity_type == "module":
                traverse(child_instance, full_namespace_path + [child_name])

    if component_instance.entity_type == "module":
        traverse(component_instance, base_namespace)
    elif component_instance.entity_type == "node":
        nodes.append(_extract_node_data(component_instance, base_namespace))

    return nodes


def collect_component_nodes_from_data(component_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect launcher node payloads from serialized component data."""
    nodes: List[Dict[str, Any]] = []

    base_namespace = component_data.get("namespace", []) or []
    if isinstance(base_namespace, str):
        base_namespace = [base_namespace]
    elif not isinstance(base_namespace, list):
        base_namespace = []

    def traverse(current_data: Dict[str, Any], full_namespace_path: List[str]):
        for child in current_data.get("children", []):
            if child.get("entity_type") == "node":
                nodes.append(_extract_node_data_from_dict(child, full_namespace_path))
            elif child.get("entity_type") == "module":
                traverse(child, full_namespace_path + [child.get("name")])

    if component_data.get("entity_type") == "module":
        traverse(component_data, base_namespace)
    elif component_data.get("entity_type") == "node":
        nodes.append(_extract_node_data_from_dict(component_data, base_namespace))

    return nodes


def build_runtime_system_component_maps(
    system_instance, forward_args: List[str] | None
) -> Tuple[Dict[str, list], Dict[Tuple[str, str], List[str]], Dict[Tuple[str, str], list]]:
    """Build compute-unit and component maps from runtime system instance."""
    compute_unit_map: Dict[str, list] = {}
    component_required_args_map: Dict[Tuple[str, str], List[str]] = {}
    component_map: Dict[Tuple[str, str], list] = {}

    for component in system_instance.children.values():
        compute_unit_map.setdefault(component.compute_unit, []).append(component)
        component_key = (component.compute_unit, component.name)
        component_map[component_key] = [component]

        nodes = collect_component_nodes(component)
        component_required_args_map[component_key] = (
            ParameterManager.collect_component_required_system_args(nodes, forward_args)
        )

    return compute_unit_map, component_required_args_map, component_map


def build_serialized_system_component_maps(
    system_data: Dict[str, Any], forward_args: List[str] | None
) -> Tuple[Dict[str, list], Dict[Tuple[str, str], List[str]], Dict[Tuple[str, str], list]]:
    """Build compute-unit and component maps from serialized system data."""
    compute_unit_map: Dict[str, list] = {}
    component_required_args_map: Dict[Tuple[str, str], List[str]] = {}
    component_map: Dict[Tuple[str, str], list] = {}

    for component in system_data.get("children", []):
        compute_unit = component.get("compute_unit", "")
        component_name = component.get("name", "")
        component_key = (compute_unit, component_name)

        compute_unit_map.setdefault(compute_unit, []).append(component)
        component_map[component_key] = [component]

        nodes = collect_component_nodes_from_data(component)
        component_required_args_map[component_key] = (
            ParameterManager.collect_component_required_system_args(nodes, forward_args)
        )

    return compute_unit_map, component_required_args_map, component_map


def _extract_node_data(node_instance, module_path: List[str]) -> Dict[str, Any]:
    """Extract node launcher data from runtime instance."""
    node_data = collect_launcher_data(node_instance)
    node_data["name"] = node_instance.name
    node_data["full_namespace_path"] = "/".join(module_path) if module_path else ""
    return node_data


def _extract_node_data_from_dict(node_instance: Dict[str, Any], module_path: List[str]) -> Dict[str, Any]:
    """Extract node launcher data from serialized node dictionary."""
    node_data = {"name": node_instance.get("name", "")}

    launch_data = node_instance.get("launcher", {})
    node_data["package"] = launch_data.get("package", "")
    node_data["ros2_launch_file"] = launch_data.get("ros2_launch_file", None)
    node_data["is_ros2_file_launch"] = node_data["ros2_launch_file"] is not None
    node_data["node_output"] = launch_data.get("node_output", "screen")
    node_data["args"] = launch_data.get("args", "")

    if not node_data["is_ros2_file_launch"]:
        node_data["plugin"] = launch_data.get("plugin", "")
        node_data["executable"] = launch_data.get("executable", "")
        node_data["use_container"] = launch_data.get("use_container", False)
        node_data["container"] = launch_data.get("container", "perception_container")

    def normalize_parameter_type(param_type: Any) -> Dict[str, Any]:
        if isinstance(param_type, dict) and "name" in param_type:
            return param_type
        if isinstance(param_type, str):
            return {"name": param_type}
        return {"name": str(param_type)}

    node_data["ports"] = launch_data.get("ports", [])
    node_data["full_namespace_path"] = "/".join(module_path) if module_path else ""
    node_data["parameters"] = []
    for param in launch_data.get("parameters", []):
        param_copy = dict(param)
        param_copy["parameter_type"] = normalize_parameter_type(param.get("parameter_type"))
        node_data["parameters"].append(param_copy)

    node_data["parameter_files"] = []
    for param_file in launch_data.get("parameter_files", []):
        param_file_copy = dict(param_file)
        param_file_copy["parameter_type"] = normalize_parameter_type(
            param_file.get("parameter_type")
        )
        node_data["parameter_files"].append(param_file_copy)

    return node_data
