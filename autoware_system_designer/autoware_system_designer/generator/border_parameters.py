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

"""Parameters of border nodes: build-time values as parameter files, package paths as find-pkg-share.

A node's parameters keep the launch order of the tiered launcher (default, override, mode; per tier
values expanded from files, files loaded at launch time, direct values). Consecutive build-time
values share one parameter file; values carrying a substitution stay inline, and files loaded at
launch time stay in place, so the last-wins outcome equals the tiered launcher.
"""

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from autoware_system_designer.common.parameter_types import to_launch_param_attr

# Tier order of the launcher: (types of values from files, type of launch-time files, types of direct values).
_TIERS = (
    (("GLOBAL", "DEFAULT_FILE"), "DEFAULT_FILE", ("DEFAULT",)),
    (("OVERRIDE_FILE",), "OVERRIDE_FILE", ("OVERRIDE",)),
    (("MODE_FILE",), "MODE_FILE", ("MODE",)),
)
_PARAM_FILE_SUFFIX = ".param.yaml"


class PackagePaths:
    """Rewrites absolute paths under a package's share or source directory to find-pkg-share.

    A source directory stands for the share directory of its package, so files under it are
    expected to be installed at the same relative path.
    """

    def __init__(self, package_dirs: Iterable[Tuple[str, str]]):
        dirs: Dict[str, str] = {}
        for directory, package in package_dirs:
            if not directory:
                continue
            for variant in {os.path.normpath(directory), os.path.realpath(directory)}:
                dirs.setdefault(variant, package)
        ordered = sorted(dirs, key=len, reverse=True)
        self._packages = dirs
        self._pattern = re.compile("(" + "|".join(re.escape(d) for d in ordered) + r")(?=/|$)") if ordered else None

    def portable(self, value: Any) -> Any:
        if self._pattern is None:
            return value
        if isinstance(value, str):
            return self._pattern.sub(lambda m: f"$(find-pkg-share {self._packages[m.group(1)]})", value)
        if isinstance(value, list):
            return [self.portable(v) for v in value]
        if isinstance(value, dict):
            return {k: self.portable(v) for k, v in value.items()}
        return value


def _typed(value: Any) -> Any:
    """Value as the launch frontend reads a `value` attribute."""
    if not isinstance(value, str):
        return value
    try:
        return yaml.safe_load(to_launch_param_attr(value))
    except yaml.YAMLError:
        return value


def _nested(values: Dict[str, Any]) -> Dict[str, Any]:
    """Dotted names as nested maps; a name that is also a prefix of another stays flat."""
    prefixes = {name.rsplit(".", 1)[0] for name in values if "." in name}
    tree: Dict[str, Any] = {}
    for name, value in values.items():
        parts = name.split(".")
        if name in prefixes or any(".".join(parts[:i]) in values for i in range(1, len(parts))):
            tree[name] = value
            continue
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return tree


def _ordered_items(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parameter values and launch-time files in launcher order."""
    items: List[Dict[str, Any]] = []
    values = node.get("param_values", [])
    files = node.get("launch_time_param_files") or []
    for value_types, file_type, direct_types in _TIERS:
        items.extend(p for p in values if p.get("parameter_type") in value_types)
        items.extend({**f, "is_file": True} for f in files if f.get("parameter_type") == file_type)
        items.extend(p for p in values if p.get("parameter_type") in direct_types)
    return items


def plan_node_parameters(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Launcher entries of a node in order: {"values": {...}} for one parameter file of build-time
    values, {"name", "value"} for an inline value, {"path", "allow_substs"} for a launch-time file."""
    entries: List[Dict[str, Any]] = []
    chunk: Optional[Dict[str, Any]] = None
    inline_after_chunk: set = set()
    for item in _ordered_items(node):
        if item.get("is_file"):
            entries.append({"path": item["path"], "allow_substs": bool(item.get("allow_substs"))})
            chunk = None
            continue
        value = item["value"]
        if "$(" in str(value):
            entries.append({"name": item["name"], "value": value})
            inline_after_chunk.add(item["name"])
            continue
        # An inline value placed after the open file would override a later value of its name.
        if chunk is None or item["name"] in inline_after_chunk:
            chunk = {}
            inline_after_chunk = set()
            entries.append({"values": chunk})
        chunk[item["name"]] = _typed(value)
    return entries


def write_parameter_files(
    nodes: Iterable[Dict[str, Any]], param_dir: str, package: str, param_dir_in_package: str
) -> None:
    """Set each node's `param_entries` to launcher entries referring to the files written here.

    Files land at <param_dir>/<node path>[.<n>].param.yaml and are referenced through
    $(find-pkg-share <package>)/<param_dir_in_package>.
    """
    for node in nodes:
        if node.get("launch_state") in ("ros2_launch_file", "node_container"):
            continue
        entries = plan_node_parameters(node)
        file_count = sum(1 for e in entries if "values" in e)
        index = 0
        rendered: List[Dict[str, Any]] = []
        for entry in entries:
            if "values" not in entry:
                rendered.append(entry)
                continue
            index += 1
            suffix = f".{index}" if file_count > 1 else ""
            relative = node["node_path"].strip("/") + suffix + _PARAM_FILE_SUFFIX
            path = os.path.join(param_dir, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                yaml.safe_dump({"/**": {"ros__parameters": _nested(entry["values"])}}, f, sort_keys=False)
            rendered.append(
                {"path": f"$(find-pkg-share {package})/{param_dir_in_package}/{relative}", "allow_substs": False}
            )
        node["param_entries"] = rendered
