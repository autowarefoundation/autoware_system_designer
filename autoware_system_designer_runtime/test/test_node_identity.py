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

"""Names and values the runtime hands to ROS must survive rcl and stay unique."""

from autoware_system_designer_runtime._impl.ros2.common.params import _ros_arg_for_param
from autoware_system_designer_runtime._impl.ros2.launchers.composable import glog_spec_for


def test_glog_names_differ_between_containers_sharing_a_namespace():
    left = glog_spec_for("/control/control_container")
    right = glog_spec_for("/control/control_check_container")

    assert left.namespace == right.namespace == "/control"
    assert left.node_name == "glog_component_control_container"
    assert right.node_name == "glog_component_control_check_container"
    assert left.name == "/control/glog_component_control_container"
    assert right.name == "/control/glog_component_control_check_container"


def test_glog_for_a_root_namespace_container():
    spec = glog_spec_for("/solo_container")

    assert spec.namespace == "/"
    assert spec.name == "/glog_component_solo_container"


def test_empty_list_param_is_omitted_so_the_node_default_applies():
    assert _ros_arg_for_param("launch.perception", []) == []
    assert _ros_arg_for_param("launch.perception", ()) == []


def test_populated_list_param_is_rendered():
    assert _ros_arg_for_param("launch.map", ["a", "b"]) == ["-p", "launch.map:=[a, b]"]
