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

"""The design read as a node graph: nodes, resolved topics, inter-node links, declared events.

Ports and the topic links between nodes are trusted; declared process events are
kept as the claim the observed triggers are diffed against.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from ..ros2.common.namespace import node_fqn

logger = logging.getLogger(__name__)

# Topics every rcl process carries that no design declares.
INFRA_TOPICS = frozenset({"/rosout", "/parameter_events"})


@dataclass
class PortInfo:
    name: str
    topic: str
    msg_type: str
    event_id: Optional[str]
    # Process events on the other side of this port's event: producers of an
    # output, consumers of an input.
    process_ids: list[str] = field(default_factory=list)


@dataclass
class DeclaredEvent:
    id: str
    name: str
    type: Optional[str]
    frequency: Optional[float]
    trigger_ids: list[str]
    action_ids: list[str]


@dataclass
class DeclaredTrigger:
    """What the design says fires an output: a clock, inputs through a gate, or nothing."""

    kind: str  # periodic | input | none
    rate_hz: Optional[float] = None
    topics: list[str] = field(default_factory=list)
    gate: Optional[str] = None
    processes: list[str] = field(default_factory=list)
    # A clock upstream of the producing process, reached through other processes.
    upstream_rate_hz: Optional[float] = None

    def label(self) -> str:
        if self.kind == "periodic":
            return f"periodic {_fmt_rate(self.rate_hz)}"
        if self.kind == "input":
            gate = f" {self.gate}" if self.gate in ("and", "or") else ""
            topics = ", ".join(self.topics) if self.topics else "?"
            text = f"input({topics}){gate}"
            if self.upstream_rate_hz:
                text += f" <- periodic {_fmt_rate(self.upstream_rate_hz)}"
            return text
        return "none"


def _fmt_rate(rate: Optional[float]) -> str:
    if rate is None:
        return "? Hz"
    return f"{rate:g} Hz"


@dataclass
class NodeInfo:
    path: str
    fqn: str
    launch_state: str
    # Processes sharing one address space share this key.
    process_key: str
    intra_process: bool
    inputs: dict[str, PortInfo] = field(default_factory=dict)
    outputs: dict[str, PortInfo] = field(default_factory=dict)
    events: dict[str, DeclaredEvent] = field(default_factory=dict)

    @property
    def input_events(self) -> dict[str, PortInfo]:
        return {p.event_id: p for p in self.inputs.values() if p.event_id}

    @property
    def output_events(self) -> dict[str, PortInfo]:
        return {p.event_id: p for p in self.outputs.values() if p.event_id}


@dataclass
class LinkInfo:
    topic: str
    publisher: str
    subscriber: str
    intra_process: bool


def resolve_topic(port: Mapping[str, Any]) -> Optional[str]:
    topic = port.get("topic")
    if isinstance(topic, list):
        parts = [str(p).strip("/") for p in topic if p]
        return "/" + "/".join(parts) if parts else None
    if isinstance(topic, str) and topic:
        return topic if topic.startswith("/") else "/" + topic
    return None


def _is_topic_port(port: Mapping[str, Any]) -> bool:
    return "/srv/" not in str(port.get("msg_type", "")) and "/action/" not in str(port.get("msg_type", ""))


class NodeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, NodeInfo] = {}
        self.by_fqn: dict[str, NodeInfo] = {}
        self.links: list[LinkInfo] = []
        self.mode: Optional[str] = None
        self.system_name: Optional[str] = None
        self.source_file: Optional[str] = None
        self._link_index: dict[tuple[str, str, str], LinkInfo] = {}

    # ---- construction ----------------------------------------------------

    @classmethod
    def from_system_structure(cls, payload: Mapping[str, Any], *, ecu: Optional[str] = None) -> "NodeGraph":
        graph = cls()
        data = payload.get("data", payload)
        meta = payload.get("metadata", {}) if "data" in payload else {}
        graph.mode = meta.get("mode")
        graph.system_name = meta.get("system_name")
        graph.source_file = data.get("source_file")
        for entity in _walk(data):
            if entity.get("entity_type") != "node" or not entity.get("launcher"):
                continue
            if ecu is not None and entity.get("compute_unit") != ecu:
                continue
            graph._add_node(entity)
        graph._build_links()
        return graph

    def _add_node(self, entity: Mapping[str, Any]) -> None:
        launcher = entity["launcher"]
        name = entity.get("name", "")
        fqn = node_fqn(name, entity.get("namespace"))
        path = entity.get("path") or fqn
        state = launcher.get("launch_state", "")
        if state == "composable_node":
            process_key = launcher.get("container_target") or fqn
        else:
            process_key = fqn
        node = NodeInfo(
            path=path,
            fqn=fqn,
            launch_state=state,
            process_key=process_key,
            intra_process=bool(launcher.get("use_intra_process_comms")),
        )
        for event in entity.get("events", []):
            if not event.get("unique_id"):
                continue
            node.events[str(event["unique_id"])] = DeclaredEvent(
                id=str(event["unique_id"]),
                name=event.get("name", ""),
                type=event.get("type"),
                frequency=event.get("frequency"),
                trigger_ids=[str(i) for i in event.get("trigger_ids", [])],
                action_ids=[str(i) for i in event.get("action_ids", [])],
            )
        # An input event's trigger_ids point at upstream outputs; an output event's
        # at the producing processes. Consumers of an input are found from the
        # process side.
        for port in entity.get("in_ports", []):
            info = self._port_info(port, producers=False)
            if info is not None:
                node.inputs.setdefault(info.topic, info)
        for port in entity.get("out_ports", []):
            info = self._port_info(port, producers=True)
            if info is not None:
                node.outputs.setdefault(info.topic, info)
        for port in node.outputs.values():
            producers = set(i for i in port.process_ids if i in node.events)
            for event in node.events.values():
                if port.event_id in event.action_ids:
                    producers.add(event.id)
            port.process_ids = sorted(producers)
        for port in node.inputs.values():
            consumers = set(port.process_ids)
            for event in node.events.values():
                if port.event_id in event.trigger_ids:
                    consumers.add(event.id)
            port.process_ids = sorted(consumers)

        if path in self.nodes:
            logger.warning("duplicate node path %s; keeping the first", path)
            return
        self.nodes[path] = node
        if fqn in self.by_fqn and self.by_fqn[fqn].path != path:
            logger.warning("nodes %s and %s share the ROS name %s", self.by_fqn[fqn].path, path, fqn)
        else:
            self.by_fqn[fqn] = node

    @staticmethod
    def _port_info(port: Mapping[str, Any], *, producers: bool) -> Optional[PortInfo]:
        if not _is_topic_port(port):
            return None
        topic = resolve_topic(port)
        if topic is None:
            return None
        event = port.get("event") or {}
        return PortInfo(
            name=port.get("name", ""),
            topic=topic,
            msg_type=str(port.get("msg_type", "")),
            event_id=str(event["unique_id"]) if event.get("unique_id") else None,
            process_ids=[str(i) for i in event.get("trigger_ids", [])] if producers else [],
        )

    def _build_links(self) -> None:
        publishers: dict[str, list[NodeInfo]] = {}
        for node in self.nodes.values():
            for topic in node.outputs:
                publishers.setdefault(topic, []).append(node)
        for sub in self.nodes.values():
            for topic in sub.inputs:
                for pub in publishers.get(topic, []):
                    if pub.path == sub.path:
                        continue
                    link = LinkInfo(
                        topic=topic,
                        publisher=pub.path,
                        subscriber=sub.path,
                        intra_process=self._same_process_intra(pub, sub),
                    )
                    self.links.append(link)
                    self._link_index[(topic, pub.path, sub.path)] = link

    @staticmethod
    def _same_process_intra(pub: NodeInfo, sub: NodeInfo) -> bool:
        return pub.process_key == sub.process_key and pub.intra_process and sub.intra_process

    # ---- queries -----------------------------------------------------------

    def node_for_fqn(self, fqn: str) -> Optional[NodeInfo]:
        return self.by_fqn.get(fqn)

    def link(self, topic: str, publisher: str, subscriber: str) -> Optional[LinkInfo]:
        return self._link_index.get((topic, publisher, subscriber))

    def is_intra_link(self, topic: str, publisher: str, subscriber: str) -> bool:
        link = self.link(topic, publisher, subscriber)
        return bool(link and link.intra_process)

    def intra_inputs(self, node: NodeInfo) -> set[str]:
        """Input topics that reach *node* without a DDS hop from at least one publisher."""
        return {link.topic for link in self.links if link.subscriber == node.path and link.intra_process}

    def probe_topics(self) -> list[tuple[str, str]]:
        """Topics whose every design reader is intra-process, as (topic, msg_type)."""
        out: dict[str, str] = {}
        for node in self.nodes.values():
            if not node.intra_process:
                continue
            for topic, port in node.outputs.items():
                if topic in INFRA_TOPICS:
                    continue
                readers = [link for link in self.links if link.topic == topic and link.publisher == node.path]
                if all(link.intra_process for link in readers):
                    out.setdefault(topic, port.msg_type)
        return sorted(out.items())

    def declared_trigger(self, node: NodeInfo, topic: str) -> DeclaredTrigger:
        port = node.outputs.get(topic)
        if port is None or not port.process_ids:
            return DeclaredTrigger(kind="none")
        producers = [node.events[i] for i in port.process_ids if i in node.events]
        if not producers:
            return DeclaredTrigger(kind="none")
        periodic = [p for p in producers if p.type == "periodic"]
        if periodic:
            rate = next((p.frequency for p in periodic if p.frequency), None)
            return DeclaredTrigger(kind="periodic", rate_hz=rate, processes=[p.name for p in periodic])
        topics: list[str] = []
        upstream_rate: Optional[float] = None
        gate = producers[0].type if len(producers) == 1 else None
        seen: set[str] = set()
        stack = list(producers)
        depth = 0
        while stack and depth < 4:
            depth += 1
            next_stack: list[DeclaredEvent] = []
            for process in stack:
                if process.id in seen:
                    continue
                seen.add(process.id)
                for trigger_id in process.trigger_ids:
                    input_port = node.input_events.get(trigger_id)
                    if input_port is not None:
                        if input_port.topic not in topics:
                            topics.append(input_port.topic)
                        continue
                    upstream = node.events.get(trigger_id)
                    if upstream is None:
                        continue
                    if upstream.type == "periodic":
                        upstream_rate = upstream_rate or upstream.frequency
                    else:
                        next_stack.append(upstream)
            stack = next_stack
        rate = next((p.frequency for p in producers if p.frequency), None)
        return DeclaredTrigger(
            kind="input",
            rate_hz=rate,
            topics=topics,
            gate=gate,
            processes=[p.name for p in producers],
            upstream_rate_hz=upstream_rate,
        )

    def declared_consumers(self, node: NodeInfo, topic: str) -> list[str]:
        port = node.inputs.get(topic)
        if port is None:
            return []
        return [node.events[i].name for i in port.process_ids if i in node.events]


def _walk(entity: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield entity
    for child in entity.get("children", []):
        yield from _walk(child)
