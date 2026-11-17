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

from autoware_system_designer_runtime._impl.replay.middleware import (
    ReplayMiddleware,
    ReplayMiddlewareError,
    replay_middleware,
)


class UUID:
    __module__ = "unique_identifier_msgs.msg._uuid"


class Header:
    __module__ = "std_msgs.msg._header"


def test_the_package_file_gives_the_replay_settings():
    middleware = replay_middleware()
    assert middleware.parameter_entries() == [
        {"name": "use_sim_time", "value": True, "type": "bool", "parameter_type": "MODE"}
    ]
    assert middleware.launcher == {"use_intra_process_comms": False}
    assert middleware.isolation({"ROS_DISTRO": "humble"}) == {"ROS_LOCALHOST_ONLY": "1"}
    assert middleware.isolation({"ROS_DISTRO": "jazzy"}) == {"ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"}
    assert middleware.isolation({}) == {"ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"}
    assert middleware.domain_id_variable == "ROS_DOMAIN_ID"
    assert middleware.identifier_field(UUID()) == "uuid"
    assert middleware.identifier_field(Header()) is None


def test_parameter_arguments_leave_what_the_command_sets():
    middleware = ReplayMiddleware.from_mapping({"node_parameters": {"use_sim_time": True, "rate": 2.5, "frame": "map"}})
    assert middleware.parameter_arguments(["node", "--ros-args", "-p", "rate:=1.0"]) == [
        "-p",
        "use_sim_time:=true",
        "-p",
        "frame:=map",
    ]
    assert [entry["type"] for entry in middleware.parameter_entries()] == ["bool", "double", "string"]


def test_an_empty_file_sets_nothing():
    middleware = ReplayMiddleware.from_mapping(None)
    assert middleware.parameter_entries() == []
    assert middleware.parameter_arguments(["node", "--ros-args"]) == []
    assert middleware.identifier_field(UUID()) is None
    assert middleware.isolation({"ROS_DISTRO": "humble"}) == {}


@pytest.mark.parametrize(
    "payload",
    [
        ["use_sim_time"],
        {"parameters": {}},
        {"node_parameters": ["use_sim_time"]},
        {"node_parameters": {"use_sim_time": [True]}},
        {"isolation_environment": {"ROS_LOCALHOST_ONLY": "1"}},
        {"isolation_environment": {"humble": {"ROS_LOCALHOST_ONLY": 1}}},
        {"distro_variable": ["ROS_DISTRO"]},
        {"domain_id_variable": 3},
        {"identifier_types": {"unique_identifier_msgs/msg/UUID": None}},
    ],
)
def test_a_malformed_file_is_refused(payload):
    with pytest.raises(ReplayMiddlewareError):
        ReplayMiddleware.from_mapping(payload)
