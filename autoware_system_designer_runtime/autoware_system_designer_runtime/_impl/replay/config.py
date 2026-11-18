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
    inputs: [/vehicle/status/velocity]        # bag topics fed beside the boundary inputs
    remap:                                    # recorded topic: topic it is fed as
      /sensing/lidar/top/pointcloud: /perception/input/pointcloud
    accept: [/map/vector_map]                 # inputs, services or subscriptions that stay unfed
    outputs: [/perception/internal/objects]   # internal topics recorded beside the boundary outputs
    skip_outputs: [/perception/debug/image]   # boundary outputs left unrecorded
    lead_in: 0.5                              # seconds only transforms and latched last messages are fed
    driver:                                   # driver settings, see DRIVER_SETTINGS
      callback_timeout: 60
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

``lead_in`` and each driver setting the file leaves unset take their value from
``config/replay_defaults.yaml`` of the package, layered with the user's file of the same name (see user_home).

Plain Python with no ROS import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from ...user_home import ConfigFileError, cached_config, load_yaml

REPLAY_DEFAULTS_NAME = "replay_defaults.yaml"

RULE_EXACT = "exact"
RULE_FIELDS = "fields"
RULE_IGNORE = "ignore"
RULES = (RULE_EXACT, RULE_FIELDS, RULE_IGNORE)


SECONDS = "s"
COUNT = "count"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DriverSetting:
    name: str
    unit: str
    description: str
    # Part of a run the setting belongs to; settings are listed grouped by it.
    category: str


DRIVER_SETTINGS = (
    DriverSetting("startup_timeout", SECONDS, "The scope has to be up within it.", "startup"),
    DriverSetting("startup_settle", SECONDS, "The scope is up once no endpoint was created for this long.", "startup"),
    DriverSetting(
        "match_timeout", SECONDS, "Publishers have this long to match the subscriptions of the scope.", "startup"
    ),
    DriverSetting("callback_timeout", SECONDS, "A granted callback has to return within it.", "callbacks"),
    DriverSetting(
        "nested_after",
        SECONDS,
        "Wall time a callback runs before the nested callbacks of its process run beside it.",
        "callbacks",
    ),
    DriverSetting(
        "delivery_timeout",
        SECONDS,
        "A published message has this long to show as work, a due timer to become ready, a publish to be recorded.",
        "delivery",
    ),
    DriverSetting(
        "burst_settle",
        SECONDS,
        "Pause before a callback whose subscription holds several messages in flight.",
        "delivery",
    ),
    DriverSetting(
        "miss_limit",
        COUNT,
        "A subscription that missed this many deliveries in a row is no longer waited for.",
        "delivery",
    ),
    DriverSetting(
        "gate_hold", SECONDS, "Longest stay of an executor in its wait with ready work and no grant.", "gate"
    ),
    DriverSetting("take_timeout", SECONDS, "A take waits this long for a message in flight.", "gate"),
    DriverSetting("process_grants", COUNT, "Threads of a process that hold a grant at once.", "gate"),
)


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
    # Seconds from the bag's start over which only the ambient inputs and the last message of each latched topic are fed.
    lead_in: float = field(default_factory=lambda: _defaults()["lead_in"])
    # Recorded bag topic to the topic it is fed as.
    remap: dict[str, str] = field(default_factory=dict)
    # Value of every driver setting.
    driver: dict[str, float] = field(default_factory=lambda: dict(_defaults()["driver"]))
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
        unknown = set(payload) - {
            "scope",
            "inputs",
            "accept",
            "outputs",
            "skip_outputs",
            "lead_in",
            "remap",
            "driver",
            "compare",
        }
        if unknown:
            raise ConfigError(f"replay configuration: unknown key(s) {sorted(unknown)}")
        config = cls(
            scope=_names(payload, "scope"),
            inputs=_names(payload, "inputs"),
            accept=_names(payload, "accept"),
            outputs=_names(payload, "outputs"),
            skip_outputs=_names(payload, "skip_outputs"),
        )
        if payload.get("lead_in") is not None:
            config.lead_in = _seconds(payload["lead_in"], "lead_in")
        remap = payload.get("remap") or {}
        if not isinstance(remap, Mapping) or not all(
            isinstance(source, str) and isinstance(target, str) for source, target in remap.items()
        ):
            raise ConfigError("remap: expected a mapping of recorded topic to replayed topic")
        config.remap = dict(remap)
        config.driver.update(parse_driver(payload.get("driver"), "driver"))
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
        try:
            return cls.from_mapping(load_yaml(Path(path)))
        except ConfigFileError as exc:
            raise ConfigError(str(exc)) from exc


def _seconds(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ConfigError(f"{where}: expected a number of seconds, zero or more")
    return float(value)


def parse_driver(value: Any, where: str) -> dict[str, float]:
    """The driver settings *value* sets, each checked against its unit."""
    if value is None:
        return {}
    units = {setting.name: setting.unit for setting in DRIVER_SETTINGS}
    if not isinstance(value, Mapping) or set(value) - set(units):
        raise ConfigError(f"{where}: expected a mapping with keys of {', '.join(units)}")
    found: dict[str, float] = {}
    for name, item in value.items():
        if units[name] == COUNT:
            if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                raise ConfigError(f"{where}.{name}: expected a count, one or more")
            found[name] = item
        else:
            found[name] = _seconds(item, f"{where}.{name}")
    return found


def _defaults() -> dict[str, Any]:
    return cached_config(REPLAY_DEFAULTS_NAME, _parse_defaults, ConfigError, merged=("driver",))


def _parse_defaults(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) - {"lead_in", "driver"}:
        raise ConfigError("expected a mapping with the keys lead_in and driver")
    driver = parse_driver(payload.get("driver"), "driver")
    missing = [setting.name for setting in DRIVER_SETTINGS if setting.name not in driver]
    if missing:
        raise ConfigError(f"driver: no value of {', '.join(missing)}")
    return {"lead_in": _seconds(payload.get("lead_in", 0.0), "lead_in"), "driver": driver}


def driver_settings() -> list[dict[str, Any]]:
    """Every driver setting with its unit, description, category and default value."""
    defaults = _defaults()["driver"]
    return [
        {
            "name": setting.name,
            "unit": setting.unit,
            "description": setting.description,
            "category": setting.category,
            "default": defaults[setting.name],
        }
        for setting in DRIVER_SETTINGS
    ]


def _names(payload: Mapping[str, Any], key: str) -> list[str]:
    value = payload.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key}: expected a list of names")
    return list(value)
