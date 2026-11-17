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

"""What a replay sets on the middleware of its scope, and the message types it reads by convention.

Read from ``config/replay_middleware.yaml`` of the package, layered with the user's file of the same name
(see user_home):

.. code-block:: yaml

    node_parameters: {use_sim_time: true}               # set on every node of the scope
    launcher: {use_intra_process_comms: false}          # set on every member of the scope
    isolation_environment: {ROS_LOCALHOST_ONLY: "1"}    # keeps discovery on this host
    domain_id_variable: ROS_DOMAIN_ID                   # carries the domain of the replay
    identifier_types:                                   # type: field holding the identifier's bytes
      unique_identifier_msgs/msg/UUID: uuid

Plain Python with no ROS import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

from ...user_home import PACKAGE_CONFIG_DIR, ConfigFileError, describe, read_config

REPLAY_MIDDLEWARE_NAME = "replay_middleware.yaml"
REPLAY_MIDDLEWARE_FILE = PACKAGE_CONFIG_DIR / REPLAY_MIDDLEWARE_NAME

_KEYS = ("node_parameters", "launcher", "isolation_environment", "domain_id_variable", "identifier_types")
# Design parameter type of a value, by its Python type.
_PARAMETER_TYPES = {bool: "bool", int: "int", float: "double", str: "string"}


class ReplayMiddlewareError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayMiddleware:
    node_parameters: Mapping[str, Any] = field(default_factory=dict)
    launcher: Mapping[str, Any] = field(default_factory=dict)
    isolation_environment: Mapping[str, str] = field(default_factory=dict)
    domain_id_variable: str = ""
    identifier_types: Mapping[str, str] = field(default_factory=dict)

    def parameter_entries(self) -> list[dict[str, Any]]:
        """The node parameters as parameter entries of a system structure."""
        return [
            {"name": name, "value": value, "type": _PARAMETER_TYPES[type(value)], "parameter_type": "MODE"}
            for name, value in self.node_parameters.items()
        ]

    def parameter_arguments(self, cmd: list[str]) -> list[str]:
        """Command line arguments that set the node parameters *cmd* does not set itself."""
        arguments: list[str] = []
        for name, value in self.node_parameters.items():
            if not any(str(arg).startswith(f"{name}:=") for arg in cmd):
                text = str(value).lower() if isinstance(value, bool) else str(value)
                arguments += ["-p", f"{name}:={text}"]
        return arguments

    def identifier_field(self, message: Any) -> Optional[str]:
        """Field of *message* that holds its identifier bytes, None when it is no identifier."""
        cls = type(message)
        package, _, rest = cls.__module__.partition(".")
        kind = rest.partition(".")[0]
        return self.identifier_types.get(f"{package}/{kind}/{cls.__name__}")

    @classmethod
    def from_mapping(cls, payload: Any) -> "ReplayMiddleware":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise ReplayMiddlewareError("replay middleware: expected a mapping")
        unknown = set(payload) - set(_KEYS)
        if unknown:
            raise ReplayMiddlewareError(f"replay middleware: unknown key(s) {sorted(unknown)}")
        node_parameters = _mapping(payload, "node_parameters")
        for name, value in node_parameters.items():
            if type(value) not in _PARAMETER_TYPES:
                raise ReplayMiddlewareError(f"node_parameters.{name}: expected a bool, a number or a string")
        domain_id_variable = payload.get("domain_id_variable") or ""
        if not isinstance(domain_id_variable, str):
            raise ReplayMiddlewareError("domain_id_variable: expected a name")
        return cls(
            node_parameters=node_parameters,
            launcher=_mapping(payload, "launcher"),
            isolation_environment=_strings(payload, "isolation_environment"),
            domain_id_variable=domain_id_variable,
            identifier_types=_strings(payload, "identifier_types"),
        )

    @classmethod
    def load(cls, path: Path) -> "ReplayMiddleware":
        import yaml

        try:
            payload = yaml.safe_load(Path(path).read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ReplayMiddlewareError(f"{path}: {exc}") from exc
        return cls.from_mapping(payload)


def _mapping(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key) or {}
    if not isinstance(value, Mapping) or not all(isinstance(name, str) for name in value):
        raise ReplayMiddlewareError(f"{key}: expected a mapping of names")
    return dict(value)


def _strings(payload: Mapping[str, Any], key: str) -> dict[str, str]:
    value = _mapping(payload, key)
    if not all(isinstance(item, str) for item in value.values()):
        raise ReplayMiddlewareError(f"{key}: expected a mapping of names to strings")
    return value


@lru_cache(maxsize=1)
def replay_middleware() -> ReplayMiddleware:
    """The settings of the package's configuration file, layered with the user's."""
    try:
        payload, files = read_config(REPLAY_MIDDLEWARE_NAME)
    except ConfigFileError as exc:
        raise ReplayMiddlewareError(str(exc)) from exc
    try:
        return ReplayMiddleware.from_mapping(payload)
    except ReplayMiddlewareError as exc:
        raise ReplayMiddlewareError(f"{describe(files)}: {exc}") from exc
