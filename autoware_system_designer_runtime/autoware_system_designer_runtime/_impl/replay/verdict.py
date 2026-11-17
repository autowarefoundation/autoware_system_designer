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

"""Verdict of a replay: its recorded outputs against a reference, topic by topic.

Both sides are sequences of (driver time, serialized message) per topic. The rule
of a topic says how two messages compare: by content, field by field with a
numeric tolerance, or not at all. Driver times compare exactly under every rule.

Content is the field values of a message. The bytes between aligned fields of a
serialized message are padding the publisher leaves unset, so equal messages
can serialize to different bytes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from .config import RULE_EXACT, RULE_FIELDS, RULE_IGNORE, CompareRule, ReplayConfig

MATCH = "match"
DIFFER = "differ"
IGNORED = "ignored"
VERDICT_FORMAT = "autoware_system_designer/replay_verdict/1"

Record = tuple[int, bytes]
Deserialize = Callable[[str, bytes], Any]


@dataclass
class TopicVerdict:
    topic: str
    rule: str
    status: str
    reference_count: int = 0
    count: int = 0
    # First difference: message index, driver time and what differs.
    difference: Optional[dict[str, Any]] = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "topic": self.topic,
            "rule": self.rule,
            "status": self.status,
            "reference_count": self.reference_count,
            "count": self.count,
        }
        if self.difference is not None:
            out["difference"] = self.difference
        return out


@dataclass
class Verdict:
    topics: list[TopicVerdict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(t.status != DIFFER for t in self.topics)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": VERDICT_FORMAT,
            "passed": self.passed,
            "topics": [t.as_dict() for t in self.topics],
        }


def group_by_topic(messages: Iterable[tuple[int, str, bytes]], *, since_ns: int = 0) -> dict[str, list[Record]]:
    """Per-topic records of a bag read; messages before *since_ns* (the warm-up) are left out."""
    grouped: dict[str, list[Record]] = {}
    for stamp, topic, data in messages:
        if stamp >= since_ns:
            grouped.setdefault(topic, []).append((stamp, data))
    return grouped


class _UuidOrder:
    """Rank of each UUID by first appearance on one side of one topic."""

    def __init__(self) -> None:
        self._rank: dict[bytes, int] = {}

    def rank(self, uuid: Any) -> int:
        key = bytes(bytearray(uuid.uuid))
        return self._rank.setdefault(key, len(self._rank))


def _is_uuid(value: Any) -> bool:
    cls = type(value)
    return cls.__name__ == "UUID" and cls.__module__.startswith("unique_identifier_msgs")


def _is_message(value: Any) -> bool:
    return hasattr(value, "get_fields_and_field_types")


def _is_sequence(value: Any) -> bool:
    return not isinstance(value, (str, bytes)) and hasattr(value, "__len__") and hasattr(value, "__getitem__")


class _FieldComparison:
    def __init__(self, rule: CompareRule) -> None:
        self._rule = rule
        self._reference_uuids = _UuidOrder()
        self._uuids = _UuidOrder()

    def _ignored(self, path: str) -> bool:
        return any(fnmatch.fnmatchcase(path, pattern) for pattern in self._rule.ignore_fields)

    def _numbers(self, a: float, b: float) -> bool:
        if a == b or (isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b)):
            return True
        if isinstance(a, bool) or isinstance(b, bool):
            return False
        tolerance = max(self._rule.abs_tolerance, self._rule.rel_tolerance * max(abs(a), abs(b)))
        return abs(a - b) <= tolerance

    def difference(self, reference: Any, value: Any, path: str = "") -> Optional[str]:
        """Where *value* leaves *reference*, None when they agree under the rule."""
        if path and self._ignored(path):
            return None
        if self._rule.uuid_order and _is_uuid(reference) and _is_uuid(value):
            a, b = self._reference_uuids.rank(reference), self._uuids.rank(value)
            return None if a == b else f"{path}: UUID is the {b + 1}. new one, the reference's the {a + 1}."
        if _is_message(reference) and _is_message(value):
            if type(reference) is not type(value):
                return f"{path or 'message'}: type {type(value).__name__}, reference {type(reference).__name__}"
            for name in reference.get_fields_and_field_types():
                found = self.difference(
                    getattr(reference, name), getattr(value, name), f"{path}.{name}" if path else name
                )
                if found is not None:
                    return found
            return None
        if _is_sequence(reference) and _is_sequence(value):
            if len(reference) != len(value):
                return f"{path}: {len(value)} elements, reference {len(reference)}"
            for index in range(len(reference)):
                found = self.difference(reference[index], value[index], path)
                if found is not None:
                    return f"{found} (element {index})" if "(element" not in found else found
            return None
        if isinstance(reference, (int, float)) and isinstance(value, (int, float)):
            return None if self._numbers(reference, value) else f"{path}: {value!r}, reference {reference!r}"
        return None if reference == value else f"{path}: {value!r}, reference {reference!r}"


def compare_topic(
    topic: str,
    msg_type: str,
    reference: list[Record],
    recorded: list[Record],
    rule: CompareRule,
    deserialize: Optional[Deserialize] = None,
) -> TopicVerdict:
    verdict = TopicVerdict(
        topic=topic, rule=rule.rule, status=MATCH, reference_count=len(reference), count=len(recorded)
    )
    if rule.rule == RULE_IGNORE:
        verdict.status = IGNORED
        return verdict
    exact = rule.rule == RULE_EXACT
    # The exact rule is the field comparison without tolerance, exclusion or UUID ranking.
    fields = _FieldComparison(CompareRule(rule=RULE_FIELDS) if exact else rule) if deserialize is not None else None
    if fields is None and not exact:
        raise ValueError(f"{topic}: the fields rule needs a deserializer")
    for index, ((ref_stamp, ref_data), (stamp, data)) in enumerate(zip(reference, recorded)):
        detail: Optional[str] = None
        if ref_stamp != stamp:
            detail = f"driver time {stamp}, reference {ref_stamp}"
        elif ref_data != data:
            if fields is None:
                detail = "message bytes differ"
            else:
                detail = fields.difference(deserialize(msg_type, ref_data), deserialize(msg_type, data))
        elif fields is not None and rule.uuid_order:
            # Equal bytes still advance the first-appearance ranks of both sides.
            fields.difference(deserialize(msg_type, ref_data), deserialize(msg_type, data))
        if detail is not None:
            verdict.status = DIFFER
            verdict.difference = {"index": index, "stamp_ns": stamp, "detail": detail}
            return verdict
    if len(reference) != len(recorded):
        verdict.status = DIFFER
        verdict.difference = {
            "index": min(len(reference), len(recorded)),
            "detail": f"{len(recorded)} messages, reference {len(reference)}",
        }
    return verdict


def compare_outputs(
    reference: Mapping[str, list[Record]],
    recorded: Mapping[str, list[Record]],
    types: Mapping[str, str],
    config: ReplayConfig,
    deserialize: Optional[Deserialize] = None,
) -> Verdict:
    verdict = Verdict()
    for topic in sorted(set(reference) | set(recorded) | set(types)):
        verdict.topics.append(
            compare_topic(
                topic,
                types.get(topic, ""),
                reference.get(topic, []),
                recorded.get(topic, []),
                config.rule_for(topic),
                deserialize,
            )
        )
    return verdict


def ros_deserialize(msg_type: str, data: bytes) -> Any:
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message

    return deserialize_message(data, get_message(msg_type))


def _hash_content(sha: Any, value: Any) -> None:
    """Feed the field values of a message to *sha*, in field order."""
    if _is_message(value):
        for name in value.get_fields_and_field_types():
            _hash_content(sha, getattr(value, name))
    elif isinstance(value, (str, bytes)):
        raw = value.encode() if isinstance(value, str) else value
        sha.update(f"{len(raw)}\0".encode())
        sha.update(raw)
    elif hasattr(value, "tobytes"):
        # Primitive arrays: array.array and numpy.ndarray.
        sha.update(f"{len(value)}\0".encode())
        sha.update(value.tobytes())
    elif _is_sequence(value):
        sha.update(f"{len(value)}\0".encode())
        for item in value:
            _hash_content(sha, item)
    else:
        sha.update(f"{value!r}\0".encode())


def digest(
    records: Mapping[str, list[Record]],
    types: Optional[Mapping[str, str]] = None,
    deserialize: Optional[Deserialize] = None,
) -> str:
    """One hash over every recorded output, for a quick equality check of two runs. A topic with a
    type in *types* is hashed by content, any other by its serialized bytes."""
    sha = hashlib.sha256()
    for topic in sorted(records):
        msg_type = (types or {}).get(topic) if deserialize is not None else None
        for stamp, data in records[topic]:
            if msg_type:
                sha.update(f"{topic}\0{stamp}\0".encode())
                _hash_content(sha, deserialize(msg_type, data))
            else:
                sha.update(f"{topic}\0{stamp}\0{len(data)}\0".encode())
                sha.update(data)
    return sha.hexdigest()
