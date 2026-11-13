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

"""Fan-out of daemon events to Server-Sent Events subscribers.

Wire form of one event: ``id: <seq>`` / ``event: <topic>`` / ``data: <json object>``. Sequence
numbers are daemon-wide and increase by one per published event; there is no replay, so a client
that reconnects resynchronises through the request API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

TOPIC_HELLO = "hello"
TOPIC_HEARTBEAT = "heartbeat"
TOPIC_DAEMON = "daemon"
TOPIC_SYSTEMS_CHANGED = "systems.changed"

SUBSCRIBER_QUEUE_SIZE = 256
RECONNECT_DELAY_MS = 3000


@dataclass(frozen=True)
class Event:
    seq: int
    topic: str
    data: dict[str, Any]

    def encode(self) -> bytes:
        payload = json.dumps(self.data, separators=(",", ":"), ensure_ascii=False)
        return f"id: {self.seq}\nevent: {self.topic}\ndata: {payload}\n\n".encode()


class Subscriber:
    """One open event stream; a subscriber that falls a full queue behind is dropped."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Optional[Event]] = asyncio.Queue(SUBSCRIBER_QUEUE_SIZE)

    def offer(self, event: Event) -> bool:
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False

    def end(self) -> None:
        """Replace whatever is queued with the end-of-stream sentinel."""
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)


class EventHub:
    """Daemon-wide event sequence and its subscribers, bounded in count."""

    def __init__(self, max_subscribers: int, heartbeat_s: float) -> None:
        self._max_subscribers = max_subscribers
        self._heartbeat_s = heartbeat_s
        self._subscribers: set[Subscriber] = set()
        self._seq = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def last_seq(self) -> int:
        return self._seq

    def subscribe(self) -> Optional[Subscriber]:
        if len(self._subscribers) >= self._max_subscribers:
            return None
        subscriber = Subscriber()
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    def next_event(self, topic: str, data: dict[str, Any]) -> Event:
        self._seq += 1
        return Event(self._seq, topic, data)

    def publish(self, topic: str, data: dict[str, Any]) -> Event:
        event = self.next_event(topic, data)
        for subscriber in list(self._subscribers):
            if not subscriber.offer(event):
                logger.warning("event subscriber fell %d events behind; dropped", SUBSCRIBER_QUEUE_SIZE)
                self._subscribers.discard(subscriber)
                subscriber.end()
        return event

    def close(self) -> None:
        """End every stream; each subscriber sees a None sentinel."""
        for subscriber in list(self._subscribers):
            subscriber.end()
        self._subscribers.clear()

    async def run_heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            self.publish(TOPIC_HEARTBEAT, {"time": time.time()})
