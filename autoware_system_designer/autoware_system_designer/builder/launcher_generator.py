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

import os
import logging
from typing import List, Dict, Any
from .instances import Instance
from ..utils.template_utils import TemplateRenderer

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
        logger.error(f"Failed to generate launcher {output_file_path}: {e}")
        raise


def _collect_all_nodes_recursively(instance: Instance) -> List[Dict[str, Any]]:
    """Recursively collect all nodes within a component, tracking their namespace paths."""
    nodes = []
    
    def traverse(current_instance: Instance, module_path: List[str]):
        """Recursively traverse instance tree to find all nodes."""
        for child_name, child_instance in current_instance.children.items():
            if child_instance.entity_type == "node":
                # Extract node information
                node_data = _extract_node_data(child_instance, module_path)
                nodes.append(node_data)
            elif child_instance.entity_type == "module":
                # For modules, add to the namespace path and continue traversing
                new_module_path = module_path + [child_name]
                traverse(child_instance, new_module_path)
    
    # Start traversal from the root component (which could be a module or node)
    if instance.entity_type == "module":
        traverse(instance, [])
    elif instance.entity_type == "node":
        # If it's already a node at the top level, just extract it
        node_data = _extract_node_data(instance, [])
        nodes.append(node_data)
    
    return nodes


def _extract_node_data(node_instance: Instance, module_path: List[str]) -> Dict[str, Any]:
    """Extract all necessary data from a node instance for launcher generation.
    
    This function uses the already-parsed parameters from parameter_manager instead of
    re-parsing the node configuration.
    """
    node_data = {}
    node_data["name"] = node_instance.name
    # Calculate namespace groups for nested push-ros-namespace
    # module_path contains the intermediate module names
    node_data["namespace_groups"] = module_path.copy()
     # Calculate full namespace path for documentation
    node_data["full_namespace_path"] = "/".join(module_path) if module_path else ""

    launch_config = node_instance.configuration.launch
    # Extract launch information
    node_data["package"] = launch_config.get("package", "")
    node_data["ros2_launch_file"] = launch_config.get("ros2_launch_file", None) # command execution mode
    is_ros2_file_launch = True if node_data["ros2_launch_file"] is not None else False
    node_data["is_ros2_file_launch"] = is_ros2_file_launch
    node_data["node_output"] = launch_config.get("node_output", "screen")

    if is_ros2_file_launch is False:
        node_data["plugin"] = launch_config.get("plugin", "")
        node_data["executable"] = launch_config.get("executable", "")
        node_data["use_container"] = launch_config.get("use_container", False)  # Default to regular node
        node_data["container"] = launch_config.get("container_name", "perception_container")
        
    # Collect ports with resolved topics
    ports = []
    # Add input ports
    for port in node_instance.link_manager.get_all_in_ports():
        topic = port.get_topic()
        if topic == "":
            continue
        ports.append({
            "direction": "input",
            "name": port.name,
            "topic": topic
        })
    # Add output ports
    for port in node_instance.link_manager.get_all_out_ports():
        topic = port.get_topic()
        if topic == "":
            continue
        ports.append({
            "direction": "output",
            "name": port.name,
            "topic": topic
        })
    node_data["ports"] = ports
        
    # Get parameters and parameter files from parameter_manager
    node_data["parameters"] =  node_instance.parameter_manager.get_parameters_for_launch()
    node_data["parameter_files"] = node_instance.parameter_manager.get_parameter_files_for_launch()

    return node_data


def _generate_compute_unit_launcher(compute_unit: str, components: list, output_dir: str):
    """Generate compute unit launcher file that launches all namespaces in the compute unit."""
    compute_unit_dir = os.path.join(output_dir, compute_unit)
    _ensure_directory(compute_unit_dir)
    
    launcher_file = os.path.join(compute_unit_dir, f"{compute_unit.lower()}.launch.xml")
    
    logger.debug(f"Creating compute unit launcher: {launcher_file}")
    
    # Previously components were grouped by the first namespace segment (component.namespace[0]).
    # Requirement change: treat each direct child instance (component) independently so that
    # compute unit launchers reference each component launcher one-to-one.
    namespaces_data = []
    for component in sorted(components, key=lambda c: c.name):
        namespace_info = {
            "namespace": component.name,  # use component (instance) name directly
            "component_count": 1,
            "args": []
        }
        namespaces_data.append(namespace_info)
    
    template_data = {
        "compute_unit": compute_unit,
        "namespaces": namespaces_data
    }
    
    _render_template_to_file('compute_unit_launcher.xml.jinja2', launcher_file, template_data)


def _generate_component_launcher(compute_unit: str, namespace: str, components: list, output_dir: str):
    """Generate component launcher file that directly launches all nodes in the component."""
    component_dir = os.path.join(output_dir, compute_unit, namespace)
    _ensure_directory(component_dir)
    
    # Sanitize namespace for filename: replace slashes with double underscores
    # This prevents creating nested directories in the filename while preserving the full name
    filename = namespace.replace('/', '__')
    
    launcher_file = os.path.join(component_dir, f"{filename}.launch.xml")
    
    logger.debug(f"Creating component launcher: {launcher_file}")
    
    # Collect all nodes recursively from all components in this namespace
    all_nodes = []
    component_full_namespace = []
    for component in components:
        nodes = _collect_all_nodes_recursively(component)
        all_nodes.extend(nodes)
        # Extract the full namespace from the component (should be same for all components in this group)
        if not component_full_namespace and hasattr(component, 'namespace'):
            component_full_namespace = component.namespace.copy()
    
    # Enrich nodes with full absolute namespace
    for node in all_nodes:
        # Combine component base namespace with node's internal namespace structure
        full_ns_list = component_full_namespace + node['namespace_groups']
        # Create namespace string (e.g., "perception/object_recognition/detection")
        # We use relative path here, assuming the launcher will be included in a root context
        # or the user wants these paths to be relative to wherever this launch file is included.
        node['full_namespace'] = "/".join(full_ns_list)

    # Prepare template data
    template_data = {
        "compute_unit": compute_unit,
        "namespace": namespace,
        "component_full_namespace": component_full_namespace,
        "nodes": all_nodes
    }
    
    _render_template_to_file('component_launcher.xml.jinja2', launcher_file, template_data)


def generate_module_launch_file(instance: Instance, output_dir: str):
    """Main entry point for launcher generation."""
    logger.debug(f"Generating launcher for {instance.name} (type: {instance.entity_type}) in {output_dir}")
    
    if instance.entity_type == "system":
        # Group components by compute unit
        compute_unit_map = {}
        for child in instance.children.values():
            if child.compute_unit not in compute_unit_map:
                compute_unit_map[child.compute_unit] = []
            compute_unit_map[child.compute_unit].append(child)
        
        # Group components by compute unit and child instance (no longer by first namespace element)
        namespace_map = {}
        for child in instance.children.values():
            key = (child.compute_unit, child.name)
            namespace_map[key] = [child]

        # Generate compute unit launchers
        for compute_unit, components in compute_unit_map.items():
            _generate_compute_unit_launcher(compute_unit, components, output_dir)

        # Generate component launchers (one per direct child instance)
        for (compute_unit, namespace), components in namespace_map.items():
            _generate_component_launcher(compute_unit, namespace, components, output_dir)
    
    elif instance.entity_type == "module":
        # Modules are now handled within component launchers, no separate files needed
        logger.debug(f"Skipping separate module launcher for {instance.name} - handled in component launcher")
        return
    
    elif instance.entity_type == "node":
        # Node launch files are generated by the package build process
        logger.debug(f"Skipping node launcher for {instance.name} - handled by package")
        return
