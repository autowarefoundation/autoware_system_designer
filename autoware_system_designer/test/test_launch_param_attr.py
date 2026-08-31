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

import yaml

from autoware_system_designer.common.parameter_types import to_launch_param_attr
from autoware_system_designer.common.template_renderer import TemplateRenderer


def test_null_like_strings_stay_strings():
    for text in ["null", "Null", "NULL", "~", ""]:
        attr = to_launch_param_attr(text)
        assert yaml.safe_load(attr) == text


def test_plain_strings_pass_through():
    assert to_launch_param_attr("general_vehicle_tracker") == "general_vehicle_tracker"
    assert to_launch_param_attr("$(var map_path)/pointcloud_map.pcd") == "$(var map_path)/pointcloud_map.pcd"


def test_string_with_quotes_is_escaped():
    attr = to_launch_param_attr("it's null")
    assert yaml.safe_load(attr) == "it's null"


def test_scalars_keep_current_rendering():
    assert to_launch_param_attr(True) == "True"
    assert to_launch_param_attr(0.5) == "0.5"
    assert to_launch_param_attr(3) == "3"


def test_lists_roundtrip_through_yaml():
    values = [["a", "b"], ["null"], [1, 2], [0.1, 0.2], ["with, comma"]]
    for value in values:
        attr = to_launch_param_attr(value)
        assert "\n" not in attr
        assert yaml.safe_load(attr) == value


def test_template_filter_is_registered():
    renderer = TemplateRenderer()
    assert renderer.env.filters["launch_param_attr"] is to_launch_param_attr
