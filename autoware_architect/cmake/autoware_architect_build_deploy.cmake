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

macro(autoware_architect_build_deploy project_name)
  # Supported invocation patterns:
  #   autoware_architect_build_deploy(<project> <deployment_file>)
  #   autoware_architect_build_deploy(<project> <architecture_file>)
  set(_EXTRA_ARGS ${ARGN})
  list(LENGTH _EXTRA_ARGS _EXTRA_LEN)
  if(_EXTRA_LEN LESS 1)
    message(FATAL_ERROR "autoware_architect_build_deploy: expected at least 1 extra arg (<deployment|architecture>), got ${_EXTRA_LEN}: ${_EXTRA_ARGS}")
  endif()
  list(GET _EXTRA_ARGS 0 _INPUT_NAME)

  set(BUILD_PY_SCRIPT "${CMAKE_BINARY_DIR}/../autoware_architect/script/deployment_process.py")
  set(ARCHITECT_SOURCE_DIR "${CMAKE_SOURCE_DIR}/../architecture/autoware_architect")
  set(ARCHITECTURE_RESOURCE_DIR "${CMAKE_BINARY_DIR}/../autoware_architect/resource")
  set(OUTPUT_ROOT_DIR "${CMAKE_INSTALL_PREFIX}/share/${CMAKE_PROJECT_NAME}/")
  get_filename_component(WORKSPACE_ROOT "${CMAKE_BINARY_DIR}/../.." ABSOLUTE)
  set(LOG_FILE "${WORKSPACE_ROOT}/log/latest_build/${CMAKE_PROJECT_NAME}/build_${_INPUT_NAME}.log")

  if(_INPUT_NAME MATCHES ".*\\.system$")
    # If the input is an architecture file, use it directly.
    set(_DEPLOYMENT_FILE "${_INPUT_NAME}")
    set(_LOG_DESC "(architecture=${_INPUT_NAME})")
  else()
    # If it's a deployment name, resolve to the deployment file path.
    set(_DEPLOYMENT_FILE "${CMAKE_SOURCE_DIR}/deployment/${_INPUT_NAME}.yaml")
    set(_LOG_DESC "(deployment=${_INPUT_NAME})")
    if(NOT EXISTS "${_DEPLOYMENT_FILE}")
      message(FATAL_ERROR "autoware_architect_build_deploy: file not found: ${_DEPLOYMENT_FILE}")
    endif()
  endif()

  add_custom_target(run_build_py_${_INPUT_NAME} ALL
    COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${ARCHITECT_SOURCE_DIR}:$ENV{PYTHONPATH} python3 -d ${BUILD_PY_SCRIPT} ${_DEPLOYMENT_FILE} ${ARCHITECTURE_RESOURCE_DIR} ${OUTPUT_ROOT_DIR} > ${LOG_FILE} 2>&1
    COMMENT "Running build.py script ${_LOG_DESC}. Log: ${LOG_FILE}"
  )
  add_dependencies(${project_name} run_build_py_${_INPUT_NAME})
endmacro()
