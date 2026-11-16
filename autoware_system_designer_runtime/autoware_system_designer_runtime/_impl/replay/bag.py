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

"""The rosbag of a replay: what it holds, read from its metadata, and its messages in time order.

The metadata is read as YAML so the pre-flight needs no ROS import; messages go
through ``rosbag2_py``, imported on first use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

METADATA_FILE = "metadata.yaml"
_STORAGE_BY_SUFFIX = {".db3": "sqlite3", ".mcap": "mcap"}
DURABILITY_TRANSIENT_LOCAL = 1


class BagError(ValueError):
    pass


@dataclass
class BagTopic:
    name: str
    msg_type: str
    count: int
    # Durability the recorded publishers offered, as rmw enum values.
    durabilities: list[int] = field(default_factory=list)

    @property
    def transient_local(self) -> bool:
        return DURABILITY_TRANSIENT_LOCAL in self.durabilities


@dataclass
class BagInfo:
    path: Path
    storage_id: str
    start_ns: int
    duration_ns: int
    topics: dict[str, BagTopic]
    # Text the reference key hashes: the bag's metadata.
    fingerprint: str = ""

    @property
    def end_ns(self) -> int:
        return self.start_ns + self.duration_ns


def bag_directory(path: Path) -> Path:
    path = Path(path)
    return path.parent if path.is_file() else path


def _durabilities(profiles: Any) -> list[int]:
    if isinstance(profiles, str):
        import yaml

        try:
            profiles = yaml.safe_load(profiles) if profiles.strip() else []
        except yaml.YAMLError:
            return []
    found = []
    for profile in profiles or []:
        value = profile.get("durability") if isinstance(profile, Mapping) else None
        if isinstance(value, int):
            found.append(value)
        elif isinstance(value, str):
            found.append(DURABILITY_TRANSIENT_LOCAL if "transient" in value.lower() else 2)
    return found


def info_from_metadata(payload: Mapping[str, Any], path: Path, fingerprint: str = "") -> BagInfo:
    info = payload.get("rosbag2_bagfile_information")
    if not isinstance(info, Mapping):
        raise BagError(f"{path}: no rosbag2_bagfile_information in the metadata")
    topics: dict[str, BagTopic] = {}
    for item in info.get("topics_with_message_count", []) or []:
        meta = item.get("topic_metadata", {})
        name = meta.get("name")
        if not name:
            continue
        topics[name] = BagTopic(
            name=name,
            msg_type=str(meta.get("type", "")),
            count=int(item.get("message_count", 0)),
            durabilities=_durabilities(meta.get("offered_qos_profiles")),
        )
    return BagInfo(
        path=Path(path),
        storage_id=str(info.get("storage_identifier", "")),
        start_ns=int((info.get("starting_time") or {}).get("nanoseconds_since_epoch", 0)),
        duration_ns=int((info.get("duration") or {}).get("nanoseconds", 0)),
        topics=topics,
        fingerprint=fingerprint,
    )


def read_bag_info(path: Path) -> BagInfo:
    """Topics, types and time span of the bag at *path* (its directory or one of its files)."""
    path = Path(path)
    if not path.exists():
        raise BagError(f"bag {path} not found")
    directory = bag_directory(path)
    metadata = directory / METADATA_FILE
    if metadata.is_file():
        import yaml

        text = metadata.read_text()
        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise BagError(f"{metadata}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise BagError(f"{metadata}: not a rosbag2 metadata file")
        return info_from_metadata(payload, directory, fingerprint=text)
    return _info_from_storage(path)


def _info_from_storage(path: Path) -> BagInfo:
    """A storage file without metadata, read through rosbag2."""
    storage_id = _STORAGE_BY_SUFFIX.get(path.suffix)
    if not path.is_file() or storage_id is None:
        raise BagError(f"{path}: no {METADATA_FILE} and not a .db3 or .mcap file")
    import rosbag2_py

    meta = rosbag2_py.Info().read_metadata(str(path), storage_id)
    topics = {
        item.topic_metadata.name: BagTopic(
            name=item.topic_metadata.name,
            msg_type=item.topic_metadata.type,
            count=item.message_count,
            durabilities=_durabilities(item.topic_metadata.offered_qos_profiles),
        )
        for item in meta.topics_with_message_count
    }
    stat = path.stat()
    return BagInfo(
        path=path,
        storage_id=storage_id,
        start_ns=int(meta.starting_time.nanoseconds),
        duration_ns=int(meta.duration.nanoseconds),
        topics=topics,
        fingerprint=f"{path.name}:{stat.st_size}:{meta.message_count}",
    )


class BagMessages:
    """Messages of the chosen topics in recorded order, one ahead: ``peek`` shows the next, ``pop`` takes it."""

    def __init__(self, info: BagInfo, topics: Sequence[str]) -> None:
        import rosbag2_py

        self._reader = rosbag2_py.SequentialReader()
        self._reader.open(
            rosbag2_py.StorageOptions(uri=str(info.path), storage_id=info.storage_id),
            rosbag2_py.ConverterOptions(input_serialization_format="", output_serialization_format=""),
        )
        self._reader.set_filter(rosbag2_py.StorageFilter(topics=list(topics)))
        self._next: Optional[tuple[int, str, bytes]] = None
        self._advance()

    def _advance(self) -> None:
        if self._reader.has_next():
            topic, data, stamp = self._reader.read_next()
            self._next = (int(stamp), topic, data)
        else:
            self._next = None

    def peek(self) -> Optional[tuple[int, str, bytes]]:
        return self._next

    def pop(self) -> tuple[int, str, bytes]:
        message = self._next
        if message is None:
            raise StopIteration
        self._advance()
        return message

    def close(self) -> None:
        self._reader = None


class BagSink:
    """Writer of the recorded outputs, a rosbag in the storage format of the input bag."""

    def __init__(self, path: Path, topics: Mapping[str, str], storage_id: str = "") -> None:
        import rosbag2_py

        self._writer = rosbag2_py.SequentialWriter()
        self._writer.open(
            rosbag2_py.StorageOptions(uri=str(path), storage_id=storage_id or rosbag2_py.get_default_storage_id()),
            rosbag2_py.ConverterOptions(input_serialization_format="cdr", output_serialization_format="cdr"),
        )
        for name, msg_type in sorted(topics.items()):
            self._writer.create_topic(rosbag2_py.TopicMetadata(name=name, type=msg_type, serialization_format="cdr"))

    def write(self, topic: str, data: bytes, stamp_ns: int) -> None:
        self._writer.write(topic, data, stamp_ns)

    def close(self) -> None:
        # The writer finishes its files when it is released.
        self._writer = None


def read_messages(path: Path, topics: Optional[Sequence[str]] = None) -> Iterator[tuple[int, str, bytes]]:
    """Every message of a bag as (stamp, topic, serialized message), in recorded order."""
    info = read_bag_info(path)
    messages = BagMessages(info, list(topics) if topics is not None else sorted(info.topics))
    while messages.peek() is not None:
        yield messages.pop()
