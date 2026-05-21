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

"""Parameter helpers for composable nodes loaded via the LoadNode service.

The ``composition_interfaces/srv/LoadNode`` request takes inline parameters
only — no ``params_files`` field. We must read each parameter YAML, extract
the block(s) that apply to the target FQN (``/**``, exact FQN, namespaced
wildcards like ``/sensing/**``), flatten, and convert to
``rcl_interfaces/Parameter`` messages.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Sequence

import yaml

logger = logging.getLogger(__name__)


def flatten_for_fqn(
    yaml_paths: Iterable[str],
    node_fqn: str,
) -> "dict[str, Any]":
    """Read each YAML and merge the params that apply to *node_fqn*.

    Matching rules (in order, later overrides earlier):

    1. ``/**`` — applies to every node.
    2. Ancestor wildcards: ``/foo/**``, ``/foo/bar/**`` — applies to any
       descendant.
    3. Exact FQN match.

    *node_fqn* must be a full ROS node name (``/ns/node``).
    """
    merged: "dict[str, Any]" = {}
    for path in yaml_paths:
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning("param file not found: %s", path)
            continue
        except yaml.YAMLError as e:
            logger.warning("param file %s parse error: %s", path, e)
            continue

        if not isinstance(doc, Mapping):
            continue

        for key in _match_order(node_fqn, doc.keys()):
            section = doc.get(key, {})
            if not isinstance(section, Mapping):
                continue
            ros_params = section.get("ros__parameters", {})
            if not isinstance(ros_params, Mapping):
                continue
            for k, v in _walk(ros_params, prefix=""):
                merged[k] = v
    return merged


def _match_order(node_fqn: str, candidate_keys) -> List[str]:
    """Return the ordered list of candidate_keys that match *node_fqn*.

    Order is from least specific (``/**``) to most specific (exact match),
    so later writes override earlier ones when merged.
    """
    keys = list(candidate_keys)
    matches: List[tuple] = []  # (specificity, key)

    for key in keys:
        if key == "/**":
            matches.append((0, key))
            continue
        if key == node_fqn:
            matches.append((3, key))
            continue
        if key.endswith("/**"):
            # Wildcard at any namespace depth.
            base = key[:-3] or "/"
            if base == "/" or node_fqn.startswith(base.rstrip("/") + "/"):
                # Depth of the namespace = specificity (more slashes = more specific).
                matches.append((1 + base.count("/"), key))

    matches.sort(key=lambda t: t[0])
    return [k for _, k in matches]


def _walk(node, prefix: str):
    """Yield (dotted_key, leaf_value) pairs from a nested dict.

    ROS 2 parameter YAML uses nested mappings as a namespace shorthand:

    .. code-block:: yaml

        ros__parameters:
          mygroup:
            mykey: 5

    becomes ``"mygroup.mykey": 5``. Sequences are leaf values.
    """
    if isinstance(node, Mapping):
        for key, val in node.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk(val, child_prefix)
    else:
        yield prefix, node


# ---- Conversion to rcl_interfaces.msg.Parameter --------------------------


def to_parameter_msgs(values: "Mapping[str, Any]") -> "list":
    """Convert a flat dict to ``rcl_interfaces/Parameter[]``.

    Import is local so this module stays importable without ROS during
    unit testing of the YAML-flattening logic alone.
    """
    from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue

    out = []
    for name, value in values.items():
        param = Parameter()
        param.name = name
        param.value = _to_parameter_value(value, ParameterValue, ParameterType)
        out.append(param)
    return out


def _to_parameter_value(value, ParameterValue, ParameterType):
    pv = ParameterValue()
    if value is None:
        pv.type = ParameterType.PARAMETER_NOT_SET
        return pv
    if isinstance(value, bool):
        pv.type = ParameterType.PARAMETER_BOOL
        pv.bool_value = bool(value)
        return pv
    if isinstance(value, int):
        pv.type = ParameterType.PARAMETER_INTEGER
        pv.integer_value = int(value)
        return pv
    if isinstance(value, float):
        pv.type = ParameterType.PARAMETER_DOUBLE
        pv.double_value = float(value)
        return pv
    if isinstance(value, str):
        pv.type = ParameterType.PARAMETER_STRING
        pv.string_value = value
        return pv
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return _seq_to_param_value(list(value), pv, ParameterType)
    # Fallback: stringify
    pv.type = ParameterType.PARAMETER_STRING
    pv.string_value = str(value)
    return pv


def _seq_to_param_value(items: list, pv, ParameterType):
    if not items:
        # ROS 2 requires us to commit to a type; default to string array.
        pv.type = ParameterType.PARAMETER_STRING_ARRAY
        pv.string_array_value = []
        return pv

    if all(isinstance(x, bool) for x in items):
        pv.type = ParameterType.PARAMETER_BOOL_ARRAY
        pv.bool_array_value = [bool(x) for x in items]
    elif all(isinstance(x, int) and not isinstance(x, bool) for x in items):
        pv.type = ParameterType.PARAMETER_INTEGER_ARRAY
        pv.integer_array_value = [int(x) for x in items]
    elif all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in items):
        pv.type = ParameterType.PARAMETER_DOUBLE_ARRAY
        pv.double_array_value = [float(x) for x in items]
    elif all(isinstance(x, str) for x in items):
        pv.type = ParameterType.PARAMETER_STRING_ARRAY
        pv.string_array_value = list(items)
    else:
        pv.type = ParameterType.PARAMETER_STRING_ARRAY
        pv.string_array_value = [str(x) for x in items]
    return pv
