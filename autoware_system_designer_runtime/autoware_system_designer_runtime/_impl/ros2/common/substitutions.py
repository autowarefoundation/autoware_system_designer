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

"""Launch-style substitutions left in a system_structure export.

The exporter resolves system variables at design time; system *arguments*
stay as ``$(var name)`` tokens and are bound per deploy variant at launch
time, the same way the generated deployment wrapper launch files bind them.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_VAR_SUB = re.compile(r"\$\(var\s+([^)\s]+)\s*\)")
_ENV_SUB = re.compile(r"\$\(env\s+([^)\s]+)(?:\s+([^)]*?))?\s*\)")
_PKG_SHARE_SUB = re.compile(r"\$\(find-pkg-share\s+([^)\s]+)\s*\)")

_MAX_PASSES = 10


def _package_share(name: str) -> str | None:
    try:
        from ament_index_python.packages import get_package_share_directory

        return get_package_share_directory(name)
    except Exception:  # noqa: BLE001
        return None


def resolve_substitutions(text: str, variables: Mapping[str, str]) -> str:
    """Expand ``$(var)``, ``$(env)`` and ``$(find-pkg-share)`` in *text*.

    Passes repeat until the text is stable so nested forms such as
    ``$(find-pkg-share $(var pkg))`` resolve inner-first. Tokens that cannot
    be resolved are left in place.
    """
    if not isinstance(text, str) or "$(" not in text:
        return text

    def _var(m: re.Match) -> str:
        value = variables.get(m.group(1))
        return m.group(0) if value is None else str(value)

    def _env(m: re.Match) -> str:
        value = os.environ.get(m.group(1))
        if value is not None:
            return value
        if m.group(2) is not None:
            return m.group(2)
        logger.warning("environment variable not set: %s", m.group(0))
        return m.group(0)

    def _pkg_share(m: re.Match) -> str:
        path = _package_share(m.group(1))
        if path is None:
            logger.warning("package not found: %s", m.group(0))
            return m.group(0)
        return path

    result = text
    for _ in range(_MAX_PASSES):
        before = result
        result = _ENV_SUB.sub(_env, result)
        result = _VAR_SUB.sub(_var, result)
        result = _PKG_SHARE_SUB.sub(_pkg_share, result)
        if result == before:
            break
    return result


def resolve_in_structure(value: Any, variables: Mapping[str, str]) -> Any:
    """Apply :func:`resolve_substitutions` to every string in a JSON payload."""
    if isinstance(value, str):
        return resolve_substitutions(value, variables)
    if isinstance(value, dict):
        return {k: resolve_in_structure(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_in_structure(item, variables) for item in value]
    return value


def unresolved_var_names(value: Any) -> set[str]:
    """Names still referenced as ``$(var name)`` anywhere in a JSON payload."""
    names: set[str] = set()
    if isinstance(value, str):
        names.update(_VAR_SUB.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            names |= unresolved_var_names(item)
    elif isinstance(value, list):
        for item in value:
            names |= unresolved_var_names(item)
    return names


def resolve_argument_values(arguments: Mapping[str, str]) -> dict[str, str]:
    """Expand substitutions inside argument values; one argument may reference another."""
    resolved = dict(arguments)
    for _ in range(_MAX_PASSES):
        updated = {name: resolve_substitutions(value, resolved) for name, value in resolved.items()}
        if updated == resolved:
            break
        resolved = updated
    return resolved
