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

"""Topics of the middleware that no design declares, by the role the runtime gives them.

The roles are read from ``config/special_topics.yaml`` of this package, layered with the user's file of
the same name (see user_home):

.. code-block:: yaml

    infrastructure: [/rosout, /parameter_events]   # carried by every process
    clock: [/clock]                                # time source of a process on simulated time
    companions: {/tf: [/tf_static]}                # subscribed together with the key
    ambient_inputs: [/tf, /tf_static]              # bag topics fed to every replay scope
    nested_callbacks: [/tf, /tf_static]            # callbacks a running callback may wait on
    handshake_suffixes: [/_supported_types]        # handshake topics beside a negotiated topic

Plain Python with no ROS import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

from .user_home import PACKAGE_CONFIG_DIR, ConfigFileError, describe, read_config

SPECIAL_TOPICS_NAME = "special_topics.yaml"
SPECIAL_TOPICS_FILE = PACKAGE_CONFIG_DIR / SPECIAL_TOPICS_NAME

_LIST_KEYS = ("infrastructure", "clock", "ambient_inputs", "nested_callbacks", "handshake_suffixes")
_KEYS = (*_LIST_KEYS, "companions")


class SpecialTopicsError(ValueError):
    pass


@dataclass(frozen=True)
class SpecialTopics:
    infrastructure: frozenset[str] = frozenset()
    clock: frozenset[str] = frozenset()
    companions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # In feeding order.
    ambient_inputs: tuple[str, ...] = ()
    nested_callbacks: frozenset[str] = frozenset()
    handshake_suffixes: tuple[str, ...] = ()

    @cached_property
    def process(self) -> frozenset[str]:
        """Topics a process carries before its own code runs: infrastructure and clock."""
        return self.infrastructure | self.clock

    @cached_property
    def _companion_of(self) -> dict[str, str]:
        return {companion: topic for topic, companions in self.companions.items() for companion in companions}

    def companion_of(self, topic: str) -> Optional[str]:
        """The topic *topic* is subscribed together with, if it is a companion."""
        return self._companion_of.get(topic)

    def is_handshake(self, topic: str) -> bool:
        return topic.endswith(self.handshake_suffixes) if self.handshake_suffixes else False

    def as_dict(self) -> dict[str, Any]:
        return {
            "infrastructure": sorted(self.infrastructure),
            "clock": sorted(self.clock),
            "companions": {topic: list(companions) for topic, companions in self.companions.items()},
            "ambient_inputs": list(self.ambient_inputs),
            "nested_callbacks": sorted(self.nested_callbacks),
            "handshake_suffixes": list(self.handshake_suffixes),
        }

    @classmethod
    def from_mapping(cls, payload: Any) -> "SpecialTopics":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise SpecialTopicsError("special topics: expected a mapping")
        unknown = set(payload) - set(_KEYS)
        if unknown:
            raise SpecialTopicsError(f"special topics: unknown key(s) {sorted(unknown)}")
        names = {key: _names(payload, key) for key in _LIST_KEYS}
        companions = payload.get("companions") or {}
        if not isinstance(companions, Mapping):
            raise SpecialTopicsError("companions: expected a mapping of topic to companion topics")
        return cls(
            infrastructure=frozenset(names["infrastructure"]),
            clock=frozenset(names["clock"]),
            companions={str(topic): tuple(_names(companions, topic)) for topic in companions},
            ambient_inputs=tuple(names["ambient_inputs"]),
            nested_callbacks=frozenset(names["nested_callbacks"]),
            handshake_suffixes=tuple(names["handshake_suffixes"]),
        )

    @classmethod
    def load(cls, path: Path) -> "SpecialTopics":
        import yaml

        try:
            payload = yaml.safe_load(Path(path).read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise SpecialTopicsError(f"{path}: {exc}") from exc
        return cls.from_mapping(payload)


def _names(payload: Mapping[str, Any], key: str) -> list[str]:
    value = payload.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SpecialTopicsError(f"{key}: expected a list of names")
    return list(value)


@lru_cache(maxsize=1)
def special_topics() -> SpecialTopics:
    """The roles of the package's configuration file, layered with the user's."""
    try:
        payload, files = read_config(SPECIAL_TOPICS_NAME)
    except ConfigFileError as exc:
        raise SpecialTopicsError(str(exc)) from exc
    try:
        return SpecialTopics.from_mapping(payload)
    except SpecialTopicsError as exc:
        raise SpecialTopicsError(f"{describe(files)}: {exc}") from exc
