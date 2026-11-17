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

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Collection, Iterator, Mapping, Optional, Sequence

from .link import DURABILITY_TRANSIENT_LOCAL, DURABILITY_VOLATILE

METADATA_FILE = "metadata.yaml"
_STORAGE_BY_SUFFIX = {".db3": "sqlite3", ".mcap": "mcap"}


class BagError(ValueError):
    pass


@dataclass
class BagTopic:
    name: str
    msg_type: str
    count: int
    # Durability the recorded publishers offered, as rmw enum values.
    durabilities: list[int] = field(default_factory=list)
    # Name the bag recorded it under, when a remap gave it another one.
    recorded_name: str = ""

    @property
    def source(self) -> str:
        return self.recorded_name or self.name

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
    # Recorded topics a remap took the name of; they are not fed.
    shadowed: list[str] = field(default_factory=list)
    # Storage files of a bag without metadata, in the order of their start times.
    files: list[Path] = field(default_factory=list)

    @property
    def end_ns(self) -> int:
        return self.start_ns + self.duration_ns


def bag_directory(path: Path) -> Path:
    path = Path(path)
    return path.parent if path.is_file() else path


def _durabilities(profiles: Any) -> list[int]:
    """Durability of each offered profile, as rmw enum values; a name other than transient local
    counts as volatile."""
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
            found.append(DURABILITY_TRANSIENT_LOCAL if "transient" in value.lower() else DURABILITY_VOLATILE)
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
        storage_id=str(info.get("storage_identifier") or "") or _storage_of_files(info, Path(path)),
        start_ns=int((info.get("starting_time") or {}).get("nanoseconds_since_epoch", 0)),
        duration_ns=int((info.get("duration") or {}).get("nanoseconds", 0)),
        topics=topics,
        fingerprint=fingerprint,
    )


def remap_topics(info: BagInfo, remap: Mapping[str, str]) -> BagInfo:
    """The bag with its topics renamed by *remap* (recorded name to replayed name). A recorded topic
    whose name a remap takes is shadowed."""
    remap = {source: target for source, target in remap.items() if source != target}
    if not remap:
        return info
    missing = sorted(source for source in remap if source not in info.topics)
    if missing:
        raise BagError(f"remap of {', '.join(missing)}: not in the bag")
    targets: dict[str, str] = {}
    for source, target in remap.items():
        if not target.startswith("/"):
            raise BagError(f"remap of {source} to {target!r}: not an absolute topic name")
        if target in targets:
            raise BagError(f"remap of {targets[target]} and {source}: both to {target}")
        targets[target] = source
    topics: dict[str, BagTopic] = {}
    shadowed: list[str] = []
    for name, topic in info.topics.items():
        if name in remap:
            topics[remap[name]] = replace(topic, name=remap[name], recorded_name=name)
        elif name in targets:
            shadowed.append(name)
        else:
            topics[name] = topic
    return replace(info, topics=topics, shadowed=sorted(shadowed))


def _storage_of_files(info: Mapping[str, Any], directory: Path) -> str:
    """Storage of a bag whose metadata names none: the suffix of its listed files, else of the files
    in *directory*."""
    for name in info.get("relative_file_paths") or []:
        storage_id = _STORAGE_BY_SUFFIX.get(Path(str(name)).suffix)
        if storage_id:
            return storage_id
    return _storage_id(directory) if directory.is_dir() else ""


def _storage_id(directory: Path) -> str:
    """Storage of the bag files in *directory*; empty when it holds none."""
    for suffix, storage_id in _STORAGE_BY_SUFFIX.items():
        if any(directory.glob(f"*{suffix}")):
            return storage_id
    return ""


def read_bag_info(path: Path) -> BagInfo:
    """Topics, types and time span of the bag at *path* (its directory or one of its files), from its
    ``metadata.yaml``, else from the storage file itself."""
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
    """A bag without metadata, read through rosbag2 from its storage file, or from the split files of
    its directory."""
    files = [path] if path.is_file() else sorted(p for suffix in _STORAGE_BY_SUFFIX for p in path.glob(f"*{suffix}"))
    storages = {_STORAGE_BY_SUFFIX.get(file.suffix) for file in files}
    if len(storages) != 1 or None in storages:
        raise BagError(f"{path}: no {METADATA_FILE} and not a .db3 or .mcap file, or a directory of either")
    (storage_id,) = storages
    import rosbag2_py

    topics: dict[str, BagTopic] = {}
    spans: list[tuple[int, int, Path]] = []
    marks = []
    for file in files:
        meta = rosbag2_py.Info().read_metadata(str(file), storage_id)
        for item in meta.topics_with_message_count:
            name = item.topic_metadata.name
            topic = topics.setdefault(name, BagTopic(name=name, msg_type=item.topic_metadata.type, count=0))
            topic.count += item.message_count
            topic.durabilities += _durabilities(item.topic_metadata.offered_qos_profiles)
        if meta.message_count:
            start = int(meta.starting_time.nanoseconds)
            spans.append((start, start + int(meta.duration.nanoseconds), file))
        marks.append(f"{file.name}:{file.stat().st_size}:{meta.message_count}")
    spans.sort()
    start_ns = spans[0][0] if spans else 0
    return BagInfo(
        path=path,
        storage_id=storage_id,
        start_ns=start_ns,
        duration_ns=max((end for _, end, _ in spans), default=0) - start_ns,
        topics=topics,
        fingerprint=" ".join(marks),
        files=[file for _, _, file in spans],
    )


