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

"""Scope of a replay and its boundary, both read from the design.

A scope is the nodes under the selected node or module paths, with the containers
their composable nodes load into. Its boundary is where the scope touches the rest
of the system: the input topics some node outside publishes, which the bag feeds,
and the output topics, which the run records. Plain Python with no ROS import.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..measure.node_graph import NodeGraph, NodeInfo, _is_topic_port, _walk, resolve_topic
from ...special_topics import special_topics
from ..ros2.common.namespace import node_fqn


class ScopeError(ValueError):
    """The scope names nothing the design holds."""


@dataclass
class Scope:
    patterns: list[str]
    # Selected nodes by design path, in design order; a node's index is its rank in a tie.
    nodes: list[str]
    # Containers launched for the selected composable nodes, by design path.
    containers: list[str]

    @property
    def members(self) -> list[str]:
        return self.nodes + [path for path in self.containers if path not in self.nodes]


@dataclass
class InputTopic:
    topic: str
    msg_type: str
    subscribers: list[str]
    # Design nodes outside the scope that publish it; empty when the design names no publisher.
    publishers: list[str]
    # Scope nodes that publish it too: the bag carries their recorded messages beside the replayed ones.
    scope_publishers: list[str] = field(default_factory=list)


@dataclass
class OutputTopic:
    topic: str
    msg_type: str
    publishers: list[str]
    # Subscribers of the design; the replay keeps those inside the scope and adds the recorder.
    scope_subscribers: list[str]
    outside_subscribers: list[str]

    @property
    def is_boundary(self) -> bool:
        return bool(self.outside_subscribers) or not self.scope_subscribers


@dataclass
class ServiceEdge:
    name: str
    srv_type: str
    node: str
    role: str  # client | server
    # Scope nodes on the other side; an edge without one leaves the scope.
    scope_peers: list[str]


@dataclass
class Boundary:
    inputs: list[InputTopic] = field(default_factory=list)
    outputs: list[OutputTopic] = field(default_factory=list)
    services: list[ServiceEdge] = field(default_factory=list)

    def input_topics(self) -> dict[str, str]:
        return {entry.topic: entry.msg_type for entry in self.inputs}

    def boundary_outputs(self) -> dict[str, str]:
        return {entry.topic: entry.msg_type for entry in self.outputs if entry.is_boundary}

    def output_types(self) -> dict[str, str]:
        return {entry.topic: entry.msg_type for entry in self.outputs}

    def as_dict(self) -> dict[str, Any]:
        return {
            "inputs": [asdict(entry) for entry in self.inputs],
            "outputs": [{**asdict(entry), "boundary": entry.is_boundary} for entry in self.outputs],
            "services": [asdict(entry) for entry in self.services],
        }


def _launched_nodes(data: Mapping[str, Any], ecu: Optional[str]) -> Iterable[Mapping[str, Any]]:
    for entity in _walk(data):
        if entity.get("entity_type") != "node" or not entity.get("launcher"):
            continue
        if ecu is not None and entity.get("compute_unit") != ecu:
            continue
        yield entity


def _entity_path(entity: Mapping[str, Any]) -> str:
    return entity.get("path") or node_fqn(entity.get("name", ""), entity.get("namespace"))


def _entity_key(entity: Mapping[str, Any]) -> str:
    """Path that names the node alone: a node placed at a module's own path is named below it."""
    path = _entity_path(entity)
    name = entity.get("name", "")
    return path if path.rstrip("/").rsplit("/", 1)[-1] == name else f"{path.rstrip('/')}/{name}"


def _selects(entity: Mapping[str, Any], pattern: str) -> bool:
    return _under(_entity_path(entity), pattern) or _entity_key(entity) == pattern.rstrip("/")


def _under(path: str, pattern: str) -> bool:
    base = pattern.rstrip("/")
    return path == base or path.startswith(base + "/") or base == ""


def select_scope(data: Mapping[str, Any], patterns: Sequence[str], *, ecu: Optional[str] = None) -> Scope:
    """The nodes at or under each of *patterns*, or named by one; every pattern must select at least one."""
    if not patterns:
        raise ScopeError("no scope given")
    entities = list(_launched_nodes(data, ecu))
    nodes: list[str] = []
    targets: set[str] = set()
    for pattern in patterns:
        matched = [e for e in entities if _selects(e, pattern)]
        if not matched:
            raise ScopeError(f"scope {pattern!r} selects no node of the system")
    for entity in entities:
        path = _entity_path(entity)
        if not any(_selects(entity, pattern) for pattern in patterns):
            continue
        nodes.append(path)
        launcher = entity["launcher"]
        if launcher.get("launch_state") == "composable_node" and launcher.get("container_target"):
            targets.add(launcher["container_target"])
    containers = [
        _entity_path(e)
        for e in entities
        if e["launcher"].get("launch_state") == "node_container"
        and node_fqn(e.get("name", ""), e.get("namespace")) in targets
    ]
    missing = targets - {
        node_fqn(e.get("name", ""), e.get("namespace"))
        for e in entities
        if e["launcher"].get("launch_state") == "node_container"
    }
    if missing:
        raise ScopeError(f"container(s) {sorted(missing)} of the selected composable nodes are not in the system")
    return Scope(patterns=list(patterns), nodes=nodes, containers=containers)


