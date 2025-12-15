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


# collect all yaml files in the architecture directory
# store the list in a shared file
macro(autoware_architect_configure)
  # Collect all yaml files in the package's architecture directory
  file(GLOB_RECURSE YAML_FILES "${CMAKE_CURRENT_SOURCE_DIR}/architecture/*.yaml")

  # Directory to place per-package manifest resources
  set(resource_dir "${CMAKE_BINARY_DIR}/../autoware_architect/resource")
  file(MAKE_DIRECTORY ${resource_dir})

  # Per-package manifest file
  set(manifest_file "${resource_dir}/${PROJECT_NAME}.yaml")

  # Start (overwrite) manifest
  file(WRITE ${manifest_file} "package_path: ${CMAKE_INSTALL_PREFIX}/share/${PROJECT_NAME}\n")
  file(APPEND ${manifest_file} "system_config_files:\n")

  foreach(YAML_FILE ${YAML_FILES})
    # Infer type from filename pattern
    set(file_type "unknown")
    if(YAML_FILE MATCHES ".*\\.node\\.yaml$")
      set(file_type "node")
    elseif(YAML_FILE MATCHES ".*\\.module\\.yaml$")
      set(file_type "module")
    elseif(YAML_FILE MATCHES ".*system\\.yaml$")
      set(file_type "system")
    elseif(YAML_FILE MATCHES ".*parameter_set\\.yaml$")
      set(file_type "parameter_set")
    endif()

    # Append entry to manifest
    file(APPEND ${manifest_file} "  - path: ${YAML_FILE}\n")
    file(APPEND ${manifest_file} "    type: ${file_type}\n")
  endforeach()

  message(STATUS "autoware_architect: generated per-package manifest ${manifest_file} (files: ${YAML_FILES})")
endmacro()
