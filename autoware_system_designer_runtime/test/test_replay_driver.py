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

"""The replay driver steps a scope of fake gated processes that speak the tracer's side of the link."""

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from autoware_system_designer_runtime._impl.replay.config import ReplayConfig
from autoware_system_designer_runtime._impl.replay.driver import (
    DRIVER_OPTION_NAMES,
    DriverOptions,
    ReplayDriver,
    ReplayError,
)
from autoware_system_designer_runtime._impl.replay.link import PROTOCOL, LinkServer
from autoware_system_designer_runtime._impl.replay.recorder import OutputRecorder

MS = 1_000_000
T0 = 1_000_000_000


class FakeProcess:
    """One gated process: subscriptions that publish when taken, timers that publish when fired.

    A message published to a subscription makes it ready; a clock step makes the timers due ready.
    """

    def __init__(self, pid: int, world: "World", protocol: int = PROTOCOL, hooks: str = "ok") -> None:
        self.pid = pid
        self._world = world
        self._lines: list[str] = [f"hello\t{protocol}\tmember{pid}\t{pid}\t{hooks}"]
        self._subs: dict[int, dict] = {}
        self._timers: dict[int, dict] = {}
        self._pubs: dict[int, str] = {}
        self._next_id = 1
        self._writer = None
        # Callbacks that never report their end, by endpoint id.
        self.stuck: set[int] = set()
        self.ungated_publish_on_take: dict[int, int] = {}
        # Subscriptions another thread reports ready between the grant and the take.
        self.ready_again_on_take: set[int] = set()
        # Callbacks that end once another subscription of the process ran: waiting id -> awaited id.
        self.waits_on: dict[int, int] = {}

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    def node(self, fqn: str) -> "FakeProcess":
        self._lines.append(f"node\t{fqn}")
        self._node = fqn
        return self

    def pub(self, topic: str) -> int:
        ident = self._id()
        self._pubs[ident] = topic
        self._lines.append(f"ep\t{ident}\tp\t{self._node}\t{topic}\treliable\tvolatile\tkeep_last\t10")
        return ident

    def sub(self, topic: str, publishes: tuple = (), depth: int = 10) -> int:
        ident = self._id()
        self._subs[ident] = {"topic": topic, "publishes": publishes, "queued": 0, "depth": depth}
        self._lines.append(f"ep\t{ident}\ts\t{self._node}\t{topic}\treliable\tvolatile\tkeep_last\t{depth}")
        self._lines.append(f"waited\t{ident}")
        return ident

    def timer(self, period_ns: int, publishes: tuple = ()) -> int:
        ident = self._id()
        self._timers[ident] = {"period": period_ns, "next": T0 + period_ns, "publishes": publishes, "ready": False}
        self._lines.append(f"timer\t{ident}\t{self._node}\t{period_ns}\tr\t{T0 + period_ns}")
        self._lines.append(f"waited\t{ident}")
        return ident

    def send(self, line: str) -> None:
        self._writer.write((line + "\n").encode())

    def _publish(self, ident: int, gated: int = 1) -> None:
        self.send(f"pub\t{ident}\t{gated}")
        self._world.delivered(self._pubs[ident])

    async def run(self, path: Path) -> None:
        reader, self._writer = await asyncio.open_unix_connection(str(path))
        for line in self._lines:
            self.send(line)
        while True:
            raw = await reader.readline()
            if not raw:
                return
            fields = raw.decode().rstrip("\n").split("\t")
            kind = fields[0]
            if kind == "time":
                now = int(fields[2])
                for ident, timer in self._timers.items():
                    if timer["next"] <= now and not timer["ready"]:
                        timer["ready"] = True
                        self.send(f"ready\t{ident}")
                self.send(f"time\t{fields[1]}")
            elif kind == "match":
                for ident in self._pubs:
                    self.send(f"match\t{ident}\t99")
                self.send(f"matched\t{fields[1]}")
            elif kind == "expect":
                sub = self._subs[int(fields[1])]
                sub["queued"] = min(sub["queued"] + 1, sub["depth"])
                if sub["queued"] == 1:
                    self.send(f"ready\t{fields[1]}")
            elif kind == "grant":
                ident = int(fields[1])
                self._world.granted.append((self.pid, ident))
                if ident in self._subs:
                    sub = self._subs[ident]
                    sub["queued"] -= 1
                    if ident in self.ready_again_on_take:
                        self.send(f"ready\t{ident}")
                    self.send(f"take\t{ident}\t1")
                    publishes = sub["publishes"]
                    if ident in self.ungated_publish_on_take:
                        self._publish(self.ungated_publish_on_take[ident], gated=0)
                else:
                    timer = self._timers[ident]
                    timer["ready"] = False
                    timer["next"] += timer["period"]
                    self.send(f"fire\t{ident}\t1")
                    self.send(f"next\t{ident}\t{timer['next']}")
                    publishes = timer["publishes"]
                for pub in publishes:
                    self._publish(pub)
                if ident in self.stuck:
                    continue
                if ident in self.waits_on:
                    self.send(f"ready\t{self.waits_on[ident]}")
                    continue
                self.send(f"done\t{ident}")
                for waiting, awaited in self.waits_on.items():
                    if awaited == ident:
                        self.send(f"done\t{waiting}")
                if ident in self._subs and self._subs[ident]["queued"] > 0:
                    self.send(f"ready\t{ident}")
            elif kind == "pass":
                self._world.passed.append((self.pid, int(fields[1]), int(fields[2]), len(self._world.granted)))
            elif kind == "free":
                self._world.freed.add(self.pid)


