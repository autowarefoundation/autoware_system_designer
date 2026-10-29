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

from autoware_system_designer_runtime._impl.ros2.common.params import resolve_value


def test_command_substitution_runs_without_a_shell():
    assert resolve_value("$(command 'echo hello')") == "hello"
    assert resolve_value("$(command 'echo a; echo b')") == "a; echo b"
    assert resolve_value("$(command 'echo $HOME')") == "$HOME"


def test_command_substitution_falls_back_when_the_command_fails():
    assert resolve_value("$(command 'definitely-missing-binary-asd' 'fallback')") == "fallback"
