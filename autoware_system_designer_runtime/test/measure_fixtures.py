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

"""Synthetic trace files and designs shared by the measurement tests."""

from __future__ import annotations

import struct
from pathlib import Path

HEADER = struct.Struct("<8sIIIIQQQ16s")
RECORD = struct.Struct("<BBHIQQQ24sQ")

MS = 1_000_000
S = 1_000_000_000
T0 = 1_700_000_000 * S  # a plausible CLOCK_REALTIME origin


class TraceBuilder:
    """Writes one process's ``<pid>.trace`` and ``<pid>.names`` in the tracer's layout."""

    def __init__(self, pid: int, start_ns: int = T0) -> None:
        self.pid = pid
        self.start_ns = start_ns
        self.records: list[bytes] = []
        self.names: list[str] = [f"# asd-names 1 pid={pid}"]

    def sub(self, handle: int, fqn: str, topic: str, t: int = T0) -> "TraceBuilder":
        self.names.append(f"{t}\tsub\t{handle:x}\t{fqn}\t{topic}\t")
        return self

    def pub(self, handle: int, fqn: str, topic: str, gid: str, t: int = T0) -> "TraceBuilder":
        self.names.append(f"{t}\tpub\t{handle:x}\t{fqn}\t{topic}\t{gid}")
        return self

    def timer(self, handle: int, period_ns: int, t: int = T0) -> "TraceBuilder":
        self.names.append(f"{t}\ttimer\t{handle:x}\t{period_ns}")
        return self

    def take(
        self, t: int, tid: int, handle: int, source_ts: int, gid: str, flags: int = 0, seq: int = 0
    ) -> "TraceBuilder":
        self.records.append(RECORD.pack(1, flags, 0, tid, t, source_ts, handle, bytes.fromhex(gid), seq))
        return self

    def fire(self, t: int, tid: int, handle: int) -> "TraceBuilder":
        self.records.append(RECORD.pack(2, 0, 0, tid, t, 0, handle, bytes(24), 0))
        return self

    def publish(self, t_in: int, t_out: int, tid: int, handle: int) -> "TraceBuilder":
        self.records.append(RECORD.pack(3, 0, 0, tid, t_in, t_out, handle, bytes(24), 0))
        return self

    def clock(self, t: int, ros_ns: int, tid: int = 9, handle: int = 0xC10C, flags: int = 0) -> "TraceBuilder":
        self.records.append(RECORD.pack(4, flags, 0, tid, t, ros_ns, handle, bytes(24), 0))
        return self

    def write(self, directory: Path, capacity: int | None = None, unfinished: int = 0) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        capacity = capacity if capacity is not None else len(self.records) + unfinished
        count = len(self.records) + unfinished
        header = HEADER.pack(
            b"ASDTRACE", 1, RECORD.size, self.pid, HEADER.size, capacity, count, self.start_ns, bytes(16)
        )
        body = b"".join(self.records) + bytes(RECORD.size) * unfinished
        path = directory / f"{self.pid}.trace"
        path.write_bytes(header + body)
        (directory / f"{self.pid}.names").write_text("\n".join(self.names) + "\n")
        return path


def gid(n: int) -> str:
    return f"{n:048x}"


# ---- designs ------------------------------------------------------------------------


def out_port(name: str, topic: str, event_id: str, producers: list[str], msg: str = "std_msgs/msg/String") -> dict:
    return {
        "name": name,
        "msg_type": msg,
        "topic": topic.strip("/").split("/"),
        "event": {
            "name": f"output_{name}",
            "type": "to_output",
            "unique_id": event_id,
            "trigger_ids": producers,
            "action_ids": [],
        },
    }


def in_port(name: str, topic: str, event_id: str, msg: str = "std_msgs/msg/String") -> dict:
    return {
        "name": name,
        "msg_type": msg,
        "topic": topic.strip("/").split("/"),
        "event": {
            "name": f"input_{name}",
            "type": "on_input",
            "unique_id": event_id,
            "trigger_ids": [],
            "action_ids": [],
        },
    }


def process(name: str, event_id: str, type_: str, triggers: list[str], actions: list[str], frequency=None) -> dict:
    return {
        "name": name,
        "type": type_,
        "process_event": True,
        "unique_id": event_id,
        "trigger_ids": triggers,
        "action_ids": actions,
        "frequency": frequency,
    }


def node(path: str, *, in_ports=(), out_ports=(), events=(), state="single_node", container=None, intra=False) -> dict:
    namespace, _, name = path.rpartition("/")
    launcher = {"launch_state": state, "package": "pkg", "ports": []}
    if state == "single_node":
        launcher["executable"] = name
    if state == "composable_node":
        launcher["container_target"] = container
        launcher["use_intra_process_comms"] = intra
        launcher["plugin"] = "pkg::Node"
    if state == "node_container":
        launcher["executable"] = "component_container_mt"
    return {
        "name": name,
        "namespace": namespace or "/",
        "path": path,
        "entity_type": "node",
        "in_ports": list(in_ports),
        "out_ports": list(out_ports),
        "events": list(events),
        "launcher": launcher,
    }


def system(children: list[dict], *, mode="Test", source_file=None) -> dict:
    return {
        "schema_version": "1.1",
        "metadata": {"system_name": "Test", "mode": mode},
        "data": {
            "name": "root",
            "entity_type": "system",
            "path": "/",
            "source_file": source_file,
            "children": children,
        },
    }


