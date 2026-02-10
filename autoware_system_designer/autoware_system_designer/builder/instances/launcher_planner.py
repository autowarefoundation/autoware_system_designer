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

from typing import Any, Dict, List, Tuple

from ..parameters.parameter_manager import ParameterManager


def collect_component_nodes(component_instance) -> List[Dict[str, Any]]:
    """Collect launcher node payloads from a runtime component instance."""
    nodes: List[Dict[str, Any]] = []

    def traverse(current_instance, module_path: List[str]):
        for child_name, child_instance in current_instance.children.items():
            if child_instance.entity_type == "node":
                nodes.append(_extract_node_data(child_instance, module_path))
            elif child_instance.entity_type == "module":
                traverse(child_instance, module_path + [child_name])

    if component_instance.entity_type == "module":
        traverse(component_instance, [])
    elif component_instance.entity_type == "node":
        nodes.append(_extract_node_data(component_instance, []))

    return nodes


def collect_component_nodes_from_data(component_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect launcher node payloads from serialized component data."""
    nodes: List[Dict[str, Any]] = []

    def traverse(current_data: Dict[str, Any], module_path: List[str]):
        for child in current_data.get("children", []):
            if child.get("entity_type") == "node":
                nodes.append(_extract_node_data_from_dict(child, module_path))
            elif child.get("entity_type") == "module":
                traverse(child, module_path + [child.get("name")])

    if component_data.get("entity_type") == "module":
        traverse(component_data, [])
    elif component_data.get("entity_type") == "node":
        nodes.append(_extract_node_data_from_dict(component_data, []))

    return nodes


def attach_component_namespace(
    nodes: List[Dict[str, Any]], component_full_namespace: List[str]
) -> None:
    """Attach absolute namespace for each node under a component."""
    for node in nodes:
        full_ns_list = component_full_namespace + node["namespace_groups"]
        node["full_namespace"] = "/".join(full_ns_list)


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
    node_data: Dict[str, Any] = {
        "name": node_instance.name,
        "namespace_groups": module_path.copy(),
        "full_namespace_path": "/".join(module_path) if module_path else "",
    }

    launch_config = node_instance.configuration.launch
    node_data["package"] = node_instance.configuration.package_name
    node_data["ros2_launch_file"] = launch_config.get("ros2_launch_file", None)
    node_data["is_ros2_file_launch"] = node_data["ros2_launch_file"] is not None
    node_data["node_output"] = launch_config.get("node_output", "screen")
    node_data["args"] = node_instance.parameter_manager.resolve_substitutions(
        launch_config.get("args", "")
    )

    if not node_data["is_ros2_file_launch"]:
        node_data["plugin"] = launch_config.get("plugin", "")
        node_data["executable"] = launch_config.get("executable", "")
        node_data["use_container"] = launch_config.get("use_container", False)
        node_data["container"] = launch_config.get("container_name", "perception_container")

    ports = []
    inputs_cfg = node_instance.configuration.inputs or []
    outputs_cfg = node_instance.configuration.outputs or []
    remap_inputs_explicit = {
        cfg.get("name")
        for cfg in inputs_cfg
        if "remap_target" in cfg and cfg.get("remap_target") not in (None, "")
    }
    remap_outputs_explicit = {
        cfg.get("name")
        for cfg in outputs_cfg
        if "remap_target" in cfg and cfg.get("remap_target") not in (None, "")
    }

    for port in node_instance.link_manager.get_all_in_ports():
        if port.is_global and port.name not in remap_inputs_explicit:
            continue
        topic = port.get_topic()
        if topic == "":
            continue
        ports.append(
            {
                "direction": "input",
                "name": port.name,
                "topic": topic,
                "remap_target": port.remap_target,
            }
        )
    for port in node_instance.link_manager.get_all_out_ports():
        if port.is_global and port.name not in remap_outputs_explicit:
            continue
        topic = port.get_topic()
        if topic == "":
            continue
        ports.append(
            {
                "direction": "output",
                "name": port.name,
                "topic": topic,
                "remap_target": port.remap_target,
            }
        )
    node_data["ports"] = ports

    node_data["parameters"] = node_instance.parameter_manager.get_parameters_for_launch()
    node_data["parameter_files"] = node_instance.parameter_manager.get_parameter_files_for_launch()
    return node_data


def _extract_node_data_from_dict(node_instance: Dict[str, Any], module_path: List[str]) -> Dict[str, Any]:
    """Extract node launcher data from serialized node dictionary."""
    node_data = {
        "name": node_instance.get("name", ""),
        "namespace_groups": module_path.copy(),
        "full_namespace_path": "/".join(module_path) if module_path else "",
    }

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
