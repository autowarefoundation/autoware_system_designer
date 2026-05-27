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
import subprocess
from typing import Any, Iterable, Mapping, Optional, Sequence

from .namespace import parent_namespace

logger = logging.getLogger(__name__)

# Matches ROS 2 launch $(command '<shell-cmd>' ['<fallback>']) substitution.
_COMMAND_SUB = re.compile(r"^\$\(command\s+'(.+?)'(?:\s+'([^']*)')?\s*\)$", re.DOTALL)

_VALID_PARAM_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_./]*$")


def resolve_value(value: Any, type_hint: Optional[str] = None) -> Any:
    """Coerce a JSON param value to a Python value using *type_hint*.

    ``$(command '<cmd>' ...)`` substitutions are resolved by running the shell
    command and using its stdout.
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
            result = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=30)
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

    if hint == "bool":
        return s.lower() not in ("false", "0", "no", "off", "")
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


def parameter_files(node_spec: Mapping[str, Any]) -> list[str]:
    return [
        f["path"]
        for f in node_spec.get("parameter_files_all", [])
        if f.get("parameter_type") != "DEFAULT_FILE"
    ]


def remap_pairs(ports: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    return [(p["remap_target"], p["topic"]) for p in ports if p.get("remap_target") and p.get("topic")]


def _ros_arg_for_param(name: str, value: Any) -> list[str]:
    """Render ``-p name:=value`` in a yaml.safe_dump-typed form ROS 2 will accept."""
    import yaml

    if not _VALID_PARAM_KEY.match(name):
        logger.warning("skipping unsafe param name %r", name)
        return []
    encoded = yaml.safe_dump(value, default_flow_style=True).strip()
    return ["-p", f"{name}:={encoded}"]


def _ros_args(
    *,
    name: str,
    namespace: Any,
    inline_params: Mapping[str, Any],
    param_files: Sequence[str],
    remaps: Sequence[tuple[str, str]],
) -> list[str]:
    args: list[str] = ["--ros-args"]
    ns = parent_namespace(namespace, name)
    if ns and ns != "/":
        args += ["-r", f"__ns:={ns}"]
    if name:
        args += ["-r", f"__node:={name}"]
    for k, v in inline_params.items():
        args += _ros_arg_for_param(k, v)
    for f in param_files:
        args += ["--params-file", f]
    for src, dst in remaps:
        args += ["-r", f"{src}:={dst}"]
    return args
