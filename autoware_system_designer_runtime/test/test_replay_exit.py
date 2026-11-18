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

import pytest

from autoware_system_designer_runtime import replay
from autoware_system_designer_runtime.replay_record import EXIT_FAILED, EXIT_INTERRUPTED


@pytest.mark.parametrize("error", [KeyError("data"), PermissionError("log dir"), ImportError("rosbag2_py")])
def test_an_unexpected_error_is_a_failed_run(monkeypatch, error):
    def prepare(json_path, options):
        raise error

    monkeypatch.setattr(replay, "_prepare", prepare)

    assert replay.replay_from_json("system.json", replay.ReplayOptions()) == EXIT_FAILED


def test_an_interrupt_before_the_run_is_an_interrupted_run(monkeypatch):
    def prepare(json_path, options):
        raise KeyboardInterrupt

    monkeypatch.setattr(replay, "_prepare", prepare)

    assert replay.replay_from_json("system.json", replay.ReplayOptions()) == EXIT_INTERRUPTED
