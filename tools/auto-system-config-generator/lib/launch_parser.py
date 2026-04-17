"""Parse a generated.launch.xml file (output of launch_unifier) into structured records."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree


@dataclass
class RemapEntry:
    from_topic: str  # e.g. ~/input/objects or input
    to_topic: str    # e.g. /perception/object_recognition/objects

    @property
    def direction(self) -> str:
        """Return 'input', 'output', or 'unknown'."""
        if self.from_topic.startswith("~/input/"):
            return "input"
        if self.from_topic.startswith("~/output/"):
            return "output"
        if self.from_topic == "input":
            return "input"
        if self.from_topic == "output":
            return "output"
        return "unknown"

    def port_name(self, node_namespace: str = "") -> Optional[str]:
        """Port name derived from the from_topic pattern.

        For the standard ~/input/xxx and ~/output/xxx convention the port name
        is the suffix after the prefix.  For bare 'input'/'output' remaps the
        port name is derived from the resolved topic so the name is meaningful.
        """
        if self.from_topic.startswith("~/input/"):
            return self.from_topic[len("~/input/"):]
        if self.from_topic.startswith("~/output/"):
            return self.from_topic[len("~/output/"):]
        if self.from_topic in ("input", "output"):
            # Derive a readable name from the resolved topic path.
            # Strip the node namespace prefix so the port reflects the topic's
            # semantic segment rather than its full absolute path.
            topic = self.to_topic
            ns = node_namespace.rstrip("/")
            if ns and topic.startswith(ns + "/"):
                topic = topic[len(ns) + 1:]
            topic = topic.lstrip("/")
            return topic if topic else self.from_topic
        return None


@dataclass
class NodeRecord:
    name: str
    namespace: str
    pkg: str
    exec: Optional[str] = None
    plugin: Optional[str] = None
    is_composable: bool = False
    container: Optional[str] = None  # full path of the container this runs in
    remaps: list[RemapEntry] = field(default_factory=list)
    param_files: list[str] = field(default_factory=list)

    @property
    def full_path(self) -> str:
        ns = self.namespace.rstrip("/")
        return f"{ns}/{self.name}" if ns else f"/{self.name}"

    @property
    def top_namespace(self) -> str:
        parts = [p for p in self.namespace.strip("/").split("/") if p]
        return "/" + parts[0] if parts else "/"

    @property
    def instance_name(self) -> str:
        """snake_case name suitable for use as an instance identifier."""
        return self.name.replace("-", "_")


@dataclass
class ContainerRecord:
    name: str
    namespace: str
    exec_type: str  # component_container_mt or component_container

    @property
    def full_path(self) -> str:
        ns = self.namespace.rstrip("/")
        return f"{ns}/{self.name}" if ns else f"/{self.name}"

    @property
    def group_type(self) -> str:
        if "mt" in self.exec_type:
            return "ros2_component_container_mt"
        return "ros2_component_container"

    @property
    def top_namespace(self) -> str:
        parts = [p for p in self.namespace.strip("/").split("/") if p]
        return "/" + parts[0] if parts else "/"


def _parse_remaps_and_params(elem) -> tuple[list[RemapEntry], list[str]]:
    remaps = []
    for r in elem.findall("remap"):
        from_t = r.get("from", "")
        to_t = r.get("to", "")
        if from_t and to_t:
            remaps.append(RemapEntry(from_topic=from_t, to_topic=to_t))

    param_files = []
    for p in elem.findall("param"):
        src = p.get("from")
        if src:
            param_files.append(src)

    return remaps, param_files


def parse_launch_xml(path: str | Path) -> tuple[list[NodeRecord], list[ContainerRecord]]:
    """Parse a generated.launch.xml file from launch_unifier.

    Returns (nodes, containers) where nodes is a flat list of all ROS 2 nodes
    (both standalone and composable) and containers is the list of component
    container descriptors.
    """
    xml_parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(path), xml_parser)
    root = tree.getroot()

    containers: list[ContainerRecord] = []
    nodes: list[NodeRecord] = []

    # --- containers ---
    for elem in root.findall(".//node_container"):
        containers.append(
            ContainerRecord(
                name=elem.get("name", ""),
                namespace=elem.get("namespace", ""),
                exec_type=elem.get("exec", "component_container"),
            )
        )

    # --- composable nodes (inside load_composable_node) ---
    for load_elem in root.findall(".//load_composable_node"):
        raw_target = load_elem.get("target", None)
        # Normalize relative container targets (e.g. "pointcloud_container" → "/pointcloud_container")
        if raw_target and not raw_target.startswith("/"):
            raw_target = "/" + raw_target
        container_target = raw_target
        for elem in load_elem.findall("composable_node"):
            remaps, param_files = _parse_remaps_and_params(elem)
            nodes.append(
                NodeRecord(
                    name=elem.get("name", ""),
                    namespace=elem.get("namespace", ""),
                    pkg=elem.get("pkg", ""),
                    plugin=elem.get("plugin"),
                    is_composable=True,
                    container=container_target,
                    remaps=remaps,
                    param_files=param_files,
                )
            )

    # --- standalone nodes ---
    for elem in root.findall("node"):
        remaps, param_files = _parse_remaps_and_params(elem)
        nodes.append(
            NodeRecord(
                name=elem.get("name", ""),
                namespace=elem.get("namespace", ""),
                pkg=elem.get("pkg", ""),
                exec=elem.get("exec"),
                is_composable=False,
                remaps=remaps,
                param_files=param_files,
            )
        )

    return nodes, containers
