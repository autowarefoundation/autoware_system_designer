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

import struct

from autoware_system_designer_runtime._impl.replay.bag import BagClock, ClockStamped, _clock_ns


class _Messages:
    def __init__(self, messages):
        self._messages = list(messages)

    def peek(self):
        return self._messages[0] if self._messages else None

    def pop(self):
        return self._messages.pop(0)


def test_bag_clock_follows_the_recorded_clock_between_its_samples():
    clock = BagClock([(1000, 100), (1010, 105), (1020, 90), (1030, 130)])
    assert clock.at(990) == 90
    assert clock.at(1000) == 100
    assert clock.at(1003) == 103
    # held at the next sample, the one stepping back left out
    assert clock.at(1008) == 105
    assert clock.at(1025) == 120
    assert clock.at(1040) == 140


def test_clock_stamped_source_restamps_messages():
    source = ClockStamped(_Messages([(1003, "/a", b"x")]), BagClock([(1000, 100)]))
    assert source.peek() == (103, "/a", b"x")
    assert source.pop() == (103, "/a", b"x")
    assert source.peek() is None


def test_clock_message_time_is_read_in_its_byte_order():
    assert _clock_ns(b"\x00\x01\x00\x00" + struct.pack("<iI", 12, 34)) == 12_000_000_034
    assert _clock_ns(b"\x00\x00\x00\x00" + struct.pack(">iI", 12, 34)) == 12_000_000_034
