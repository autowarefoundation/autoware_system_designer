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

"""Border: components launched behind a fixed launch-file interface.

A ``*.border.yaml`` file is a local build input, like a deployments table: plain YAML
without a format declaration or schema. It describes components as a system does,
the launch arguments of the generated launch file, its external interface (ports bound
to topics), the launch files included ahead of the components (system launches), node
groups and connections. Connections reach the interface as a module's
do, through ``subscriber.<port>`` and ``publisher.<port>``. The border builds as a system
whose boundary ports are the interface ports, each bound to its topic.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.path_utils import canonical_path
from autoware_system_designer.common.source_location import format_source, source_from_source_map
from autoware_system_designer.model.config import SystemConfig
from autoware_system_designer.model.domain import PortDefinition
from autoware_system_designer.parser.data_validator import entity_name_decode, split_package_qualifier
from autoware_system_designer.parser.yaml_parser import yaml_parser

logger = logging.getLogger(__name__)

BORDER_SUFFIX = ".border.yaml"

# The border name becomes the launch file stem, the export directory and the compute unit name.
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_ARGUMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_/]*$")

_KNOWN_KEYS = (
    "name",
    "components",
    "arguments",
    "variables",
    "subscribers",
    "publishers",
    "system_launches",
    "node_groups",
    "connections",
)
_COMPONENT_KEYS = ("name", "entity", "path", "parameter_set")
_INTERFACE_ROLES = ("subscriber", "publisher")
_SYSTEM_LAUNCH_KEYS = ("package", "file", "scoped", "arguments")


def is_border_file(path: str) -> bool:
    return isinstance(path, str) and path.endswith(BORDER_SUFFIX)


@dataclass
class BorderPort:
    name: str
    topic: str


@dataclass
class BorderArgument:
    name: str
    default: Optional[str] = None
    description: str = ""


@dataclass
class BorderSystemLaunch:
    """Launch file included ahead of the components, e.g. the global parameter loader."""

    package: str
    file: str
    scoped: bool = True
    arguments: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class BorderConfig:
    """Parsed border file."""

    name: str
    file_path: Path
    components: List[Dict[str, Any]] = field(default_factory=list)
    arguments: List[BorderArgument] = field(default_factory=list)
    variables: List[Dict[str, Any]] = field(default_factory=list)
    subscribers: List[BorderPort] = field(default_factory=list)
    publishers: List[BorderPort] = field(default_factory=list)
    system_launches: List[BorderSystemLaunch] = field(default_factory=list)
    node_groups: List[Dict[str, Any]] = field(default_factory=list)
    connections: List[List[str]] = field(default_factory=list)
    source_map: Optional[Dict[str, Dict[str, int]]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def ports(self, role: str) -> List[BorderPort]:
        return self.subscribers if role == "subscriber" else self.publishers


class _Reader:
    """Field access over the border YAML; every error names the file and the field."""

    def __init__(self, file_path: Path, source_map: Optional[Dict[str, Dict[str, int]]]):
        self.file_path = file_path
        self.source_map = source_map

    def error(self, yaml_path: str, message: str) -> ValidationError:
        source = source_from_source_map(self.source_map, self.file_path, yaml_path)
        return ValidationError(f"Border {self.file_path.name}: {message}{format_source(source)}", source=source)

    def string(self, data: Dict[str, Any], key: str, yaml_path: str, required: bool = True) -> Optional[str]:
        value = data.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value.strip():
            raise self.error(f"{yaml_path}/{key}", f"'{key}' must be a non-empty string")
        return value.strip()

    def items(self, data: Dict[str, Any], key: str, kind: type = dict) -> List[Any]:
        value = data.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise self.error(f"/{key}", f"'{key}' must be a list")
        for idx, item in enumerate(value):
            if not isinstance(item, kind):
                raise self.error(f"/{key}/{idx}", f"'{key}[{idx}]' must be a {'mapping' if kind is dict else 'list'}")
        return value


def _parse_components(reader: _Reader, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    components = reader.items(data, "components")
    if not components:
        raise reader.error("/components", "'components' must list at least one component")
    seen = set()
    for idx, item in enumerate(components):
        yaml_path = f"/components/{idx}"
        unknown = sorted(set(item) - set(_COMPONENT_KEYS))
        if unknown:
            raise reader.error(f"{yaml_path}/{unknown[0]}", f"unknown component key(s) {unknown}")
        name = reader.string(item, "name", yaml_path)
        if name in _INTERFACE_ROLES:
            raise reader.error(f"{yaml_path}/name", f"component name '{name}' is reserved for interface references")
        if name in seen:
            raise reader.error(f"{yaml_path}/name", f"component '{name}' is declared twice")
        seen.add(name)
        entity = reader.string(item, "entity", yaml_path)
        try:
            _, entity_type = entity_name_decode(split_package_qualifier(entity)[1])
        except ValidationError as exc:
            raise reader.error(f"{yaml_path}/entity", exc.message) from exc
        if entity_type not in ("module", "node"):
            raise reader.error(f"{yaml_path}/entity", f"entity '{entity}' must be a module or a node")
        path = reader.string(item, "path", yaml_path, required=False)
        if path is not None and not path.startswith("/"):
            raise reader.error(f"{yaml_path}/path", f"path '{path}' must be absolute")
        sets = item.get("parameter_set", [])
        if not isinstance(sets, list) or not all(isinstance(s, str) and s for s in sets):
            raise reader.error(f"{yaml_path}/parameter_set", "'parameter_set' must be a list of parameter set names")
    return components


def _parse_arguments(reader: _Reader, data: Dict[str, Any]) -> List[BorderArgument]:
    arguments: List[BorderArgument] = []
    seen = set()
    for idx, item in enumerate(reader.items(data, "arguments")):
        yaml_path = f"/arguments/{idx}"
        name = reader.string(item, "name", yaml_path)
        if not _ARGUMENT_NAME.match(name):
            raise reader.error(f"{yaml_path}/name", f"argument name '{name}' is not a valid launch argument name")
        if name in seen:
            raise reader.error(f"{yaml_path}/name", f"argument '{name}' is declared twice")
        seen.add(name)
        default = item.get("default")
        if isinstance(default, bool):
            default = "true" if default else "false"
        elif default is not None and not isinstance(default, str):
            default = str(default)
        description = item.get("description", "")
        if not isinstance(description, str):
            raise reader.error(f"{yaml_path}/description", "'description' must be a string")
        arguments.append(BorderArgument(name=name, default=default, description=description))
    return arguments


def _parse_variables(reader: _Reader, data: Dict[str, Any], argument_names: set) -> List[Dict[str, Any]]:
    variables = reader.items(data, "variables")
    seen = set()
    for idx, item in enumerate(variables):
        yaml_path = f"/variables/{idx}"
        name = reader.string(item, "name", yaml_path)
        if name in seen:
            raise reader.error(f"{yaml_path}/name", f"variable '{name}' is declared twice")
        if name in argument_names:
            raise reader.error(f"{yaml_path}/name", f"'{name}' is declared as both an argument and a variable")
        if item.get("value") is None:
            raise reader.error(f"{yaml_path}/value", f"variable '{name}' requires 'value'")
        seen.add(name)
    return variables


def _parse_ports(reader: _Reader, data: Dict[str, Any], key: str) -> List[BorderPort]:
    ports: List[BorderPort] = []
    seen = set()
    for idx, item in enumerate(reader.items(data, key)):
        yaml_path = f"/{key}/{idx}"
        name = reader.string(item, "name", yaml_path)
        topic = reader.string(item, "topic", yaml_path)
        if name in seen:
            raise reader.error(f"{yaml_path}/name", f"{key} port '{name}' is declared twice")
        segments = topic.lstrip("/").split("/")
        if not topic.startswith("/") or not all(segments):
            raise reader.error(f"{yaml_path}/topic", f"topic '{topic}' must be an absolute ROS topic, e.g. '/foo/bar'")
        seen.add(name)
        ports.append(BorderPort(name=name, topic=topic))
    return ports


def _parse_system_launches(reader: _Reader, data: Dict[str, Any]) -> List[BorderSystemLaunch]:
    launches: List[BorderSystemLaunch] = []
    for idx, item in enumerate(reader.items(data, "system_launches")):
        yaml_path = f"/system_launches/{idx}"
        unknown = sorted(set(item) - set(_SYSTEM_LAUNCH_KEYS))
        if unknown:
            raise reader.error(f"{yaml_path}/{unknown[0]}", f"unknown system launch key(s) {unknown}")
        scoped = item.get("scoped", True)
        if not isinstance(scoped, bool):
            raise reader.error(f"{yaml_path}/scoped", "'scoped' must be a boolean")
        arguments: List[Tuple[str, str]] = []
        raw_arguments = item.get("arguments", [])
        if not isinstance(raw_arguments, list):
            raise reader.error(f"{yaml_path}/arguments", "'arguments' must be a list")
        for arg_idx, argument in enumerate(raw_arguments):
            arg_path = f"{yaml_path}/arguments/{arg_idx}"
            if not isinstance(argument, dict):
                raise reader.error(arg_path, "a system launch argument is a mapping of 'name' and 'value'")
            name = reader.string(argument, "name", arg_path)
            value = argument.get("value")
            if isinstance(value, bool):
                value = "true" if value else "false"
            elif value is None:
                raise reader.error(f"{arg_path}/value", f"system launch argument '{name}' requires 'value'")
            arguments.append((name, str(value)))
        launches.append(
            BorderSystemLaunch(
                package=reader.string(item, "package", yaml_path),
                file=reader.string(item, "file", yaml_path),
                scoped=scoped,
                arguments=arguments,
            )
        )
    return launches


def _parse_connections(reader: _Reader, data: Dict[str, Any]) -> List[List[str]]:
    connections = reader.items(data, "connections", kind=list)
    for idx, item in enumerate(connections):
        if len(item) != 2 or not all(isinstance(end, str) and end for end in item):
            raise reader.error(f"/connections/{idx}", "a connection is a pair of port references")
    return connections


def parse_border(border_path: str) -> BorderConfig:
    """Parse and validate a border file; connections are checked against the entities when the system is linked."""
    file_path = Path(canonical_path(border_path))
    if not file_path.is_file():
        raise ValidationError(f"Border file not found: {border_path}")
    data, source_map = yaml_parser.load_config_with_source(file_path)
    reader = _Reader(file_path, source_map)
    if not isinstance(data, dict):
        raise reader.error("", "top level must be a mapping")

    unknown = sorted(set(data) - set(_KNOWN_KEYS))
    if unknown:
        raise reader.error(f"/{unknown[0]}", f"unknown key(s) {unknown}; supported: {list(_KNOWN_KEYS)}")

    name = reader.string(data, "name", "")
    if not _NAME.match(name):
        raise reader.error("/name", f"name '{name}' must match {_NAME.pattern}")
    expected_stem = file_path.name[: -len(BORDER_SUFFIX)]
    if name != expected_stem:
        raise reader.error("/name", f"name '{name}' differs from the file name '{expected_stem}{BORDER_SUFFIX}'")

    arguments = _parse_arguments(reader, data)
    return BorderConfig(
        name=name,
        file_path=file_path,
        components=_parse_components(reader, data),
        arguments=arguments,
        variables=_parse_variables(reader, data, {a.name for a in arguments}),
        subscribers=_parse_ports(reader, data, "subscribers"),
        publishers=_parse_ports(reader, data, "publishers"),
        system_launches=_parse_system_launches(reader, data),
        node_groups=reader.items(data, "node_groups"),
        connections=_parse_connections(reader, data),
        source_map=source_map,
        raw=data,
    )


def _interface_ports(border: BorderConfig, role: str) -> List[PortDefinition]:
    return [PortDefinition(name=port.name, port_role=role, topic=port.topic) for port in border.ports(role)]


def border_system_config(border: BorderConfig, config_registry: Any) -> SystemConfig:
    """System the deployment build runs for the border; all components share one compute unit.

    The interface ports are the boundary ports of the system, each bound to its topic;
    connections reach them as a module's connections reach its ports.
    """
    components = []
    for component in border.components:
        entry = {key: component[key] for key in _COMPONENT_KEYS if key in component}
        entry["compute_unit"] = border.name
        components.append(entry)

    return SystemConfig(
        name=border.name,
        full_name=f"{border.name}.system",
        entity_type="system",
        config=border.raw,
        file_path=border.file_path,
        source_map=border.source_map,
        package=config_registry.deployment_package_name,
        arguments=[{"name": argument.name} for argument in border.arguments],
        components=components,
        connections=border.connections,
        variables=border.variables or None,
        node_groups=border.node_groups or None,
        inputs=_interface_ports(border, "subscriber"),
        outputs=_interface_ports(border, "publisher"),
    )
