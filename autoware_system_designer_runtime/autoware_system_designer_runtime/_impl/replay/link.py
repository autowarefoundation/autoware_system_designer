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

"""Driver side of the replay link: one Unix socket connection per gated process.

The lines are those of ``autoware_system_designer_tracer/src/replay.c``. The server
keeps what a process registered (nodes, endpoints, timers) and hands every line to
the driver.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SOCKET_ENV = "ASD_REPLAY_SOCKET"
START_ENV = "ASD_REPLAY_START_NS"
WALL_TIMERS_ENV = "ASD_REPLAY_WALL_TIMERS"

# rmw_qos_*_policy_t values.
RELIABILITY_BEST_EFFORT = 2
DURABILITY_TRANSIENT_LOCAL = 1
HISTORY_KEEP_ALL = 2

PUBLISHER = "p"
SUBSCRIPTION = "s"
TIMER = "t"
# Timer on a wall clock the process kept: it runs outside the driver's time.
CLOCK_FREE = "f"


@dataclass(eq=False)
class Endpoint:
    """A publisher, subscription or timer of a gated process, with the driver's view of its state."""

    link: "ProcessLink"
    id: int
    kind: str
    node: str
    topic: str = ""
    reliability: int = 0
    durability: int = 0
    # Keep-last depth of a subscription; 0 without a bound.
    depth: int = 0
    period_ns: int = 0
    clock: str = ""
    alive: bool = True
    # In a wait set of its process: its callbacks pass the gate.
    waited: bool = False
    ready: bool = False
    # Subscription: messages published to it and not taken.
    outstanding: int = 0
    # Timer: next call time in ROS time, -1 when canceled.
    next_ns: int = -1
    # Publisher: subscriptions it matched at the last query.
    matched: int = 0
    # Subscription: deliveries are waited for. Cleared when its publishers do not reach it.
    expect: bool = True
    misses: int = 0
    # Timer: due and never ready; left out of the schedule until it reports a new call time.
    stuck: bool = False

    @property
    def gated(self) -> bool:
        return self.kind == SUBSCRIPTION or (self.kind == TIMER and self.clock != CLOCK_FREE)

    def label(self) -> str:
        if self.kind == TIMER:
            return f"timer {self.period_ns / 1e6:g} ms of {self.node}"
        return f"{'subscription' if self.kind == SUBSCRIPTION else 'publisher'} {self.topic} of {self.node}"


@dataclass(eq=False)
class ProcessLink:
    pid: int
    writer: asyncio.StreamWriter = field(repr=False)
    endpoints: dict[int, Endpoint] = field(default_factory=dict)
    nodes: list[str] = field(default_factory=list)
    connected: bool = True
    time_ack: int = 0
    match_ack: int = 0

    def send(self, line: str) -> None:
        if self.connected:
            self.writer.write((line + "\n").encode())


# (link, line kind, fields after the kind). "bye" follows the last line of a process.
LineHandler = Callable[[ProcessLink, str, list[str]], None]


class LinkServer:
    def __init__(self, path: Path, handler: LineHandler) -> None:
        self._path = Path(path)
        self._handler = handler
        self._server: Optional[asyncio.AbstractServer] = None
        self.links: dict[int, ProcessLink] = {}

    @property
    def path(self) -> Path:
        return self._path

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._serve, path=str(self._path))

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for link in self.links.values():
            if link.connected:
                link.connected = False
                link.writer.close()
        self._path.unlink(missing_ok=True)

    def broadcast(self, line: str) -> None:
        for link in self.links.values():
            link.send(line)

    def endpoints(self) -> list[Endpoint]:
        return [ep for link in self.links.values() for ep in link.endpoints.values() if ep.alive]

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        link: Optional[ProcessLink] = None
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                fields = raw.decode(errors="replace").rstrip("\n").split("\t")
                kind, fields = fields[0], fields[1:]
                if link is None:
                    if kind != "hello" or not fields:
                        continue
                    link = ProcessLink(pid=int(fields[0]), writer=writer)
                    self.links[link.pid] = link
                try:
                    self._register(link, kind, fields)
                    self._handler(link, kind, fields)
                except (ValueError, IndexError, KeyError) as exc:
                    logger.warning("replay link of pid %d: bad line %s %s: %s", link.pid, kind, fields, exc)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if link is not None and link.connected:
                link.connected = False
                for ep in link.endpoints.values():
                    ep.alive = False
                self._handler(link, "bye", [])
            writer.close()

    @staticmethod
    def _register(link: ProcessLink, kind: str, fields: list[str]) -> None:
        if kind == "node":
            link.nodes.append(fields[0])
        elif kind == "ep":
            ident, ep_kind, node, topic, reliability, durability, history, depth = fields[:8]
            link.endpoints[int(ident)] = Endpoint(
                link=link,
                id=int(ident),
                kind=ep_kind,
                node=node,
                topic=topic,
                reliability=int(reliability),
                durability=int(durability),
                depth=0 if int(history) == HISTORY_KEEP_ALL else max(int(depth), 1),
            )
        elif kind == "timer":
            ident, node, period, clock, next_ns = fields[:5]
            link.endpoints[int(ident)] = Endpoint(
                link=link,
                id=int(ident),
                kind=TIMER,
                node=node,
                period_ns=int(period),
                clock=clock,
                next_ns=int(next_ns),
            )
        elif kind == "fini":
            ep = link.endpoints.get(int(fields[0]))
            if ep is not None:
                ep.alive = False
