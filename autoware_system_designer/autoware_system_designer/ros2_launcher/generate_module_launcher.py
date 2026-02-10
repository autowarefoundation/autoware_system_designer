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

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from ..builder.instances.instances import Instance
from ..builder.parameters.parameter_manager import ParameterManager
from ..file_io.source_location import SourceLocation, format_source
from ..file_io.system_structure_json import extract_system_structure_data
from ..file_io.template_renderer import TemplateRenderer

logger = logging.getLogger(__name__)


def _ensure_directory(directory_path: str) -> None:
    """Ensure directory exists by creating it if necessary."""

    os.makedirs(directory_path, exist_ok=True)


def _render_template_to_file(template_name: str, output_file_path: str, template_data: dict) -> None:
    """Render template and write to file with error handling."""

    try:
        renderer = TemplateRenderer()
        launcher_xml = renderer.render_template(template_name, **template_data)

        with open(output_file_path, "w") as f:
            f.write(launcher_xml)

        logger.info(f"Successfully generated launcher: {output_file_path}")
    except Exception as e:
        src = SourceLocation(file_path=Path(output_file_path))
        logger.error(f"Failed to generate launcher {output_file_path}: {e}{format_source(src)}")
        raise


def _collect_all_nodes_recursively(instance: Instance) -> List[Dict[str, Any]]:
    """Recursively collect all nodes within a component, tracking their namespace paths."""

    nodes: List[Dict[str, Any]] = []

    def traverse(current_instance: Instance, module_path: List[str]):
        for child_name, child_instance in current_instance.children.items():
            if child_instance.entity_type == "node":
                node_data = _extract_node_data(child_instance, module_path)
                nodes.append(node_data)
            elif child_instance.entity_type == "module":
                new_module_path = module_path + [child_name]
                traverse(child_instance, new_module_path)

    if instance.entity_type == "module":
        traverse(instance, [])
    elif instance.entity_type == "node":
        node_data = _extract_node_data(instance, [])
        nodes.append(node_data)

    return nodes


