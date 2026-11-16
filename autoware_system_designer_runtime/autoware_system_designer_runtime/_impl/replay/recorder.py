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

"""Recorded outputs of a replay, in the order and at the driver time of their publication.

A subscription hears a message some time after its publish; the tracer reports the
publish at once. The recorder pairs the two: every reported publish reserves the next
message heard on its topic, and a step is complete once every reserved message is
here, so the record does not depend on arrival order across topics.
"""

from __future__ import annotations

from collections import deque
from typing import Optional, Protocol


class Sink(Protocol):
    def write(self, topic: str, data: bytes, stamp_ns: int) -> None: ...


class OutputRecorder:
    def __init__(self, topics: dict[str, str], sink: Optional[Sink] = None) -> None:
        self.topics = dict(topics)
        self._sink = sink
        self._heard: dict[str, deque[bytes]] = {topic: deque() for topic in topics}
        self._reserved: deque[tuple[str, int]] = deque()
        self._reserved_count: dict[str, int] = {topic: 0 for topic in topics}
        self._active = False
        self.written = 0
        # Messages heard without a reported publish: a publisher outside the gate.
        self.unannounced = 0
        self.lost = 0

    def start(self) -> None:
        """Drops what was heard before the first step and starts recording."""
        for queue in self._heard.values():
            queue.clear()
        self._reserved.clear()
        self._reserved_count = {topic: 0 for topic in self.topics}
        self._active = True

    def published(self, topic: str, stamp_ns: int) -> None:
        if self._active and topic in self.topics:
            self._reserved.append((topic, stamp_ns))
            self._reserved_count[topic] += 1

    def heard(self, topic: str, data: bytes) -> None:
        if self._active and topic in self._heard:
            self._heard[topic].append(data)

    def missing(self) -> Optional[str]:
        """A topic with a reported publish that was not heard yet."""
        for topic, count in self._reserved_count.items():
            if count > len(self._heard[topic]):
                return topic
        return None

    def forgive(self, topic: str) -> None:
        """Gives up on the reserved messages of *topic* that never arrived."""
        excess = self._reserved_count[topic] - len(self._heard[topic])
        if excess <= 0:
            return
        self.lost += excess
        kept: deque[tuple[str, int]] = deque()
        for entry in reversed(self._reserved):
            if entry[0] == topic and excess > 0:
                excess -= 1
                continue
            kept.appendleft(entry)
        self._reserved = kept
        self._reserved_count[topic] = len(self._heard[topic])

    def flush(self, now_ns: int) -> None:
        """Writes the complete reservations in publish order, then what was heard unannounced."""
        if self.missing() is not None:
            return
        while self._reserved:
            topic, stamp = self._reserved.popleft()
            self._reserved_count[topic] -= 1
            self._write(topic, self._heard[topic].popleft(), stamp)
        for topic in sorted(self._heard):
            queue = self._heard[topic]
            while queue:
                self.unannounced += 1
                self._write(topic, queue.popleft(), now_ns)

    def _write(self, topic: str, data: bytes, stamp_ns: int) -> None:
        self.written += 1
        if self._sink is not None:
            self._sink.write(topic, data, stamp_ns)
