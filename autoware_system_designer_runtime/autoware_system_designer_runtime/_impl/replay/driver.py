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

"""Replay driver: the single owner of time and input of a gated scope.

The driver holds every callback of the scope at the gate of its process and moves in
steps. A step is one of: a granted callback, a bag message published, the clocks set
to the next timer expiry. After each it waits until the scope is settled, then takes
the next in this order:

1. a ready callback, the lowest in the tie order, until none is ready;
2. the earliest timer expiry, when it is not after the next bag message;
3. the next bag message, with the clocks at its recorded time.

Settled means: no callback runs, every process holds the driver's time, every message
published to a waited subscription shows as ready work there, every timer due is ready,
and every recorded publish was heard. All of it is counted from what the tracer
observes; the declared event chains play no part. Publishes, takes and timer fires of
a thread without the grant break the order and are counted as violations.

Until the first step the subscriptions on the topics every process carries pass the gate: the
events of the startup are handled as they arrive and leave no backlog to step through.

A callback that waits on another callback of its process, as on a transform listener, holds
the scope: that callback is gated too. Once a callback has run for `nested_after`, the ready
callbacks of its process on the nested callback topics are granted one at a time while it runs.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol

from ...special_topics import special_topics
from .link import PROTOCOL, PUBLISHER, SUBSCRIPTION, TIMER, Endpoint, LinkServer, ProcessLink
from .recorder import OutputRecorder

logger = logging.getLogger(__name__)

MAX_ANOMALIES = 200
# Driver settings the driver reads itself; fields of DriverOptions.
DRIVER_OPTION_NAMES = (
    "startup_timeout",
    "startup_settle",
    "match_timeout",
    "callback_timeout",
    "nested_after",
    "delivery_timeout",
    "burst_settle",
    "miss_limit",
)


class ReplayError(RuntimeError):
    """The replay cannot continue; ``member`` names the scope member at fault, where one is."""

    def __init__(self, message: str, member: Optional[str] = None) -> None:
        super().__init__(message)
        self.member = member


class Source(Protocol):
    def peek(self) -> Optional[tuple[int, str, bytes]]: ...

    def pop(self) -> tuple[int, str, bytes]: ...


class ReplayIO(Protocol):
    """The driver's own endpoints: publishers of the fed topics and the recorder's subscriptions."""

    # Topic and message type of every bag topic the driver publishes.
    fed_topics: Mapping[str, str]

    async def prepare(self, driver: "ReplayDriver") -> None: ...

    async def publish(self, topic: str, data: bytes) -> None: ...

    async def subscription_count(self, topic: str) -> int: ...


@dataclass
class DriverOptions:
    start_ns: int
    # Driver settings, see config.DRIVER_SETTINGS.
    startup_timeout: float
    startup_settle: float
    match_timeout: float
    callback_timeout: float
    nested_after: float
    delivery_timeout: float
    burst_settle: float
    miss_limit: int
    # Bag messages after it are not delivered.
    end_ns: Optional[int] = None
    # Ties go to a seeded draw among the ready callbacks; None takes the first in design order.
    seed: Optional[int] = None
    max_callbacks_per_instant: int = 100000
    # Stops after the scope is up and matched, without a step.
    startup_only: bool = False
    # Receives one line per step and per line of a process, for a step log.
    step_log: Optional[Callable[[str], None]] = None
    # Receives the driver time while the bag is stepped through, at most every progress_interval.
    progress: Optional[Callable[[int], None]] = None
    progress_interval: float = 1.0


@dataclass
class ReplayResult:
    start_ns: int = 0
    end_ns: int = 0
    wall_s: float = 0.0
    messages: int = 0
    callbacks: int = 0
    # Callbacks on the topics every process carries; their count follows the startup, not the bag.
    infra_callbacks: int = 0
    # Callbacks on the nested callback topics run beside a callback that waited on them.
    nested_callbacks: int = 0
    timer_fires: int = 0
    publishes: int = 0
    clock_steps: int = 0
    outputs: int = 0
    # Publishes on recorded topics the recorder never heard.
    lost_outputs: int = 0
    violations: list[dict[str, Any]] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class ReplayDriver:
    def __init__(
        self,
        server: LinkServer,
        source: Source,
        io: ReplayIO,
        recorder: OutputRecorder,
        options: DriverOptions,
        *,
        node_order: Mapping[str, int],
        expected_nodes: frozenset[str] = frozenset(),
        member_rank: Callable[[str], int] = lambda member: 0,
    ) -> None:
        self._server = server
        self._source = source
        self._io = io
        self._recorder = recorder
        self._options = options
        self._node_order = dict(node_order)
        self._expected_nodes = set(expected_nodes)
        self._member_rank = member_rank
        self._special = special_topics()
        self._random = random.Random(options.seed) if options.seed is not None else None
        self._changed = asyncio.Event()
        self._now = options.start_ns
        self._time_seq = 0
        self._match_seq = 0
        self._running: Optional[Endpoint] = None
        # Nested callback granted beside the running one.
        self._nested: Optional[Endpoint] = None
        self._stepping = False
        self._finished = False
        self._error: Optional[str] = None
        self._seen_nodes: set[str] = set()
        self._last_registration = 0.0
        self._block: Optional[tuple[str, object]] = None
        self._block_since = 0.0
        self._violations: Counter = Counter()
        self._log = options.step_log
        self.result = ReplayResult(start_ns=options.start_ns)
        self._registrations = {
            "hello": self._on_hello,
            "node": self._on_node,
            "ep": self._on_endpoint,
            "timer": self._on_registration,
            "fini": self._on_fini,
        }
        self._endpoint_lines = {
            "waited": self._on_waited,
            "ready": self._on_ready,
            "unready": self._on_unready,
            "next": self._on_next,
            "match": self._on_match,
            "done": self._on_done,
            "take": self._on_take,
            "fire": self._on_fire,
            "pub": self._on_publish,
            "lost": self._on_lost,
        }

    # ---- link events -------------------------------------------------------------

    def handle(self, link: ProcessLink, kind: str, fields: list[str]) -> None:
        """One line of a process; the server has already registered what it creates."""
        self._changed.set()
        if self._log is not None:
            self._log(f"< {link.id} {kind} {' '.join(fields)}")
        registration = self._registrations.get(kind)
        if registration is not None:
            self._last_registration = time.monotonic()
            registration(link, fields)
        elif kind == "bye":
            self._on_bye(link)
        elif kind == "time":
            link.time_ack = int(fields[0])
        elif kind == "matched":
            link.match_ack = int(fields[0])
        elif kind in self._endpoint_lines:
            ep = link.endpoints.get(int(fields[0]))
            if ep is not None:
                self._endpoint_lines[kind](ep, fields)

    def _on_registration(self, link: ProcessLink, fields: list[str]) -> None:
        return None

    def _on_hello(self, link: ProcessLink, fields: list[str]) -> None:
        if link.protocol != PROTOCOL:
            self._error = (
                f"{link.label()} speaks link protocol {link.protocol}, the driver {PROTOCOL}: "
                "the tracer and the runtime are of different builds"
            )
            return
        if link.missing_hooks:
            self._anomaly(
                f"{link.label()}: rclcpp hook(s) {', '.join(link.missing_hooks)} did not resolve; "
                "its callbacks end at the next wait of their thread and intra-process delivery may stay on"
            )
        if self._time_seq:
            link.send(f"time\t{self._time_seq}\t{self._now}")

    def _on_node(self, link: ProcessLink, fields: list[str]) -> None:
        self._seen_nodes.add(fields[0])

    def _on_endpoint(self, link: ProcessLink, fields: list[str]) -> None:
        self._pass_until_stepping(link.endpoints.get(int(fields[0])))

    def _on_fini(self, link: ProcessLink, fields: list[str]) -> None:
        if self._running is not None and not self._running.alive:
            self._running = None
        if self._nested is not None and not self._nested.alive:
            self._nested = None

    def _on_bye(self, link: ProcessLink) -> None:
        if self._running is not None and self._running.link is link:
            self._running = None
        if self._nested is not None and self._nested.link is link:
            self._nested = None
        if not self._finished and link.endpoints:
            self._error = f"{link.label()} left during the replay"

    def _on_waited(self, ep: Endpoint, fields: list[str]) -> None:
        ep.waited = True

    def _on_ready(self, ep: Endpoint, fields: list[str]) -> None:
        ep.ready = True

    def _on_unready(self, ep: Endpoint, fields: list[str]) -> None:
        ep.ready = False
        self._ended(ep)

    def _on_next(self, ep: Endpoint, fields: list[str]) -> None:
        ep.next_ns = int(fields[1])
        ep.stuck = False

    def _on_match(self, ep: Endpoint, fields: list[str]) -> None:
        ep.matched = int(fields[1])

    def _on_done(self, ep: Endpoint, fields: list[str]) -> None:
        self._ended(ep)

    def _on_take(self, ep: Endpoint, fields: list[str]) -> None:
        ep.outstanding = max(ep.outstanding - 1, 0)
        ep.misses = 0
        ep.ready = False
        self._ordered(ep, fields[1], "take")

    def _on_fire(self, ep: Endpoint, fields: list[str]) -> None:
        self.result.timer_fires += self._stepping
        ep.ready = False
        self._ordered(ep, fields[1], "timer")

    def _on_publish(self, ep: Endpoint, fields: list[str]) -> None:
        self._ordered(ep, fields[1], "publish")
        if self._stepping:
            self.result.publishes += 1
            self._published(ep.topic)

    def _on_lost(self, ep: Endpoint, fields: list[str]) -> None:
        self._anomaly(f"a message published to {ep.label()} never arrived")

    def _pass_until_stepping(self, ep: Optional[Endpoint]) -> None:
        if ep is None or ep.kind != SUBSCRIPTION or ep.topic not in self._special.process:
            return
        if self._stepping or self._finished:
            return
        ep.passing = True
        ep.link.send(f"pass\t{ep.id}\t1")

    def _gate_passing(self) -> None:
        for ep in self._server.endpoints():
            if ep.passing:
                ep.passing = False
                ep.ready = False
                ep.link.send(f"pass\t{ep.id}\t0")

    def _ended(self, ep: Endpoint) -> None:
        if self._nested is ep:
            self._nested = None
        elif self._running is ep:
            self._running = None

    def _ordered(self, ep: Endpoint, gated: str, what: str) -> None:
        """Counts a publish, take or timer fire made outside the granted callback."""
        if gated == "1" or not self._stepping or ep.topic in self._special.process:
            return
        self._violations[(what, ep.node, ep.topic or f"{ep.period_ns / 1e6:g} ms")] += 1

    def _published(self, topic: str) -> None:
        for ep in self._server.endpoints():
            if ep.kind != SUBSCRIPTION or ep.topic != topic or not ep.expect:
                continue
            if ep.depth == 0 or ep.outstanding < ep.depth:
                ep.outstanding += 1
            ep.link.send(f"expect\t{ep.id}")
        self._recorder.published(topic, self._now)

    def _anomaly(self, text: str) -> None:
        logger.warning("replay: %s", text)
        if len(self.result.anomalies) < MAX_ANOMALIES:
            self.result.anomalies.append(text)

    # ---- settling ------------------------------------------------------------------

    def _blocker(self) -> Optional[tuple[str, object]]:
        """What the scope still owes before the next step, None once it is settled."""
        if self._running is not None:
            return ("callback", self._running)
        if self._nested is not None:
            return ("callback", self._nested)
        for link in self._server.links.values():
            if link.connected and link.time_ack < self._time_seq:
                return ("clock", link)
        for ep in self._server.endpoints():
            if not ep.waited or ep.ready:
                continue
            if ep.kind == SUBSCRIPTION and ep.outstanding > 0 and ep.expect:
                return ("delivery", ep)
            if ep.kind == TIMER and ep.gated and not ep.stuck and 0 <= ep.next_ns <= self._now:
                return ("timer", ep)
        topic = self._recorder.missing()
        return ("record", topic) if topic is not None else None

    def _give_up(self, kind: str, subject: object) -> None:
        if kind == "callback":
            assert isinstance(subject, Endpoint)
            raise ReplayError(
                f"the callback of {subject.label()} did not return within {self._options.callback_timeout:g} s; "
                "a call that waits on ROS time or on another callback blocks while the driver holds both",
                member=subject.link.member or None,
            )
        if kind == "clock":
            assert isinstance(subject, ProcessLink)
            raise ReplayError(f"{subject.label()} did not apply the clock step", member=subject.member or None)
        if kind == "delivery":
            assert isinstance(subject, Endpoint)
            subject.outstanding = 0
            subject.misses += 1
            self._anomaly(f"a message published to {subject.label()} did not show up as work")
            if subject.misses >= self._options.miss_limit:
                subject.expect = False
                self._anomaly(f"{subject.label()} is no longer waited for")
        elif kind == "timer":
            assert isinstance(subject, Endpoint)
            subject.stuck = True
            self._anomaly(f"{subject.label()} is due and never became ready")
        else:
            assert isinstance(subject, str)
            self._recorder.forgive(subject)
            self._anomaly(f"a publish on {subject} was not heard by the recorder")

    async def _settle(self) -> None:
        while True:
            if self._error is not None:
                raise ReplayError(self._error)
            self._changed.clear()
            block = self._blocker()
            if block is None:
                self._block = None
                self._recorder.flush(self._now)
                return
            now = time.monotonic()
            if block != self._block:
                self._block, self._block_since = block, now
            limit = self._options.callback_timeout if block[0] == "callback" else self._options.delivery_timeout
            remaining = self._block_since + limit - now
            if remaining <= 0:
                self._block = None
                self._give_up(*block)
                continue
            if block[0] == "callback" and self._running is not None and self._nested is None:
                nested_at = self._block_since + self._options.nested_after
                if now >= nested_at:
                    self._grant_nested(self._running)
                else:
                    remaining = min(remaining, nested_at - now)
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    def _grant_nested(self, running: Endpoint) -> None:
        """Grants the first ready nested callback of the process of *running*, if any."""
        ready = [
            ep
            for ep in running.link.endpoints.values()
            if ep.gated and ep.ready and ep.alive and ep.topic in self._special.nested_callbacks
        ]
        if not ready:
            return
        ep = min(ready, key=self._tie_key)
        ep.ready = False
        self._nested = ep
        ep.link.send(f"grant\t{ep.id}")
        if self._log is not None:
            self._log(f"> grant {ep.link.id} {ep.id} {ep.label()} beside {running.label()}")
        self.result.nested_callbacks += 1

    # ---- steps ---------------------------------------------------------------------

    def _tie_key(self, ep: Endpoint) -> tuple[int, int, int, int]:
        return (
            self._node_order.get(ep.node, len(self._node_order)),
            self._member_rank(ep.link.member),
            ep.link.id if ep.node not in self._node_order else 0,
            ep.id,
        )

    def _choose(self, ready: list[Endpoint]) -> Endpoint:
        """The next callback among the ready ones. Callbacks on the topics every rcl process carries
        go first and take no draw: their number follows the startup, and the draws must not."""
        ready.sort(key=self._tie_key)
        infra = [ep for ep in ready if ep.topic in self._special.process]
        if infra:
            return infra[0]
        return self._random.choice(ready) if self._random is not None else ready[0]

    async def _drain(self) -> None:
        """Runs ready callbacks one at a time until the scope is settled with none left."""
        granted = 0
        while True:
            await self._settle()
            ready = [ep for ep in self._server.endpoints() if ep.gated and ep.ready]
            if not ready:
                return
            ep = self._choose(ready)
            if ep.kind == SUBSCRIPTION and ep.outstanding > 1 and self._options.burst_settle > 0:
                await asyncio.sleep(self._options.burst_settle)
                if not (ep.alive and ep.ready):
                    continue
            ep.ready = False
            self._running = ep
            ep.link.send(f"grant\t{ep.id}")
            if self._log is not None:
                self._log(f"> grant {ep.link.id} {ep.id} {ep.label()}")
            if ep.topic in self._special.process:
                self.result.infra_callbacks += 1
            else:
                self.result.callbacks += 1
            granted += 1
            if granted > self._options.max_callbacks_per_instant:
                raise ReplayError(
                    f"{granted} callbacks ran at ROS time {self._now} without the scope settling; "
                    f"last: {ep.label()}",
                    member=ep.link.member or None,
                )

    def _set_time(self, ns: int) -> None:
        self._now = ns
        self._time_seq += 1
        self.result.clock_steps += 1
        if self._log is not None:
            self._log(f"> time {ns}")
        self._server.broadcast(f"time\t{self._time_seq}\t{ns}")

    def _next_timer(self) -> Optional[int]:
        times = [
            ep.next_ns
            for ep in self._server.endpoints()
            if ep.kind == TIMER and ep.gated and ep.waited and not ep.stuck and ep.next_ns > self._now
        ]
        return min(times) if times else None

    # ---- startup -------------------------------------------------------------------

    async def _startup(self) -> None:
        options = self._options
        deadline = time.monotonic() + options.startup_timeout
        self._last_registration = time.monotonic()
        while True:
            if self._error is not None:
                raise ReplayError(self._error)
            missing = sorted(self._expected_nodes - self._seen_nodes)
            quiet = time.monotonic() - self._last_registration
            if not missing and self._server.links and quiet >= options.startup_settle:
                break
            if time.monotonic() > deadline:
                what = f"node(s) {', '.join(missing)} did not start" if missing else "the scope kept creating endpoints"
                raise ReplayError(f"scope not up within {options.startup_timeout:g} s: {what}")
            await asyncio.sleep(0.1)
        await self._io.prepare(self)
        await self._await_matched()

    async def _query_matched(self) -> None:
        self._match_seq += 1
        links = [link for link in self._server.links.values() if link.connected]
        for link in links:
            link.send(f"match\t{self._match_seq}")
        deadline = time.monotonic() + 5.0
        while any(link.connected and link.match_ack < self._match_seq for link in links):
            if self._error is not None:
                raise ReplayError(self._error)
            if time.monotonic() > deadline:
                raise ReplayError("a process did not answer the matched-subscription query")
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), 0.2)
            except asyncio.TimeoutError:
                pass

    async def _shortfalls(self, fed: Mapping[str, str]) -> dict[str, str]:
        """Topics whose publishers have not matched every subscription of the scope yet."""
        await self._query_matched()
        readers: Counter = Counter()
        for ep in self._server.endpoints():
            if ep.kind == SUBSCRIPTION:
                readers[ep.topic] += 1
        short: dict[str, str] = {}
        for ep in self._server.endpoints():
            if ep.kind != PUBLISHER or ep.topic in self._special.process:
                continue
            needed = readers[ep.topic] + (ep.topic in self._recorder.topics)
            if ep.matched < needed:
                short[ep.topic] = f"{ep.label()} matched {ep.matched} of {needed} subscriptions"
        for topic in fed:
            count = await self._io.subscription_count(topic)
            if count < readers[topic]:
                short[topic] = f"the driver's publisher of {topic} matched {count} of {readers[topic]} subscriptions"
        return short

    async def _await_matched(self) -> None:
        fed = self._io.fed_topics
        deadline = time.monotonic() + self._options.match_timeout
        while True:
            short = await self._shortfalls(fed)
            if not short:
                return
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(0.2)
        for topic, text in sorted(short.items()):
            self._anomaly(f"{text}; deliveries on {topic} are not waited for")
            for ep in self._server.endpoints():
                if ep.kind == SUBSCRIPTION and ep.topic == topic:
                    ep.expect = False

    # ---- run -------------------------------------------------------------------------

    def endpoints(self) -> list[Endpoint]:
        return self._server.endpoints()

    def wake(self) -> None:
        """Something the settling waits on has changed outside the links."""
        self._changed.set()

    async def run(self) -> ReplayResult:
        started = time.monotonic()
        try:
            await self._startup()
            if self._options.startup_only:
                return self.result
            await self._step_through()
        finally:
            self._stepping = False
            self._finished = True
            self._server.broadcast("free")
            result = self.result
            result.end_ns = self._now
            result.wall_s = round(time.monotonic() - started, 3)
            result.outputs = self._recorder.written
            result.lost_outputs = self._recorder.lost
            result.violations = [
                {"kind": kind, "node": node, "endpoint": endpoint, "count": count}
                for (kind, node, endpoint), count in sorted(self._violations.items())
            ]
            if self._recorder.unannounced:
                result.anomalies.append(
                    f"{self._recorder.unannounced} recorded message(s) had no observed publish "
                    "(intra-process or untraced)"
                )
        return self.result

    async def _step_through(self) -> None:
        options = self._options
        for ep in self._server.endpoints():
            ep.outstanding = 0
        self._recorder.start()
        self._stepping = True
        self._gate_passing()
        self._set_time(self._now)
        await self._drain()
        progress = time.monotonic()
        reported = progress
        if options.progress is not None:
            options.progress(self._now)
        while True:
            if options.progress is not None and time.monotonic() - reported >= options.progress_interval:
                reported = time.monotonic()
                options.progress(self._now)
            message = self._source.peek()
            if message is None or (options.end_ns is not None and message[0] > options.end_ns):
                break
            timer = self._next_timer()
            if timer is not None and timer <= max(message[0], self._now):
                self._set_time(timer)
                await self._drain()
                continue
            stamp, topic, data = self._source.pop()
            if stamp > self._now:
                self._set_time(stamp)
                await self._settle()
            if self._log is not None:
                self._log(f"> publish {topic}")
            await self._io.publish(topic, data)
            self.result.messages += 1
            self._published(topic)
            await self._drain()
            if time.monotonic() - progress >= 5.0:
                progress = time.monotonic()
                logger.info(
                    "replay: %.3f s of ROS time, %d messages in, %d callbacks, %d outputs",
                    (self._now - options.start_ns) / 1e9,
                    self.result.messages,
                    self.result.callbacks,
                    self._recorder.written,
                )
        await self._settle()
        if options.progress is not None:
            options.progress(self._now)