class World:
    """The test's side of a replay: the bag, the driver's own endpoints and what the recorder hears."""

    def __init__(self, messages, recorded=()):
        self.socket_dir = Path(tempfile.mkdtemp(prefix="asd_test_"))
        self.path = self.socket_dir / "driver.sock"
        self._messages = list(messages)
        self.fed_topics = {topic: "std_msgs/msg/String" for _, topic, _ in messages}
        self.written: list[tuple[str, int]] = []
        self.recorder = OutputRecorder({topic: "std_msgs/msg/String" for topic in recorded}, self)
        self.granted: list[tuple[int, int]] = []
        self.freed: set[int] = set()
        # (pid, endpoint id, on, callbacks granted so far) of every pass command.
        self.passed: list[tuple[int, int, int, int]] = []
        self.published: list[str] = []
        self.driver = None

    # source
    def peek(self):
        return self._messages[0] if self._messages else None

    def pop(self):
        return self._messages.pop(0)

    # driver endpoints
    async def prepare(self, driver) -> None:
        return None

    async def publish(self, topic: str, data: bytes) -> None:
        self.published.append(topic)

    async def subscription_count(self, topic: str) -> int:
        return 99

    # recorder sink and the delivery of a recorded publish
    def write(self, topic: str, data: bytes, stamp_ns: int) -> None:
        self.written.append((topic, stamp_ns - T0))

    def delivered(self, topic: str) -> None:
        self.recorder.heard(topic, b"data")
        self.driver.wake()


def replay(world: World, processes, *, node_order, expected=(), **options):
    async def scenario():
        server = LinkServer(world.path, lambda link, kind, fields: world.driver.handle(link, kind, fields))
        world.driver = ReplayDriver(
            server,
            world,
            world,
            world.recorder,
            DriverOptions(
                **{
                    **{name: ReplayConfig().driver[name] for name in DRIVER_OPTION_NAMES},
                    "start_ns": T0,
                    "startup_settle": 0.05,
                    "startup_timeout": 5.0,
                    "burst_settle": 0.0,
                    **options,
                }
            ),
            node_order=node_order,
            expected_nodes=frozenset(expected or node_order),
            member_rank=lambda member: int(member[len("member") :]),
        )
        await server.start()
        tasks = [asyncio.ensure_future(process.run(world.path)) for process in processes]
        try:
            return await world.driver.run()
        finally:
            await asyncio.sleep(0.05)
            await server.close()
            for task in tasks:
                task.cancel()
            shutil.rmtree(world.socket_dir, ignore_errors=True)

    return asyncio.run(scenario())


def chain(world: World):
    """/a turns /in into /mid; /b turns /mid into /out and publishes /tick every 100 ms."""
    a = FakeProcess(1, world).node("/a")
    a.sub("/in", publishes=(a.pub("/mid"),))
    b = FakeProcess(2, world).node("/b")
    b.sub("/mid", publishes=(b.pub("/out"),))
    b.timer(100 * MS, publishes=(b.pub("/tick"),))
    return [a, b]


