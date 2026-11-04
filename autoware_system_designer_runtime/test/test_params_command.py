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

from autoware_system_designer_runtime._impl.ros2.common.params import _ros_args, parameter_layers, resolve_value


def test_command_substitution_runs_without_a_shell():
    assert resolve_value("$(command 'echo hello')") == "hello"
    assert resolve_value("$(command 'echo a; echo b')") == "a; echo b"
    assert resolve_value("$(command 'echo $HOME')") == "$HOME"


def test_command_substitution_falls_back_when_the_command_fails():
    assert resolve_value("$(command 'definitely-missing-binary-asd' 'fallback')") == "fallback"


def _layered_spec():
    return {
        "parameters": [
            {"name": "global_value", "value": "1", "type": "int", "parameter_type": "GLOBAL"},
            {"name": "default_value", "value": "2", "type": "int", "parameter_type": "DEFAULT"},
            {"name": "override_value", "value": "3", "type": "int", "parameter_type": "OVERRIDE"},
        ],
        "parameter_files_all": [
            {"path": "/cfg/expanded.param.yaml", "parameter_type": "DEFAULT_FILE"},
            {"path": "/cfg/car_a/description.param.yaml", "parameter_type": "DEFAULT_FILE"},
            {"path": "/cfg/car_a/override.param.yaml", "parameter_type": "OVERRIDE_FILE"},
        ],
        "launcher": {
            "launch_time_param_files": [
                {"path": "/cfg/car_a/description.param.yaml", "parameter_type": "DEFAULT_FILE"},
                {"path": "/cfg/car_a/override.param.yaml", "parameter_type": "OVERRIDE_FILE"},
            ]
        },
    }


def test_parameter_layers_follow_the_launcher_tiers():
    assert parameter_layers(_layered_spec(), ["/cfg/vehicle_info.param.yaml"]) == [
        "/cfg/vehicle_info.param.yaml",
        {"global_value": 1},
        "/cfg/car_a/description.param.yaml",
        {"default_value": 2},
        "/cfg/car_a/override.param.yaml",
        {"override_value": 3},
    ]


def test_ros_args_keep_the_layer_order():
    args = _ros_args(name="node", namespace="/", parameters=parameter_layers(_layered_spec()), remaps=[])
    sources = [value.partition(":=")[0] for flag, value in zip(args, args[1:]) if flag in ("-p", "--params-file")]
    assert sources == [
        "global_value",
        "/cfg/car_a/description.param.yaml",
        "default_value",
        "/cfg/car_a/override.param.yaml",
        "override_value",
    ]