def _collect_all_nodes_recursively_data(instance_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Recursively collect all nodes within a component from serialized data."""

    nodes: List[Dict[str, Any]] = []

    def traverse(current_data: Dict[str, Any], module_path: List[str]):
        for child in current_data.get("children", []):
            if child.get("entity_type") == "node":
                node_data = _extract_node_data_from_dict(child, module_path)
                nodes.append(node_data)
            elif child.get("entity_type") == "module":
                new_module_path = module_path + [child.get("name")]
                traverse(child, new_module_path)

    if instance_data.get("entity_type") == "module":
        traverse(instance_data, [])
    elif instance_data.get("entity_type") == "node":
        node_data = _extract_node_data_from_dict(instance_data, [])
        nodes.append(node_data)

    return nodes


def _extract_node_data(node_instance: Instance, module_path: List[str]) -> Dict[str, Any]:
    """Extract data from a node instance for launcher generation."""

    node_data: Dict[str, Any] = {}
    node_data["name"] = node_instance.name
    node_data["namespace_groups"] = module_path.copy()
    node_data["full_namespace_path"] = "/".join(module_path) if module_path else ""

    launch_config = node_instance.configuration.launch
    node_data["package"] = node_instance.configuration.package_name
    node_data["ros2_launch_file"] = launch_config.get("ros2_launch_file", None)
    is_ros2_file_launch = True if node_data["ros2_launch_file"] is not None else False
    node_data["is_ros2_file_launch"] = is_ros2_file_launch
    node_data["node_output"] = launch_config.get("node_output", "screen")

    raw_args = launch_config.get("args", "")
    node_data["args"] = node_instance.parameter_manager.resolve_substitutions(raw_args)

    if is_ros2_file_launch is False:
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
    """Extract node data from serialized instance dict for launcher generation."""

    node_data = {
        "name": node_instance.get("name", ""),
        "namespace_groups": module_path.copy(),
        "full_namespace_path": "/".join(module_path) if module_path else "",
    }

    launch_data = node_instance.get("launcher", {})
    node_data["package"] = launch_data.get("package", "")
    node_data["ros2_launch_file"] = launch_data.get("ros2_launch_file", None)
    node_data["is_ros2_file_launch"] = True if node_data["ros2_launch_file"] is not None else False
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

    parameters = []
    for param in launch_data.get("parameters", []):
        param_copy = dict(param)
        param_copy["parameter_type"] = normalize_parameter_type(param.get("parameter_type"))
        parameters.append(param_copy)
    node_data["parameters"] = parameters

    parameter_files = []
    for param_file in launch_data.get("parameter_files", []):
        param_file_copy = dict(param_file)
        param_file_copy["parameter_type"] = normalize_parameter_type(param_file.get("parameter_type"))
        parameter_files.append(param_file_copy)
    node_data["parameter_files"] = parameter_files

    return node_data


def _generate_compute_unit_launcher(
    compute_unit: str,
    components: list,
    output_dir: str,
    forward_args: List[str] | None = None,
    namespace_forward_args: Dict[str, List[str]] | None = None,
):
    """Generate compute unit launcher file."""

    compute_unit_dir = os.path.join(output_dir, compute_unit)
    _ensure_directory(compute_unit_dir)

    launcher_file = os.path.join(compute_unit_dir, f"{compute_unit.lower()}.launch.xml")
    logger.debug(f"Creating compute unit launcher: {launcher_file}")

    namespaces_data = []
    for component in sorted(components, key=lambda c: c.name):
        component_args = (namespace_forward_args or {}).get(component.name, [])
        namespaces_data.append({"namespace": component.name, "args": component_args})

    template_data = {
        "compute_unit": compute_unit,
        "namespaces": namespaces_data,
        "forward_args": forward_args or [],
    }
    _render_template_to_file("compute_unit_launcher.xml.jinja2", launcher_file, template_data)


def _generate_component_launcher(
    compute_unit: str,
    namespace: str,
    components: list,
    output_dir: str,
    forward_args: List[str] | None = None,
):
    """Generate component launcher file that directly launches all nodes in the component."""

    component_dir = os.path.join(output_dir, compute_unit, namespace)
    _ensure_directory(component_dir)

    filename = namespace.replace("/", "__")
    launcher_file = os.path.join(component_dir, f"{filename}.launch.xml")
    logger.debug(f"Creating component launcher: {launcher_file}")

    all_nodes = []
    component_full_namespace = []
    for component in components:
        nodes = _collect_all_nodes_recursively(component)
        all_nodes.extend(nodes)
        if not component_full_namespace and hasattr(component, "namespace"):
            component_full_namespace = component.namespace.copy()

    for node in all_nodes:
        full_ns_list = component_full_namespace + node["namespace_groups"]
        node["full_namespace"] = "/".join(full_ns_list)

    component_forward_args = ParameterManager.collect_component_required_system_args(
        all_nodes, forward_args
    )
    template_data = {
        "compute_unit": compute_unit,
        "namespace": namespace,
        "component_full_namespace": component_full_namespace,
        "nodes": all_nodes,
        "forward_args": component_forward_args,
    }
    _render_template_to_file("component_launcher.xml.jinja2", launcher_file, template_data)


def _generate_component_launcher_from_data(
    compute_unit: str,
    namespace: str,
    components: list,
    output_dir: str,
    forward_args: List[str] | None = None,
):
    """Generate component launcher file from serialized system structure."""

    component_dir = os.path.join(output_dir, compute_unit, namespace)
    _ensure_directory(component_dir)

    filename = namespace.replace("/", "__")
    launcher_file = os.path.join(component_dir, f"{filename}.launch.xml")
    logger.debug(f"Creating component launcher: {launcher_file}")

    all_nodes = []
    component_full_namespace = []
    for component in components:
        nodes = _collect_all_nodes_recursively_data(component)
        all_nodes.extend(nodes)
        if not component_full_namespace:
            component_full_namespace = component.get("namespace", [])

    for node in all_nodes:
        full_ns_list = component_full_namespace + node["namespace_groups"]
        node["full_namespace"] = "/".join(full_ns_list)

    component_forward_args = ParameterManager.collect_component_required_system_args(
        all_nodes, forward_args
    )
    template_data = {
        "compute_unit": compute_unit,
        "namespace": namespace,
        "component_full_namespace": component_full_namespace,
        "nodes": all_nodes,
        "forward_args": component_forward_args,
    }
    _render_template_to_file("component_launcher.xml.jinja2", launcher_file, template_data)


def _generate_compute_unit_launcher_from_data(
    compute_unit: str,
    components: list,
    output_dir: str,
    forward_args: List[str] | None = None,
    namespace_forward_args: Dict[str, List[str]] | None = None,
):
    """Generate compute unit launcher from serialized system structure."""

    compute_unit_dir = os.path.join(output_dir, compute_unit)
    _ensure_directory(compute_unit_dir)

    launcher_file = os.path.join(compute_unit_dir, f"{compute_unit.lower()}.launch.xml")
    logger.debug(f"Creating compute unit launcher: {launcher_file}")

    namespaces_data = []
    for component in sorted(components, key=lambda c: c.get("name", "")):
        component_name = component.get("name", "")
        component_args = (namespace_forward_args or {}).get(component_name, [])
        namespaces_data.append({"namespace": component_name, "args": component_args})

    template_data = {
        "compute_unit": compute_unit,
        "namespaces": namespaces_data,
        "forward_args": forward_args or [],
    }
    _render_template_to_file("compute_unit_launcher.xml.jinja2", launcher_file, template_data)


def generate_module_launch_file(
    instance: Instance, output_dir: str, forward_args: List[str] | None = None
):
    """Main entry point for launcher generation."""

    if isinstance(instance, Instance):
        logger.debug(
            f"Generating launcher for {instance.name} (type: {instance.entity_type}) in {output_dir}"
        )

        if instance.entity_type == "system":
            compute_unit_map: Dict[str, list] = {}
            namespace_args_map: Dict[tuple, List[str]] = {}
            for child in instance.children.values():
                compute_unit_map.setdefault(child.compute_unit, []).append(child)
                nodes = _collect_all_nodes_recursively(child)
                namespace_args_map[
                    (child.compute_unit, child.name)
                ] = ParameterManager.collect_component_required_system_args(nodes, forward_args)

            namespace_map = {}
            for child in instance.children.values():
                key = (child.compute_unit, child.name)
                namespace_map[key] = [child]

            for compute_unit, components in compute_unit_map.items():
                component_args_map = {
                    component.name: namespace_args_map.get((compute_unit, component.name), [])
                    for component in components
                }
                _generate_compute_unit_launcher(
                    compute_unit,
                    components,
                    output_dir,
                    forward_args=forward_args,
                    namespace_forward_args=component_args_map,
                )

            for (compute_unit, namespace), components in namespace_map.items():
                _generate_component_launcher(
                    compute_unit,
                    namespace,
                    components,
                    output_dir,
                    forward_args=forward_args,
                )

        elif instance.entity_type in ("module", "node"):
            logger.debug(
                f"Skipping launcher for {instance.name} (type: {instance.entity_type}) - handled upstream"
            )
            return
        return

    instance_data, _ = extract_system_structure_data(instance)
    logger.debug(f"Generating launcher from system structure data in {output_dir}")

    if instance_data.get("entity_type") != "system":
        logger.debug("Launcher generation expects system-level data; skipping.")
        return

    compute_unit_map = {}
    namespace_map = {}
    namespace_args_map: Dict[tuple, List[str]] = {}
    for child in instance_data.get("children", []):
        compute_unit = child.get("compute_unit", "")
        compute_unit_map.setdefault(compute_unit, []).append(child)
        key = (compute_unit, child.get("name", ""))
        namespace_map[key] = [child]
        nodes = _collect_all_nodes_recursively_data(child)
        namespace_args_map[key] = ParameterManager.collect_component_required_system_args(
            nodes, forward_args
        )

    for compute_unit, components in compute_unit_map.items():
        component_args_map = {
            component.get("name", ""): namespace_args_map.get((compute_unit, component.get("name", "")), [])
            for component in components
        }
        _generate_compute_unit_launcher_from_data(
            compute_unit,
            components,
            output_dir,
            forward_args=forward_args,
            namespace_forward_args=component_args_map,
        )

    for (compute_unit, namespace), components in namespace_map.items():
        _generate_component_launcher_from_data(
            compute_unit,
            namespace,
            components,
            output_dir,
            forward_args=forward_args,
        )