def chain_design() -> dict:
    """/a (timer 20 ms → /x) → /b (on /x → /y); /e samples /x under a 10 ms timer → /r.

    /c and /d share container /cc with intra-process on: /c (timer 50 ms → /p) → /d (on /p → /q).
    /f publishes /w with no declared producer.
    """
    return system(
        [
            node(
                "/a",
                out_ports=[out_port("x", "/x", "a.out", ["a.tick"])],
                events=[process("tick", "a.tick", "periodic", [], ["a.out"], 50.0)],
            ),
            node(
                "/b",
                in_ports=[in_port("x", "/x", "b.in")],
                out_ports=[out_port("y", "/y", "b.out", ["b.run"])],
                events=[process("run", "b.run", "on_input", ["b.in"], ["b.out"])],
            ),
            node(
                "/e",
                in_ports=[in_port("x", "/x", "e.in")],
                out_ports=[out_port("r", "/r", "e.out", ["e.tick"])],
                events=[process("tick", "e.tick", "periodic", [], ["e.out"], 100.0)],
            ),
            node("/cc", state="node_container"),
            node(
                "/c",
                out_ports=[out_port("p", "/p", "c.out", ["c.tick"])],
                events=[process("tick", "c.tick", "periodic", [], ["c.out"], 20.0)],
                state="composable_node",
                container="/cc",
                intra=True,
            ),
            node(
                "/d",
                in_ports=[in_port("p", "/p", "d.in")],
                out_ports=[out_port("q", "/q", "d.out", ["d.run"])],
                events=[process("run", "d.run", "on_input", ["d.in"], ["d.out"])],
                state="composable_node",
                container="/cc",
                intra=True,
            ),
            node("/f", out_ports=[out_port("w", "/w", "f.out", [])]),
        ]
    )


# ---- a traced run of chain_design() ----------------------------------------------------

GA, GB, GC, GD, GE, GF = (gid(n) for n in range(1, 7))
US = 1_000


def write_chain_traces(directory: Path, seconds: int = 1) -> tuple[int, int]:
    """One second of the chain design running; returns the window (start, end) in ns.

    /a fires every 20 ms and publishes /x 1 ms later; /b takes /x 0.5 ms after the
    publish and answers on /y 2 ms later; /e takes /x too but publishes /r from its
    own 10 ms timer. In container /cc, /c publishes /p from a 50 ms timer, /d sees
    only the duplicate DDS copy and publishes /q 3 ms later from a thread with no
    marker. /f publishes /w from a thread that never took or fired.
    """
    end = T0 + seconds * S
    a = TraceBuilder(100).timer(0xA1, 20 * MS).pub(0xA2, "/a", "/x", GA)
    b = TraceBuilder(200).sub(0xB1, "/b", "/x").pub(0xB2, "/b", "/y", GB)
    e = TraceBuilder(300).sub(0xE1, "/e", "/x").timer(0xE2, 10 * MS).pub(0xE3, "/e", "/r", GE)
    cc = (
        TraceBuilder(400).timer(0xC1, 50 * MS).pub(0xC2, "/c", "/p", GC).sub(0xD1, "/d", "/p").pub(0xD2, "/d", "/q", GD)
    )
    f = TraceBuilder(500).pub(0xF1, "/f", "/w", GF)

    t = T0
    while t < end:
        a.fire(t, 1, 0xA1)
        p_in = t + 1 * MS
        p_out = p_in + 50 * US
        a.publish(p_in, p_out, 1, 0xA2)
        source = p_in + 20 * US
        take_b = p_out + 500 * US
        b.take(take_b, 2, 0xB1, source, GA)
        b.publish(take_b + 2 * MS, take_b + 2 * MS + 30 * US, 2, 0xB2)
        e.take(p_out + 300 * US, 3, 0xE1, source, GA)
        t += 20 * MS

    t = T0 + 5 * MS
    while t < end:
        e.fire(t, 3, 0xE2)
        e.publish(t + 500 * US, t + 520 * US, 3, 0xE3)
        t += 10 * MS

    t = T0
    while t < end:
        cc.fire(t, 4, 0xC1)
        p_in = t + 1 * MS
        cc.publish(p_in, p_in + 40 * US, 4, 0xC2)
        cc.take(p_in + 200 * US, 4, 0xD1, p_in + 10 * US, GC)
        cc.publish(p_in + 3 * MS, p_in + 3 * MS + 40 * US, 5, 0xD2)
        t += 50 * MS

    t = T0 + 7 * MS
    while t < end:
        f.publish(t, t + 10 * US, 6, 0xF1)
        t += 100 * MS

    for builder in (a, b, e, cc, f):
        builder.write(directory)
    return T0, end


ROS_T0 = 1_600_000_000 * S  # the bag's own epoch


def write_clock_trace(
    directory: Path, start: int, end: int, rate: float, period_ns: int = 10 * MS, pid: int = 600
) -> Path:
    """One process running on /clock from ``start`` to ``end``: ROS time advances ``rate`` × wall time."""
    b = TraceBuilder(pid).sub(0xC1, "/clock_sink", "/clock")
    t = start - period_ns
    while t <= end + period_ns:
        b.clock(t, ROS_T0 + round((t - start) * rate))
        t += period_ns
    return b.write(directory)
