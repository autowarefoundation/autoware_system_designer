# Copyright 2025 TIER IV, inc.
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

# copied from autoware_system_designer/autoware_system_designer-extras.cmake

find_package(ament_cmake QUIET REQUIRED)

include("${autoware_system_designer_DIR}/autoware_system_designer_build_deploy.cmake")
include("${autoware_system_designer_DIR}/autoware_system_designer_generate_launcher.cmake")
include("${autoware_system_designer_DIR}/autoware_system_designer_parameter.cmake")