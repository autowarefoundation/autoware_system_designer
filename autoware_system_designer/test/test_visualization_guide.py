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

"""Canvas slot lookup: leaves, nested maps, and unknown components."""

from autoware_system_designer.visualizer.visualization_guide import get_component_position


def test_leaf_slot_is_returned_at_any_depth():
    assert get_component_position(["planning"]) == [5, 2]
    assert get_component_position(["planning", "scenario_planning"]) == [5, 2]
    assert get_component_position(["sensing", "lidar", "top"]) == [0, 1]


def test_nested_component_takes_its_shallowest_slot():
    assert get_component_position(["sensing"]) == [0, 1]
    assert get_component_position(["perception"]) == [2, 1]
    assert get_component_position(["perception", "unknown_sub"]) == [2, 1]


def test_unknown_component_has_no_slot():
    assert get_component_position(["nowhere"]) is None
    assert get_component_position([]) is None
