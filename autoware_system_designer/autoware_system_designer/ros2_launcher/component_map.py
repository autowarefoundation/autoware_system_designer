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

from ..builder.parameters.parameter_manager import ParameterManager


def _extract_node_data(node_instance: Dict[str, Any], namespace: str) -> Dict[str, Any]:
    """Extract node launcher data from serialized node dictionary (launcher is canonical from LaunchManager)."""
    launch_data = node_instance.get("launcher", {})
    name = node_instance.get("name")
    node_path = node_instance.get("path")
    return {
        **launch_data,
        "name": name,
        "namespace": namespace,
        "node_path": node_path,
    }


def collect_component_nodes(component_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect launcher node payloads from serialized component data."""
    nodes: List[Dict[str, Any]] = []

    def traverse(current_data: Dict[str, Any]):
        for child in current_data.get("children", []):
            if child.get("entity_type") == "node":
                nodes.append(_extract_node_data(child, child.get("namespace")))
            elif child.get("entity_type") == "module":
                traverse(child)

    if component_data.get("entity_type") == "module":
        traverse(component_data)
    elif component_data.get("entity_type") == "node":
        nodes.append(_extract_node_data(component_data, component_data.get("namespace")))

    return nodes


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

        nodes = collect_component_nodes(component)
        component_required_args_map[component_key] = ParameterManager.collect_component_required_system_args(
            nodes, forward_args
        )

    return compute_unit_map, component_required_args_map, component_map
