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

"""The time base durations are measured in.

Records are stamped in wall time, which is what orders them and matches a take
to its publish. A system running on ``/clock`` (``use_sim_time``) declares its
rates and lives its timers in ROS time, so its durations are measured there:
the tracer records every ROS time override as a (wall, ROS) sample, and the
ROS clock maps each wall instant onto that piecewise-linear curve. A paused bag
keeps publishing its frozen value; the tracer flags the last sighting of such a
value, the curve stays flat between the two, and nothing inside the pause counts.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Iterable, Optional, Sequence, Union

from .trace_reader import FLAG_CLOCK_LAST, ClockSample, TraceSet

CLOCK_AUTO = "auto"
CLOCK_WALL = "wall"
CLOCK_ROS = "ros"
CLOCK_CHOICES = (CLOCK_AUTO, CLOCK_WALL, CLOCK_ROS)

# Fewer distinct samples than this cannot define a rate.
MIN_SAMPLES = 2


class WallClock:
    """Durations are wall-time differences."""

    name = CLOCK_WALL
    rate: Optional[float] = None
    samples = 0

    def at(self, t_ns: int) -> int:
        return t_ns

    def elapsed(self, t0_ns: int, t1_ns: int) -> int:
        return t1_ns - t0_ns

    def describe(self) -> str:
        return "wall time"

    def as_dict(self) -> dict:
        return {"base": self.name}


class RosClock:
    """Durations are ROS-time differences along the recorded (wall, ROS) samples.

    Between two samples the mapping is linear; outside them it continues the
    nearest segment. Wall times that fall while ROS time stood still map to the
    same ROS instant.
    """

    name = CLOCK_ROS

    def __init__(self, samples: Iterable[ClockSample]) -> None:
        self._wall, self._ros = _monotone(samples)
        if len(self._wall) < MIN_SAMPLES:
            raise ValueError(f"a ROS clock needs at least {MIN_SAMPLES} distinct /clock samples")
        self.samples = len(self._wall)
        span = self._wall[-1] - self._wall[0]
        self.rate = (self._ros[-1] - self._ros[0]) / span if span > 0 else 0.0

    def at(self, t_ns: int) -> int:
        index = bisect_right(self._wall, t_ns) - 1
        index = min(max(index, 0), len(self._wall) - 2)
        w0, w1 = self._wall[index], self._wall[index + 1]
        r0, r1 = self._ros[index], self._ros[index + 1]
        if w1 == w0:
            return r0
        return r0 + round((t_ns - w0) * (r1 - r0) / (w1 - w0))

    def elapsed(self, t0_ns: int, t1_ns: int) -> int:
        return self.at(t1_ns) - self.at(t0_ns)

    def describe(self) -> str:
        return f"ROS time (x{self.rate:.3g} of wall time, {self.samples} /clock samples)"

    def as_dict(self) -> dict:
        return {"base": self.name, "rate": round(self.rate, 4), "samples": self.samples}


Clock = Union[WallClock, RosClock]


def _monotone(samples: Iterable[ClockSample]) -> tuple[list[int], list[int]]:
    """Distinct ROS values at the earliest wall time each was seen, both non-decreasing.

    Several processes see one value at slightly different wall instants; the
    earliest is its sample. A value the tracer flagged as held (a pause) gets a
    second point at its last sighting, so the curve is flat across the plateau.
    ROS time zero is the value a clock holds before its first /clock message and
    is no sample.
    """
    first_seen: dict[int, int] = {}
    last_held: dict[int, int] = {}
    for sample in samples:
        if sample.ros_ns <= 0:
            continue
        if sample.flags & FLAG_CLOCK_LAST:
            held = last_held.get(sample.ros_ns)
            if held is None or sample.t > held:
                last_held[sample.ros_ns] = sample.t
            continue
        seen = first_seen.get(sample.ros_ns)
        if seen is None or sample.t < seen:
            first_seen[sample.ros_ns] = sample.t
    wall: list[int] = []
    ros: list[int] = []
    for ros_ns in sorted(first_seen):
        t = first_seen[ros_ns]
        if wall and t <= wall[-1]:
            continue
        wall.append(t)
        ros.append(ros_ns)
        end = last_held.get(ros_ns)
        if end is not None and end > t:
            wall.append(end)
            ros.append(ros_ns)
    return wall, ros


def clock_for(trace_set: TraceSet, choice: str = CLOCK_AUTO) -> Clock:
    """The time base of a run: ROS time when the traced processes ran on /clock, else wall time."""
    if choice not in CLOCK_CHOICES:
        raise ValueError(f"unknown clock {choice!r}; expected one of {CLOCK_CHOICES}")
    if choice == CLOCK_WALL:
        return WallClock()
    samples: Sequence[ClockSample] = trace_set.clock_samples()
    try:
        return RosClock(samples)
    except ValueError:
        if choice == CLOCK_ROS:
            raise
        return WallClock()
