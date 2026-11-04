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

"""
Crop box of the vehicle body, from a vehicle_info.param.yaml.

Generator of autoware_system_designer_generate_param_files.

Args:
    <vehicle_info.param.yaml>
"""

import yaml


def generate(args):
    with open(args[0]) as f:
        info = yaml.safe_load(f)["/**"]["ros__parameters"]

    params = {
        "min_x": -info["rear_overhang"],
        "max_x": info["wheel_base"] + info["front_overhang"],
        "min_y": -(info["wheel_tread"] / 2.0 + info["right_overhang"]),
        "max_y": info["wheel_tread"] / 2.0 + info["left_overhang"],
        "min_z": 0.0,
        "max_z": info["vehicle_height"],
        "negative": True,
    }
    return {"config/crop_box.param.yaml": params}
