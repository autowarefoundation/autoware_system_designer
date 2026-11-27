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

"""Launch file of a subsystem, laid out as a hand-written entry launch file.

Sections in order: launch arguments, external interfaces as arguments, system launches,
then the nodes in groups that push one namespace segment each. Node remaps reach interface
topics through the interface arguments. Node parameters load from the parameter files written
beside the launch file, and package paths resolve through find-pkg-share, so the output does not
depend on the workspace it was built in.
"""

import logging
import os
import shutil
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.template_renderer import TemplateRenderer
from autoware_system_designer.generator.ros2_launcher.component_map import collect_component_nodes
from autoware_system_designer.generator.subsystem_parameters import PackagePaths, write_parameter_files
from autoware_system_designer.parser.subsystem_parser import SubsystemConfig

logger = logging.getLogger(__name__)

_INDENT = "  "


@dataclass
class _Interface:
    name: str
    topic: str


@dataclass
class _Group:
    """One namespace segment; nodes in it are launched without a namespace of their own.

    Entries keep their insertion order: a node payload or a child group.
    """

    segment: str
    label: str = ""
    entries: List[Any] = field(default_factory=list)
    children: Dict[str, "_Group"] = field(default_factory=dict)

    def child(self, segment: str) -> "_Group":
        if segment not in self.children:
            self.children[segment] = _Group(segment)
            self.entries.append(self.children[segment])
        return self.children[segment]

    def at(self, namespace: str) -> "_Group":
        group = self
        for segment in (s for s in (namespace or "").split("/") if s):
            group = group.child(segment)
        return group


def _entity_name(data: Dict[str, Any]) -> str:
    source = os.path.basename(data.get("source_file") or "")
    return source[: -len(".yaml")] if source.endswith(".yaml") else source


def _external_interfaces(subsystem: SubsystemConfig) -> List[_Interface]:
    return [_Interface(f"input/{p.name}", p.topic) for p in subsystem.inputs] + [
        _Interface(f"output/{p.name}", p.topic) for p in subsystem.outputs
    ]


def _check_launch_names(subsystem: SubsystemConfig, external: List[_Interface]) -> None:
    """Arguments and interfaces share one launch configuration namespace; each name is declared once."""
    declared: Dict[str, str] = {}
    entries = [(a.name, "argument") for a in subsystem.arguments] + [(i.name, "external interface") for i in external]
    for name, kind in entries:
        if name in declared:
            raise ValidationError(
                f"Subsystem {subsystem.file_path.name}: {kind} '{name}' collides with an {declared[name]}"
            )
        declared[name] = kind


def _with_topic_variables(node: Dict[str, Any], variables: Dict[str, str]) -> Dict[str, Any]:
    ports = [
        {**port, "topic": f"$(var {variables[port['topic']]})"} if port.get("topic") in variables else port
        for port in node.get("ports", [])
    ]
    # A container requires a namespace attribute; an empty one resolves to the group namespace.
    namespace = "" if node.get("launch_state") == "node_container" else None
    return {**node, "ports": ports, "namespace": namespace}


def _build_tree(system_data: Dict[str, Any]) -> Tuple[_Group, List[Dict[str, Any]]]:
    """Namespace tree labelled with the module instances, and every node."""
    root = _Group("")
    nodes: List[Dict[str, Any]] = []

    def label_modules(data: Dict[str, Any]) -> None:
        if data.get("entity_type") != "module":
            return
        root.at(data.get("path")).label = f"{data['name']}: {_entity_name(data)}"
        for child in data.get("children", []):
            label_modules(child)

    # Children of the root: the instance and the containers of the node groups.
    for child in system_data.get("children", []):
        label_modules(child)
        nodes.extend(collect_component_nodes(child))
    return root, nodes


def _render_group(group: _Group, render_node) -> List[str]:
    """Blocks of a group's entries; containers come first so nodes load into a started container."""
    is_container = [isinstance(e, dict) and e.get("launch_state") == "node_container" for e in group.entries]
    entries = [e for e, c in zip(group.entries, is_container) if c] + [
        e for e, c in zip(group.entries, is_container) if not c
    ]
    blocks: List[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            blocks.append(render_node(entry))
            continue
        lines = [f"<!-- {entry.label} -->"] if entry.label else []
        lines.append("<group>")
        lines.append(f'{_INDENT}<push-ros-namespace namespace="{entry.segment}"/>')
        for block in _render_group(entry, render_node):
            lines.append("")
            lines.append(textwrap.indent(block, _INDENT))
        lines.append("</group>")
        blocks.append("\n".join(lines))
    return blocks


def generate_subsystem_launcher(
    subsystem: SubsystemConfig,
    system_data: Dict[str, Any],
    output_file: str,
    package: str,
    output_root: str,
    package_paths: PackagePaths,
) -> str:
    """Write the subsystem launch file and its parameter files.

    Parameter files go to <output_root>/config/<subsystem name>/, which the package installs to its
    share directory at the same relative path.
    """
    output_file = os.path.abspath(output_file)
    param_dir_in_package = f"config/{subsystem.name}"
    param_dir = os.path.join(output_root, param_dir_in_package)
    shutil.rmtree(param_dir, ignore_errors=True)
    renderer = TemplateRenderer()

    external = _external_interfaces(subsystem)
    _check_launch_names(subsystem, external)
    variables: Dict[str, str] = {}
    for interface in external:
        # A topic both subscribed and published at the subsystem is the output it publishes.
        if interface.topic not in variables or interface.name.startswith("output/"):
            variables[interface.topic] = interface.name

    root, nodes = _build_tree(system_data)
    launched = [package_paths.portable(_with_topic_variables(node, variables)) for node in nodes]
    write_parameter_files(launched, param_dir, package, param_dir_in_package)
    for node, payload in zip(nodes, launched):
        root.at(node.get("namespace")).entries.append(payload)

    forward_args = [argument.name for argument in subsystem.arguments]

    def render_node(node: Dict[str, Any]) -> str:
        text = renderer.render_template("launch_nodes.xml.jinja2", nodes=[node], forward_args=forward_args)
        return textwrap.dedent(text).strip("\n")

    body = "\n\n".join(_render_group(root, render_node))
    renderer.render_template_to_file(
        "subsystem_launcher.xml.jinja2",
        output_file,
        source_file=subsystem.file_path.name,
        arguments=subsystem.arguments,
        external_interfaces=external,
        system_launches=subsystem.system_launches,
        body=textwrap.indent(body, _INDENT),
    )
    logger.info(f"Generated subsystem launcher: {output_file}")
    return output_file
