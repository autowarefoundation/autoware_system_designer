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

"""Replay configuration file: what the design's ports do not say about a scope.

.. code-block:: yaml

    scope: [/perception/object_recognition]   # node or module paths, beside --scope
    inputs: [/tf, /tf_static]                 # bag topics fed beside the boundary inputs
    remap:                                    # recorded topic: topic it is fed as
      /sensing/lidar/top/pointcloud: /perception/input/pointcloud
    accept: [/map/vector_map]                 # inputs, services or subscriptions that stay unfed
    outputs: [/perception/internal/objects]   # internal topics recorded beside the boundary outputs
    skip_outputs: [/perception/debug/image]   # boundary outputs left unrecorded
    compare:
      default: exact                          # exact | fields | ignore
      topics:
        /perception/objects:
          rule: fields
          abs_tolerance: 1.0e-6
          rel_tolerance: 0.0
          ignore_fields: [header.stamp]
          uuid_order: true
        /diagnostics: {rule: ignore}

Plain Python with no ROS import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

RULE_EXACT = "exact"
RULE_FIELDS = "fields"
RULE_IGNORE = "ignore"
RULES = (RULE_EXACT, RULE_FIELDS, RULE_IGNORE)


class ConfigError(ValueError):
    pass


@dataclass
class CompareRule:
    """How one topic's messages are compared with the reference."""

    rule: str = RULE_EXACT
    abs_tolerance: float = 0.0
    rel_tolerance: float = 0.0
    # Field paths left out, without sequence indices; fnmatch patterns.
    ignore_fields: list[str] = field(default_factory=list)
    # UUID fields compare by the order of first appearance on the topic.
    uuid_order: bool = False

    @classmethod
    def from_mapping(cls, value: Any, where: str) -> "CompareRule":
        if isinstance(value, str):
            value = {"rule": value}
        if not isinstance(value, Mapping):
            raise ConfigError(f"{where}: expected a rule name or a mapping")
        unknown = set(value) - {"rule", "abs_tolerance", "rel_tolerance", "ignore_fields", "uuid_order"}
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
        rule = value.get("rule", RULE_FIELDS if len(value) > 0 and "rule" not in value else RULE_EXACT)
        if rule not in RULES:
            raise ConfigError(f"{where}: rule {rule!r} is not one of {', '.join(RULES)}")
        return cls(
            rule=rule,
            abs_tolerance=float(value.get("abs_tolerance", 0.0)),
            rel_tolerance=float(value.get("rel_tolerance", 0.0)),
            ignore_fields=[str(item) for item in value.get("ignore_fields", [])],
            uuid_order=bool(value.get("uuid_order", False)),
        )


@dataclass
class ReplayConfig:
    scope: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    accept: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    skip_outputs: list[str] = field(default_factory=list)
    # Recorded bag topic to the topic it is fed as.
    remap: dict[str, str] = field(default_factory=dict)
    default_rule: CompareRule = field(default_factory=CompareRule)
    topic_rules: dict[str, CompareRule] = field(default_factory=dict)

    def rule_for(self, topic: str) -> CompareRule:
        return self.topic_rules.get(topic, self.default_rule)

    @classmethod
    def from_mapping(cls, payload: Any) -> "ReplayConfig":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise ConfigError("replay configuration: expected a mapping")
        unknown = set(payload) - {"scope", "inputs", "accept", "outputs", "skip_outputs", "remap", "compare"}
        if unknown:
            raise ConfigError(f"replay configuration: unknown key(s) {sorted(unknown)}")
        config = cls(
            scope=_names(payload, "scope"),
            inputs=_names(payload, "inputs"),
            accept=_names(payload, "accept"),
            outputs=_names(payload, "outputs"),
            skip_outputs=_names(payload, "skip_outputs"),
        )
        remap = payload.get("remap") or {}
        if not isinstance(remap, Mapping) or not all(
            isinstance(source, str) and isinstance(target, str) for source, target in remap.items()
        ):
            raise ConfigError("remap: expected a mapping of recorded topic to replayed topic")
        config.remap = dict(remap)
        compare = payload.get("compare") or {}
        if not isinstance(compare, Mapping):
            raise ConfigError("compare: expected a mapping")
        unknown = set(compare) - {"default", "topics"}
        if unknown:
            raise ConfigError(f"compare: unknown key(s) {sorted(unknown)}")
        if "default" in compare:
            config.default_rule = CompareRule.from_mapping(compare["default"], "compare.default")
        topics = compare.get("topics") or {}
        if not isinstance(topics, Mapping):
            raise ConfigError("compare.topics: expected a mapping of topic to rule")
        for topic, rule in topics.items():
            config.topic_rules[str(topic)] = CompareRule.from_mapping(rule, f"compare.topics.{topic}")
        return config

    @classmethod
    def load(cls, path: Optional[Path]) -> "ReplayConfig":
        if path is None:
            return cls()
        import yaml

        try:
            payload = yaml.safe_load(Path(path).read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        return cls.from_mapping(payload)


def _names(payload: Mapping[str, Any], key: str) -> list[str]:
    value = payload.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key}: expected a list of names")
    return list(value)
