"""Recursive namespace tree builder for auto-system-config-generator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .launch_parser import ContainerRecord, NodeRecord


def _ns_to_name(namespace: str) -> str:
    parts = [p for p in namespace.strip("/").split("/") if p]
    return parts[-1] if parts else "root"


def _name_to_pascal(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


@dataclass
class NamespaceNode:
    namespace: str                              # e.g. "/perception/object_recognition"
    name: str                                   # snake_case leaf e.g. "object_recognition"
    entity_name: str                            # PascalCase e.g. "ObjectRecognition"
    direct_nodes: list[NodeRecord] = field(default_factory=list)
    children: dict[str, "NamespaceNode"] = field(default_factory=dict)
    containers: list[ContainerRecord] = field(default_factory=list)

    @property
    def all_nodes(self) -> list[NodeRecord]:
        """Recursively collect all nodes in this subtree."""
        result = list(self.direct_nodes)
        for child in self.children.values():
            result.extend(child.all_nodes)
        return result

    @property
    def all_containers(self) -> list[ContainerRecord]:
        result = list(self.containers)
        for child in self.children.values():
            result.extend(child.all_containers)
        return result

    def nodes_at_depth(self, depth: int) -> list["NamespaceNode"]:
        """Return all NamespaceNodes at the given depth below this node (0 = self)."""
        if depth == 0:
            return [self]
        result = []
        for child in self.children.values():
            result.extend(child.nodes_at_depth(depth - 1))
        return result


def build_namespace_tree(
    nodes: list[NodeRecord],
    containers: list[ContainerRecord],
    overrides: Optional[dict] = None,
    top_depth: int = 1,
) -> dict[str, "NamespaceNode"]:
    """Build a recursive namespace tree from flat node/container lists.

    Returns top-level namespace → NamespaceNode (at depth `top_depth`).

    Each node is placed at its parent namespace. E.g. a node at
    /perception/object_recognition/detection/euclidean_cluster_node
    has namespace /perception/object_recognition/detection and is a
    direct_node of the NamespaceNode for that namespace.
    """
    overrides = overrides or {}
    # namespace string → NamespaceNode
    ns_map: dict[str, NamespaceNode] = {}

    def get_or_create(ns: str) -> NamespaceNode:
        if ns in ns_map:
            return ns_map[ns]
        raw_name = _ns_to_name(ns)
        if ns in overrides:
            name = overrides[ns].get("name", raw_name)
            entity = overrides[ns].get("entity_name", _name_to_pascal(raw_name))
        else:
            name = raw_name
            entity = _name_to_pascal(raw_name)
        node = NamespaceNode(namespace=ns, name=name, entity_name=entity)
        ns_map[ns] = node
        # Link to parent
        parent_ns = _parent_ns(ns)
        if parent_ns:
            parent = get_or_create(parent_ns)
            child_key = name
            parent.children[child_key] = node
        return node

    def _parent_ns(ns: str) -> Optional[str]:
        parts = [p for p in ns.strip("/").split("/") if p]
        if len(parts) <= 1:
            return None
        return "/" + "/".join(parts[:-1])

    for node in nodes:
        ns = node.namespace.rstrip("/") or "/"
        ns_node = get_or_create(ns)
        ns_node.direct_nodes.append(node)

    for container in containers:
        ns = container.namespace.rstrip("/") or "/"
        ns_node = get_or_create(ns)
        ns_node.containers.append(container)

    # Move nodes whose full_path matches a child namespace into that namespace to avoid duplicate instances.
    for ns_node in list(ns_map.values()):
        to_move = [n for n in ns_node.direct_nodes if n.full_path in ns_map]
        for node in to_move:
            ns_node.direct_nodes.remove(node)
            ns_map[node.full_path].direct_nodes.append(node)

    # Collect top-level nodes: namespaces at the given depth from root
    top: dict[str, NamespaceNode] = {}
    for ns, ns_node in ns_map.items():
        parts = [p for p in ns.strip("/").split("/") if p]
        if len(parts) == top_depth:
            top[ns] = ns_node

    return dict(sorted(top.items()))
