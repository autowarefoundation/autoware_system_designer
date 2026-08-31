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

"""Generic record serialization: dataclass field declarations are the export schema.

Field metadata keys:
  ``exclude``  working state, never exported
  ``alias``    exported key name when it differs from the field name
  ``ref``      export the referenced object's ``unique_id`` (or a list of them)
  ``dump``     value converter applied before the generic rules

A class-level ``__serde_computed__`` tuple of ``(attribute, key)`` pairs exports
derived values (typically properties such as ``unique_id``) alongside the fields.
"""

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

JsonValue = Union[None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"]]


def dump(obj: Any) -> JsonValue:
    """Serialize a record (or container of records) to JSON-compatible data."""
    return _dump_value(obj)


def _dump_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _dump_record(value)
    if isinstance(value, (list, tuple)):
        return [_dump_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump_value(item) for key, item in value.items()}
    raise TypeError(f"serde.dump: unsupported type {type(value).__name__}")


def _dump_record(record: Any) -> Dict[str, JsonValue]:
    data: Dict[str, JsonValue] = {}
    for spec in fields(record):
        meta = spec.metadata
        if meta.get("exclude"):
            continue
        key = meta.get("alias", spec.name)
        raw = getattr(record, spec.name)
        if meta.get("ref"):
            data[key] = _dump_ref(raw)
            continue
        converter = meta.get("dump")
        if converter is not None:
            raw = converter(raw)
        data[key] = _dump_value(raw)
    for attribute, key in getattr(record, "__serde_computed__", ()):
        data[key or attribute] = _dump_value(getattr(record, attribute))
    return data


def _dump_ref(value: Any) -> Optional[JsonValue]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [item.unique_id for item in value]
    return value.unique_id
