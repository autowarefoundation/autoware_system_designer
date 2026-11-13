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

"""Parameter resolution and ROS --ros-args formatting shared across all launch types."""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from .namespace import parent_namespace

logger = logging.getLogger(__name__)

# Matches ROS 2 launch $(command '<cmd>' ['<fallback>']) substitution.
_COMMAND_SUB = re.compile(r"^\$\(command\s+'(.+?)'(?:\s+'([^']*)')?\s*\)$", re.DOTALL)

_VALID_PARAM_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_./]*$")


def resolve_value(value: Any, type_hint: Optional[str] = None) -> Any:
    """Coerce a JSON param value to a Python value using *type_hint*.

    ``$(command '<cmd>' ...)`` substitutions are resolved by running the
    command, split into arguments without a shell, and using its stdout.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, list)):
        return value

    s = str(value)

    m = _COMMAND_SUB.match(s.strip())
    if m:
        cmd_str = m.group(1)
        fallback = m.group(2) if m.group(2) is not None else ""
        try:
            argv = shlex.split(cmd_str)
            if not argv:
                return fallback
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return result.stdout.strip()
            logger.warning(
                "command substitution failed (rc=%d): %s\n%s",
                result.returncode,
                cmd_str,
                result.stderr.strip(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("command substitution error: %s: %s", cmd_str, exc)
        return fallback

    hint = (type_hint or "").strip().lower()

    # An empty value carries no truth: it falls through rather than reading as False.
    if hint == "bool" and s != "":
        return s.lower() not in ("false", "0", "no", "off")
    if hint == "int":
        try:
            return int(s)
        except ValueError:
            pass
    if hint == "double":
        try:
            return float(s)
        except ValueError:
            pass

    import yaml

    try:
        parsed = yaml.safe_load(s)
        if isinstance(parsed, (bool, int, float, list)):
            return parsed
    except Exception:  # noqa: BLE001
        pass
    return s


def params_dict(params: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    return {p["name"]: resolve_value(p["value"], p.get("type")) for p in params}


# A parameter file path, or inline values.
ParameterLayer = Union[str, dict[str, Any]]

# Priority tiers of the exported XML launchers: (value types expanded from files, file type, direct value types).
_PARAMETER_TIERS = (
    (("GLOBAL", "DEFAULT_FILE"), "DEFAULT_FILE", ("DEFAULT",)),
    (("OVERRIDE_FILE",), "OVERRIDE_FILE", ("OVERRIDE",)),
    (("MODE_FILE",), "MODE_FILE", ("MODE",)),
)


def parameter_layers(node_spec: Mapping[str, Any], base_files: Sequence[str] = ()) -> list[ParameterLayer]:
    """Parameter sources of a node, lowest priority first; a later layer wins.

    Base files precede every tier. Each tier lists the values expanded from its files, its
    files, then its direct values. Default files are expanded at build time, except those whose
    path keeps a launch-time substitution, which the launcher lists to load as files.
    """
    params = node_spec.get("parameters", [])
    launch_time_defaults = [
        f["path"]
        for f in node_spec.get("launcher", {}).get("launch_time_param_files", [])
        if f.get("parameter_type") == "DEFAULT_FILE"
    ]

    def values(types: Sequence[str]) -> dict[str, Any]:
        return params_dict(p for p in params if p.get("parameter_type", "DEFAULT") in types)

    layers: list[ParameterLayer] = list(base_files)
    for file_value_types, file_type, value_types in _PARAMETER_TIERS:
        layers.append(values(file_value_types))
        if file_type == "DEFAULT_FILE":
            layers.extend(launch_time_defaults)
        else:
            layers.extend(
                f["path"] for f in node_spec.get("parameter_files_all", []) if f.get("parameter_type") == file_type
            )
        layers.append(values(value_types))
    return [layer for layer in layers if layer]


def remap_pairs(ports: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    return [(p["remap_target"], p["topic"]) for p in ports if p.get("remap_target") and p.get("topic")]


def _ros_arg_for_param(name: str, value: Any) -> list[str]:
    """Render ``-p name:=value`` in a yaml.safe_dump-typed form ROS 2 will accept."""
    import yaml

    if not _VALID_PARAM_KEY.match(name):
        logger.warning("skipping unsafe param name %r", name)
        return []
    # An empty sequence carries no element type, so rcl rejects it; the name is
    # left unset and the node's declared default applies.
    if isinstance(value, (list, tuple)) and not value:
        logger.debug("param %r resolves to an empty list; omitted so the node default applies", name)
        return []
    encoded = yaml.safe_dump(value, default_flow_style=True).strip()
    return ["-p", f"{name}:={encoded}"]


def _ros_args(
    *,
    name: str,
    namespace: Any,
    parameters: Sequence[ParameterLayer],
    remaps: Sequence[tuple[str, str]],
) -> list[str]:
    args: list[str] = ["--ros-args"]
    ns = parent_namespace(namespace, name)
    if ns and ns != "/":
        args += ["-r", f"__ns:={ns}"]
    if name:
        args += ["-r", f"__node:={name}"]
    # rcl applies --params-file and -p in command-line order, last wins.
    for layer in parameters:
        if isinstance(layer, str):
            args += ["--params-file", layer]
        else:
            for k, v in layer.items():
                args += _ros_arg_for_param(k, v)
    for src, dst in remaps:
        args += ["-r", f"{src}:={dst}"]
    return args


def _ros2_executable_path(package: str, executable: str) -> Optional[str]:
    """Return the direct binary path for a ROS 2 executable, or None to fall back to ros2 run.

    Direct spawn delivers the stop signal to rclcpp, not to an intermediate Python wrapper.
    """
    try:
        from ros2run.api import get_executable_path

        path = get_executable_path(package_name=package, executable_name=executable)
        if path:
            return path
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_executable_path(%r, %r) failed: %s", package, executable, exc)
    return None


def build_cmd(launcher: Mapping[str, str]) -> list[str]:
    """Return [binary_path] or ['ros2', 'run', pkg, exe] for a launcher spec."""
    exec_path = _ros2_executable_path(launcher["package"], launcher["executable"])
    if exec_path:
        return [exec_path]
    logger.warning(
        "could not resolve executable path for %s/%s, falling back to ros2 run",
        launcher["package"],
        launcher["executable"],
    )
    return ["ros2", "run", launcher["package"], launcher["executable"]]
