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

from autoware_system_designer_runtime._impl.ros2.launchers.launch_file import include_cmdline


def _launch_args(parameters):
    spec = {"launcher": {"package": "pkg", "ros2_launch_file": "a.launch.xml"}, "parameters": parameters}
    cmd = include_cmdline(spec)
    return [cmd[i + 1] for i, token in enumerate(cmd) if token == "--launch-arg"]


def test_boolean_launch_args_are_lowercase():
    args = _launch_args(
        [
            {"name": "as_bool", "value": False},
            {"name": "as_string", "value": "false", "type": "string"},
            {"name": "as_true", "value": True},
        ]
    )
    assert args == ["as_bool:=false", "as_string:=false", "as_true:=true"]


def test_list_and_scalar_launch_args():
    args = _launch_args([{"name": "items", "value": [1, 2]}, {"name": "rate", "value": 10}])
    assert args == ["items:=[1, 2]", "rate:=10"]


def test_global_parameters_are_typed_json_apart_from_launch_args():
    spec = {
        "launcher": {
            "package": "pkg",
            "ros2_launch_file": "a.launch.xml",
            "global_parameters": [
                {"name": "wheel_base", "value": "2.79", "type": "double"},
                {"name": "use_sim_time", "value": False, "type": "bool"},
            ],
        },
        "parameters": [],
    }
    cmd = include_cmdline(spec)
    assert "--launch-arg" not in cmd
    assert [cmd[i + 1] for i, token in enumerate(cmd) if token == "--global-param"] == [
        "wheel_base:=2.79",
        "use_sim_time:=false",
    ]
