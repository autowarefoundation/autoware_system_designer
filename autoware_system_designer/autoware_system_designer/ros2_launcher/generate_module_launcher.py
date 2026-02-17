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

import logging
import os
from pathlib import Path
from typing import Dict, List

from ..builder.instances.instances import Instance
from ..builder.instances.launcher_planner import (
    build_runtime_system_component_maps,
    build_serialized_system_component_maps,
    collect_component_nodes,
    collect_component_nodes_from_data,
)
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


def _generate_compute_unit_launcher(
    compute_unit: str,
    components: list,
    output_dir: str,
    forward_args: List[str] | None = None,
    component_forward_args: Dict[str, List[str]] | None = None,
):
    """Generate compute unit launcher file."""

    compute_unit_dir = os.path.join(output_dir, compute_unit)
    _ensure_directory(compute_unit_dir)

    launcher_file = os.path.join(compute_unit_dir, f"{compute_unit.lower()}.launch.xml")
    logger.debug(f"Creating compute unit launcher: {launcher_file}")

    components_data = []
    for component in sorted(components, key=lambda c: c.name):
        args_for_component = (component_forward_args or {}).get(component.name, [])
        components_data.append({"component": component.name, "args": args_for_component})

    template_data = {
        "compute_unit": compute_unit,
        "components": components_data,
        "forward_args": forward_args or [],
    }
    _render_template_to_file("compute_unit_launcher.xml.jinja2", launcher_file, template_data)


def _generate_component_launcher(
    compute_unit: str,
    namespace: str,
    components: list,
    output_dir: str,
    component_forward_args: List[str] | None = None,
):
    """Generate component launcher file that directly launches all nodes in the component."""

    component_dir = os.path.join(output_dir, compute_unit, namespace)
    _ensure_directory(component_dir)

    filename = namespace.replace("/", "__")
    launcher_file = os.path.join(component_dir, f"{filename}.launch.xml")
    logger.debug(f"Creating component launcher: {launcher_file}")

    all_nodes = []
    for component in components:
        all_nodes.extend(collect_component_nodes(component))

    template_data = {
        "compute_unit": compute_unit,
        "namespace": namespace,
        "nodes": all_nodes,
        "forward_args": component_forward_args or [],
    }
    _render_template_to_file("component_launcher.xml.jinja2", launcher_file, template_data)


def _generate_component_launcher_from_data(
    compute_unit: str,
    namespace: str,
    components: list,
    output_dir: str,
    component_forward_args: List[str] | None = None,
):
    """Generate component launcher file from serialized system structure."""

    component_dir = os.path.join(output_dir, compute_unit, namespace)
    _ensure_directory(component_dir)

    filename = namespace.replace("/", "__")
    launcher_file = os.path.join(component_dir, f"{filename}.launch.xml")
    logger.debug(f"Creating component launcher: {launcher_file}")

    all_nodes = []
    for component in components:
        all_nodes.extend(collect_component_nodes_from_data(component))

    template_data = {
        "compute_unit": compute_unit,
        "namespace": namespace,
        "nodes": all_nodes,
        "forward_args": component_forward_args or [],
    }
    _render_template_to_file("component_launcher.xml.jinja2", launcher_file, template_data)


def _generate_compute_unit_launcher_from_data(
    compute_unit: str,
    components: list,
    output_dir: str,
    forward_args: List[str] | None = None,
    component_forward_args: Dict[str, List[str]] | None = None,
):
    """Generate compute unit launcher from serialized system structure."""

    compute_unit_dir = os.path.join(output_dir, compute_unit)
    _ensure_directory(compute_unit_dir)

    launcher_file = os.path.join(compute_unit_dir, f"{compute_unit.lower()}.launch.xml")
    logger.debug(f"Creating compute unit launcher: {launcher_file}")

    components_data = []
    for component in sorted(components, key=lambda c: c.get("name", "")):
        component_name = component.get("name", "")
        args_for_component = (component_forward_args or {}).get(component_name, [])
        components_data.append({"component": component_name, "args": args_for_component})

    template_data = {
        "compute_unit": compute_unit,
        "components": components_data,
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
            compute_unit_map, component_args_by_id, component_map = (
                build_runtime_system_component_maps(instance, forward_args)
            )

            for compute_unit, components in compute_unit_map.items():
                component_args_map = {
                    component.name: component_args_by_id.get((compute_unit, component.name), [])
                    for component in components
                }
                _generate_compute_unit_launcher(
                    compute_unit,
                    components,
                    output_dir,
                    forward_args=forward_args,
                    component_forward_args=component_args_map,
                )

            for (compute_unit, component_name), components in component_map.items():
                _generate_component_launcher(
                    compute_unit,
                    component_name,
                    components,
                    output_dir,
                    component_forward_args=component_args_by_id.get(
                        (compute_unit, component_name), []
                    ),
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

    compute_unit_map, component_args_by_id, component_map = build_serialized_system_component_maps(
        instance_data, forward_args
    )

    for compute_unit, components in compute_unit_map.items():
        component_args_map = {
            component.get("name", ""): component_args_by_id.get(
                (compute_unit, component.get("name", "")), []
            )
            for component in components
        }
        _generate_compute_unit_launcher_from_data(
            compute_unit,
            components,
            output_dir,
            forward_args=forward_args,
            component_forward_args=component_args_map,
        )

    for (compute_unit, component_name), components in component_map.items():
        _generate_component_launcher_from_data(
            compute_unit,
            component_name,
            components,
            output_dir,
            component_forward_args=component_args_by_id.get((compute_unit, component_name), []),
        )
