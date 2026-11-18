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

from autoware_system_designer_runtime.replay_record import (
    PHASE_PRE,
    PHASE_REPLAY,
    parse_progress,
    progress_message,
)


def test_a_progress_line_is_read_back_behind_a_log_prefix():
    line = "[12:00:00] replay - " + progress_message(PHASE_REPLAY, "1.0 of 2.0 s", 0.5)

    assert parse_progress(line) == {"phase": PHASE_REPLAY, "step": "1.0 of 2.0 s", "fraction": 0.5}


def test_a_fraction_is_clamped_and_optional():
    assert parse_progress(progress_message(PHASE_REPLAY, fraction=1.7))["fraction"] == 1.0
    assert parse_progress(progress_message(PHASE_PRE, "launching"))["fraction"] is None


def test_other_lines_carry_no_progress():
    assert parse_progress("replay: 1.000 s of ROS time") is None
    assert parse_progress('replay progress: {"phase": "unknown"}') is None
    assert parse_progress("replay progress: not json") is None
