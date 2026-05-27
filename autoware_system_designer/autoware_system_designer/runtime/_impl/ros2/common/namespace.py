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

"""ROS namespace helpers shared across all launch types."""

from __future__ import annotations

from typing import Any, Mapping, Optional


def _ns_segments(ns: Any) -> list[str]:
    if ns is None:
        return []
    if isinstance(ns, str):
        return [s for s in ns.split("/") if s]
    if isinstance(ns, (list, tuple)):
        return [str(p).strip("/") for p in ns if p]
    return []


def parent_namespace(ns: Any, name: Optional[str] = None) -> str:
    """Return parent ROS namespace, stripping the trailing segment when it equals *name*."""
    segs = _ns_segments(ns)
    if name and segs and segs[-1] == name:
        segs = segs[:-1]
    return "/" + "/".join(segs) if segs else "/"


def join_fqn(namespace: str, node_name: str) -> str:
    """Join a pre-resolved parent namespace string with a node name into a leading-slash FQN."""
    ns = namespace if namespace.startswith("/") else "/" + namespace
    ns = ns.rstrip("/")
    if not node_name:
        return ns or "/"
    if node_name.startswith("/"):
        return node_name
    return f"{ns}/{node_name}" if ns else f"/{node_name}"


def node_fqn(name: str, namespace: Any) -> str:
    """Canonical ROS FQN: ``<parent_namespace>/<name>``."""
    return join_fqn(parent_namespace(namespace, name), name)


def unique_node_name(node: "Mapping[str, Any]") -> str:
    """Return a unique key for a node spec: structural path, or FQN#launch_state as fallback."""
    path = node.get("path")
    if path:
        return str(path)
    fqn = node_fqn(node.get("name", "_unnamed_"), node.get("namespace"))
    state = node.get("launcher", {}).get("launch_state", "")
    return f"{fqn}#{state}" if state else fqn