def test_outputs_are_recorded_at_the_driver_time_of_their_cause():
    world = World(
        [(T0 + 50 * MS, "/in", b"1"), (T0 + 150 * MS, "/in", b"2"), (T0 + 250 * MS, "/in", b"3")], ["/out", "/tick"]
    )

    result = replay(world, chain(world), node_order={"/a": 0, "/b": 1})

    assert world.written == [
        ("/out", 50 * MS),
        ("/tick", 100 * MS),
        ("/out", 150 * MS),
        ("/tick", 200 * MS),
        ("/out", 250 * MS),
    ]
    assert (result.messages, result.callbacks, result.timer_fires, result.outputs) == (3, 8, 2, 5)
    assert result.end_ns == T0 + 250 * MS
    assert result.violations == [] and result.anomalies == []
    assert world.freed == {1, 2}


def test_a_timer_due_with_a_message_fires_before_the_message_is_delivered():
    world = World([(T0 + 100 * MS, "/in", b"1")], ["/out", "/tick"])

    replay(world, chain(world), node_order={"/a": 0, "/b": 1})

    assert world.written == [("/tick", 100 * MS), ("/out", 100 * MS)]


def test_messages_after_the_end_are_not_delivered():
    world = World([(T0 + 50 * MS, "/in", b"1"), (T0 + 350 * MS, "/in", b"2")], ["/out", "/tick"])

    result = replay(world, chain(world), node_order={"/a": 0, "/b": 1}, end_ns=T0 + 200 * MS)

    assert world.written == [("/out", 50 * MS)]
    assert result.messages == 1


def test_progress_reports_the_driver_time_from_the_first_step_to_the_last():
    world = World([(T0 + 50 * MS, "/in", b"1"), (T0 + 150 * MS, "/in", b"2")], ["/out", "/tick"])
    reported = []

    replay(world, chain(world), node_order={"/a": 0, "/b": 1}, progress=reported.append, progress_interval=0.0)

    assert reported[0] == T0 and reported[-1] == T0 + 150 * MS
    assert reported == sorted(reported)


def fan_in(world: World):
    """/x and /y both hear /in; /z hears what each of them publishes."""
    processes = []
    for pid, name in ((1, "/x"), (2, "/y")):
        process = FakeProcess(pid, world).node(name)
        process.sub("/in", publishes=(process.pub(f"{name}/out"),))
        processes.append(process)
    z = FakeProcess(3, world).node("/z")
    z.sub("/x/out")
    z.sub("/y/out")
    return processes + [z]


def test_callbacks_ready_together_run_in_design_order():
    world = World([(T0 + 10 * MS, "/in", b"1")])
    replay(world, fan_in(world), node_order={"/x": 0, "/y": 1, "/z": 2})
    # /x, then /y, then /z for each of its inputs in creation order.
    assert world.granted == [(1, 2), (2, 2), (3, 1), (3, 2)]

    world = World([(T0 + 10 * MS, "/in", b"1")])
    replay(world, fan_in(world), node_order={"/y": 0, "/z": 1, "/x": 2})
    # /z runs on /y's output before /x has run.
    assert world.granted == [(2, 2), (3, 2), (1, 2), (3, 1)]


def test_a_seed_picks_another_order_and_repeats_it():
    orders = []
    for seed in (3, 3, 4, 5, 6):
        world = World([(T0 + 10 * MS * n, "/in", b"1") for n in range(1, 6)])
        replay(world, fan_in(world), node_order={"/x": 0, "/y": 1, "/z": 2}, seed=seed)
        orders.append(world.granted)

    assert orders[0] == orders[1]
    assert len({tuple(order) for order in orders}) > 1


def test_a_queue_of_several_messages_runs_one_callback_each():
    world = World([(T0 + 10 * MS, "/in", b"1")], ["/out"])
    a = FakeProcess(1, world).node("/a")
    mid = a.pub("/mid")
    a.sub("/in", publishes=(mid, mid, mid))
    b = FakeProcess(2, world).node("/b")
    b.sub("/mid", publishes=(b.pub("/out"),))

    result = replay(world, [a, b], node_order={"/a": 0, "/b": 1})

    assert world.written == [("/out", 10 * MS)] * 3
    assert result.callbacks == 4