def replay_structure(data: Mapping[str, Any], scope: Scope) -> dict[str, Any]:
    """The system reduced to the scope's members, each on simulated time and without intra-process delivery."""
    keep = set(scope.members)

    def reduce(entity: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        children = [child for child in (reduce(c) for c in entity.get("children", [])) if child is not None]
        is_node = entity.get("entity_type") == "node" and bool(entity.get("launcher"))
        if is_node and _entity_path(entity) not in keep:
            return None
        if not is_node and not children and entity.get("entity_type") != "system":
            return None
        reduced = {key: copy.deepcopy(value) for key, value in entity.items() if key != "children"}
        reduced["children"] = children
        if is_node:
            _to_replay_node(reduced)
        return reduced

    reduced = reduce(data)
    return reduced if reduced is not None else {**{k: v for k, v in data.items() if k != "children"}, "children": []}


def _to_replay_node(entity: dict[str, Any]) -> None:
    launcher = entity["launcher"]
    launcher["use_intra_process_comms"] = False
    # A launch file takes its parameters as launch arguments; its nodes keep the file's own setting.
    if launcher.get("launch_state") in ("single_node", "composable_node"):
        parameters = [p for p in entity.get("parameters", []) if p.get("name") != "use_sim_time"]
        parameters.append({"name": "use_sim_time", "value": True, "type": "bool", "parameter_type": "MODE"})
        entity["parameters"] = parameters


def _service_ports(entity: Mapping[str, Any]) -> Iterable[tuple[str, str, str]]:
    """(resolved name, type, role) of the service and action ports of a node."""
    for side, fallback in (("in_ports", "client"), ("out_ports", "server")):
        for port in entity.get(side, []):
            if _is_topic_port(port):
                continue
            name = resolve_topic(port)
            if name is None:
                continue
            role = port.get("role") if port.get("role") in ("client", "server") else fallback
            yield name, str(port.get("msg_type", "")), role


def compute_boundary(data: Mapping[str, Any], scope: Scope, *, ecu: Optional[str] = None) -> Boundary:
    """Where the scope meets the rest of the system. *ecu* limits the scope only: a publisher on
    another compute unit is still a publisher outside the scope."""
    graph = NodeGraph.from_system_structure({"data": data} if "data" not in data else data)
    inside = set(scope.nodes)
    scoped: list[NodeInfo] = [graph.nodes[path] for path in scope.nodes if path in graph.nodes]

    publishers: dict[str, list[str]] = {}
    subscribers: dict[str, list[str]] = {}
    for node in graph.nodes.values():
        for topic in node.outputs:
            publishers.setdefault(topic, []).append(node.path)
        for topic in node.inputs:
            subscribers.setdefault(topic, []).append(node.path)

    process_topics = special_topics().process
    boundary = Boundary()
    inputs: dict[str, InputTopic] = {}
    outputs: dict[str, OutputTopic] = {}
    for node in scoped:
        for topic, port in node.inputs.items():
            if topic in process_topics:
                continue
            sources = publishers.get(topic, [])
            outside = [p for p in sources if p not in inside]
            if sources and not outside:
                continue  # fed inside the scope
            entry = inputs.setdefault(
                topic,
                InputTopic(
                    topic=topic,
                    msg_type=port.msg_type,
                    subscribers=[],
                    publishers=outside,
                    scope_publishers=[p for p in sources if p in inside],
                ),
            )
            entry.subscribers.append(node.path)
        for topic, port in node.outputs.items():
            if topic in process_topics:
                continue
            readers = subscribers.get(topic, [])
            entry = outputs.setdefault(
                topic,
                OutputTopic(
                    topic=topic,
                    msg_type=port.msg_type,
                    publishers=[],
                    scope_subscribers=[s for s in readers if s in inside],
                    outside_subscribers=[s for s in readers if s not in inside],
                ),
            )
            entry.publishers.append(node.path)
    boundary.inputs = sorted(inputs.values(), key=lambda e: e.topic)
    boundary.outputs = sorted(outputs.values(), key=lambda e: e.topic)

    structure = data.get("data", data)
    ports: dict[str, list[tuple[str, str, str]]] = {}
    for entity in _launched_nodes(structure, None):
        ports[_entity_path(entity)] = list(_service_ports(entity))
    for path in scope.nodes:
        for name, srv_type, role in ports.get(path, []):
            other = "server" if role == "client" else "client"
            peers = [
                p for p in scope.nodes if p != path and any(n == name and r == other for n, _, r in ports.get(p, []))
            ]
            boundary.services.append(ServiceEdge(name=name, srv_type=srv_type, node=path, role=role, scope_peers=peers))
    boundary.services.sort(key=lambda e: (e.name, e.node))
    return boundary


def scope_node_fqns(data: Mapping[str, Any], scope: Scope) -> dict[str, str]:
    """ROS name of each scope member that is one ROS node, by design path; launch files have none."""
    members = set(scope.members)
    names: dict[str, str] = {}
    for entity in _launched_nodes(data.get("data", data), None):
        path = _entity_path(entity)
        if path in members and entity["launcher"].get("launch_state") != "ros2_launch_file":
            names[path] = node_fqn(entity.get("name", ""), entity.get("namespace"))
    return names


def unconnected_ports(data: Mapping[str, Any], scope: Scope) -> dict[str, set[str]]:
    """Topic names the scope's nodes use for ports the design leaves unconnected, by ROS node name."""
    members = set(scope.members)
    found: dict[str, set[str]] = {}
    for entity in _launched_nodes(data.get("data", data), None):
        if _entity_path(entity) not in members:
            continue
        fqn = node_fqn(entity.get("name", ""), entity.get("namespace"))
        namespace = fqn.rsplit("/", 1)[0]
        for port in list(entity.get("in_ports", [])) + list(entity.get("out_ports", [])):
            target = port.get("remap_target")
            if not _is_topic_port(port) or resolve_topic(port) is not None or not isinstance(target, str):
                continue
            if target.startswith("~"):
                name = fqn + target[1:]
            elif target.startswith("/"):
                name = target
            else:
                name = f"{namespace}/{target}"
            found.setdefault(fqn, set()).add(name)
    return found