class BagMessages:
    """Messages of the chosen topics in recorded order, one ahead: ``peek`` shows the next, ``pop`` takes it.
    Topics are chosen and given by their replayed names."""

    def __init__(self, info: BagInfo, topics: Sequence[str]) -> None:
        self._storage_id = info.storage_id
        self._names = {info.topics[name].source if name in info.topics else name: name for name in topics}
        # What is left to open: the bag itself, or its storage files one after the other.
        self._uris = [str(file) for file in info.files] or [str(info.path)]
        self._reader = None
        self._next: Optional[tuple[int, str, bytes]] = None
        self._advance()

    def _open(self, uri: str) -> None:
        import rosbag2_py

        self._reader = rosbag2_py.SequentialReader()
        self._reader.open(
            rosbag2_py.StorageOptions(uri=uri, storage_id=self._storage_id),
            rosbag2_py.ConverterOptions(input_serialization_format="", output_serialization_format=""),
        )
        self._reader.set_filter(rosbag2_py.StorageFilter(topics=list(self._names)))

    def _advance(self) -> None:
        while self._reader is None or not self._reader.has_next():
            if not self._uris:
                self._next = None
                return
            self._open(self._uris.pop(0))
        topic, data, stamp = self._reader.read_next()
        self._next = (int(stamp), self._names.get(topic, topic), data)

    def peek(self) -> Optional[tuple[int, str, bytes]]:
        return self._next

    def pop(self) -> tuple[int, str, bytes]:
        message = self._next
        if message is None:
            raise IndexError("no message")
        self._advance()
        return message

    def close(self) -> None:
        self._reader = None
        self._uris = []


class LeadIn:
    """A message source that, before *since_ns*, holds every message of the topics in *kept* and the
    last message of each topic in *latched*: what a subscriber joining at *since_ns* receives from a
    transient-local publisher, whatever rate it was recorded at."""

    def __init__(self, source, since_ns: int, kept: Collection[str], latched: Collection[str] = ()) -> None:
        self._source = source
        kept = frozenset(kept)
        latched = frozenset(latched) - kept
        held: list[tuple[int, tuple[int, str, bytes]]] = []
        last: dict[str, tuple[int, tuple[int, str, bytes]]] = {}
        order = 0
        while True:
            message = source.peek()
            if message is None or message[0] >= since_ns:
                break
            source.pop()
            if message[1] in kept:
                held.append((order, message))
            elif message[1] in latched:
                last[message[1]] = (order, message)
            order += 1
        self._held = [message for _, message in sorted([*held, *last.values()], key=lambda item: item[0])]

    def peek(self) -> Optional[tuple[int, str, bytes]]:
        return self._held[0] if self._held else self._source.peek()

    def pop(self) -> tuple[int, str, bytes]:
        return self._held.pop(0) if self._held else self._source.pop()

    def close(self) -> None:
        self._source.close()


def topic_metadata(name: str, msg_type: str):
    """rosbag2 metadata of a CDR topic; distributions from Iron on take a topic id first."""
    import rosbag2_py

    fields = {"name": name, "type": msg_type, "serialization_format": "cdr"}
    try:
        return rosbag2_py.TopicMetadata(id=0, **fields)
    except TypeError:
        return rosbag2_py.TopicMetadata(**fields)


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
            self._writer.create_topic(topic_metadata(name, msg_type))

    def write(self, topic: str, data: bytes, stamp_ns: int) -> None:
        self._writer.write(topic, data, stamp_ns)

    def close(self) -> None:
        # The writer finishes its files when it is released.
        self._writer = None


def read_messages(path: Path) -> Iterator[tuple[int, str, bytes]]:
    """Every message of a bag as (stamp, topic, serialized message), in recorded order."""
    info = read_bag_info(path)
    messages = BagMessages(info, sorted(info.topics))
    while messages.peek() is not None:
        yield messages.pop()