def test_a_subscription_reported_ready_during_its_callback_is_not_granted_again():
    world = World([(T0 + 10 * MS, "/in", b"1")])
    a = FakeProcess(1, world).node("/a")
    sub = a.sub("/in")
    a.ready_again_on_take.add(sub)

    result = replay(world, [a], node_order={"/a": 0}, callback_timeout=0.5)

    assert world.granted == [(1, sub)]
    assert result.callbacks == 1


def test_a_publish_outside_the_granted_callback_is_a_violation():
    world = World([(T0 + 10 * MS, "/in", b"1")])
    a = FakeProcess(1, world).node("/a")
    stray = a.pub("/stray")
    a.ungated_publish_on_take[a.sub("/in")] = stray

    result = replay(world, [a], node_order={"/a": 0})

    assert result.violations == [{"kind": "publish", "node": "/a", "endpoint": "/stray", "count": 1}]


def test_a_callback_that_does_not_return_fails_the_replay():
    world = World([(T0 + 10 * MS, "/in", b"1")])
    a = FakeProcess(1, world).node("/a")
    a.stuck.add(a.sub("/in"))

    with pytest.raises(ReplayError, match="subscription /in of /a did not return"):
        replay(world, [a], node_order={"/a": 0}, callback_timeout=0.2)
    assert world.freed == {1}


def test_a_callback_waiting_for_a_transform_gets_the_listener_callback_beside_it():
    world = World([(T0 + 10 * MS, "/in", b"1")], recorded=("/out",))
    a = FakeProcess(1, world).node("/a")
    cloud = a.sub("/in", publishes=(a.pub("/out"),))
    listener = a.sub("/tf_static")
    a.waits_on[cloud] = listener

    result = replay(world, [a], node_order={"/a": 0}, nested_after=0.05, callback_timeout=2.0)

    assert world.granted == [(1, cloud), (1, listener)]
    assert (result.callbacks, result.nested_callbacks, result.violations) == (1, 1, [])


def test_a_callback_waiting_on_another_topic_still_fails_the_replay():
    world = World([(T0 + 10 * MS, "/in", b"1")])
    a = FakeProcess(1, world).node("/a")
    cloud = a.sub("/in")
    a.waits_on[cloud] = a.sub("/other")

    with pytest.raises(ReplayError, match="subscription /in of /a did not return"):
        replay(world, [a], node_order={"/a": 0}, nested_after=0.05, callback_timeout=0.3)


def test_a_scope_whose_node_never_starts_fails_at_startup():
    world = World([(T0 + 10 * MS, "/in", b"1")])
    a = FakeProcess(1, world).node("/a")

    with pytest.raises(ReplayError, match="/b did not start"):
        replay(world, [a], node_order={"/a": 0, "/b": 1}, startup_timeout=0.3)


def test_a_process_of_another_link_protocol_fails_the_replay():
    world = World([])
    a = FakeProcess(1, world, protocol=PROTOCOL + 1).node("/a")
    a.sub("/in")
    with pytest.raises(ReplayError, match="link protocol"):
        replay(world, [a], node_order={"/a": 0})


def test_a_process_with_an_unresolved_rclcpp_hook_is_an_anomaly():
    world = World([(T0 + 50 * MS, "/in", b"1")], ["/out"])
    a = FakeProcess(1, world, hooks="execute").node("/a")
    a.sub("/in", publishes=(a.pub("/out"),))

    result = replay(world, [a], node_order={"/a": 0})

    assert result.callbacks == 1
    assert len(result.anomalies) == 1 and "rclcpp hook(s) execute" in result.anomalies[0]


def test_infrastructure_subscriptions_pass_the_gate_until_the_first_step():
    world = World([(T0 + 50 * MS, "/in", b"1")], ["/out"])
    a = FakeProcess(1, world).node("/a")
    events = a.sub("/parameter_events")
    a.sub("/in", publishes=(a.pub("/out"),))

    result = replay(world, [a], node_order={"/a": 0})

    assert world.passed == [(1, events, 1, 0), (1, events, 0, 0)]
    assert (result.callbacks, result.infra_callbacks) == (1, 0)


def test_startup_only_stops_before_the_first_step():
    world = World([(T0 + 10 * MS, "/in", b"1")], ["/out"])

    result = replay(world, chain(world), node_order={"/a": 0, "/b": 1}, startup_only=True)

    assert (result.messages, result.callbacks) == (0, 0)
    assert world.granted == [] and world.published == []
