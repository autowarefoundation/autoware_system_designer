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

macro(autoware_system_designer_build_deploy project_name)
  # Supported invocation patterns:
  #   autoware_system_designer_build_deploy(<project> <deployment_file>)
  #   autoware_system_designer_build_deploy(<project> <design_file>)
  #   autoware_system_designer_build_deploy(<project> <deployment|design> PRINT_LEVEL=<LEVEL>)
  # PRINT_LEVEL controls what is printed to the terminal (stderr).
  # Full logs are still written to the per-target log file.
  # Valid levels: DEBUG, INFO, WARNING, ERROR, CRITICAL.
  set(_EXTRA_ARGS ${ARGN})
  list(LENGTH _EXTRA_ARGS _EXTRA_LEN)
  if(_EXTRA_LEN LESS 1)
    message(FATAL_ERROR "autoware_system_designer_build_deploy: expected at least 1 extra arg (<deployment|design>), got ${_EXTRA_LEN}: ${_EXTRA_ARGS}")
  endif()
  list(GET _EXTRA_ARGS 0 _INPUT_NAME)

  # Defaults
  set(_PRINT_LEVEL "ERROR")

  # Parse optional args: only PRINT_LEVEL=<LEVEL> is supported.
  if(_EXTRA_LEN GREATER 1)
    if(_EXTRA_LEN GREATER 2)
      message(FATAL_ERROR "autoware_system_designer_build_deploy: only PRINT_LEVEL=<LEVEL> is supported as an optional arg. Got: ${ARGN}")
    endif()
    list(GET _EXTRA_ARGS 1 _ONLY)
    if(NOT _ONLY MATCHES "^PRINT_LEVEL=.+$")
      message(FATAL_ERROR "autoware_system_designer_build_deploy: only PRINT_LEVEL=<LEVEL> is supported (e.g. PRINT_LEVEL=WARNING). Got: ${_ONLY}")
    endif()
    string(REPLACE "=" ";" _PAIR "${_ONLY}")
    list(GET _PAIR 1 _PRINT_LEVEL)
  endif()

  set(BUILD_PY_SCRIPT "${CMAKE_BINARY_DIR}/../autoware_system_designer/script/deployment_process.py")
  set(TEE_RUN_SCRIPT "${CMAKE_BINARY_DIR}/../autoware_system_designer/script/tee_run.py")
  set(SYSTEM_DESIGNER_SOURCE_DIR "${CMAKE_SOURCE_DIR}/../design/autoware_system_designer")
  set(SYSTEM_DESIGNER_RESOURCE_DIR "${CMAKE_BINARY_DIR}/../autoware_system_designer/resource")
  set(OUTPUT_ROOT_DIR "${CMAKE_INSTALL_PREFIX}/share/${CMAKE_PROJECT_NAME}/")
  get_filename_component(WORKSPACE_ROOT "${CMAKE_BINARY_DIR}/../.." ABSOLUTE)
  set(LOG_DIR "${WORKSPACE_ROOT}/log/latest_build/${CMAKE_PROJECT_NAME}")
  set(LOG_FILE "${LOG_DIR}/build_${_INPUT_NAME}.log")
  set(_WORKSPACE_ARGS "")
  if(EXISTS "${CMAKE_SOURCE_DIR}/workspace.yaml")
    list(APPEND _WORKSPACE_ARGS "${CMAKE_SOURCE_DIR}/workspace.yaml")
  endif()

  if(_INPUT_NAME MATCHES ".*\\.deployments\\.yaml$")
    # Deployments table file path was provided directly.
    set(_DEPLOYMENT_FILE "${_INPUT_NAME}")
    set(_LOG_DESC "(deployments_table=${_INPUT_NAME})")
  elseif(_INPUT_NAME MATCHES ".*\\.deployments$")
    # Deployments table name (without .yaml): resolve under this package's deployment directory.
    set(_DEPLOYMENT_FILE "${CMAKE_SOURCE_DIR}/deployment/${_INPUT_NAME}.yaml")
    set(_LOG_DESC "(deployments_table=${_INPUT_NAME})")
  elseif(_INPUT_NAME MATCHES ".*\\.system$")
    # If the input is an design file, use it directly.
    set(_DEPLOYMENT_FILE "${_INPUT_NAME}")
    set(_LOG_DESC "(design=${_INPUT_NAME})")
  else()
    # If it's a deployment name, treat as a system name/file within the system.
    # We assume usage of system files now.
    set(_DEPLOYMENT_FILE "${_INPUT_NAME}.system.yaml")
    set(_LOG_DESC "(system=${_INPUT_NAME})")
  endif()

  add_custom_target(run_build_py_${_INPUT_NAME} ALL
    COMMAND ${CMAKE_COMMAND} -E make_directory ${LOG_DIR}
    COMMAND ${CMAKE_COMMAND} -E env
      PYTHONPATH=${SYSTEM_DESIGNER_SOURCE_DIR}:$ENV{PYTHONPATH}
      autoware_system_designer_PRINT_LEVEL=${_PRINT_LEVEL}
      python3 ${TEE_RUN_SCRIPT} --log-file ${LOG_FILE} -- python3 -d ${BUILD_PY_SCRIPT} ${_DEPLOYMENT_FILE} ${SYSTEM_DESIGNER_RESOURCE_DIR} ${OUTPUT_ROOT_DIR} ${_WORKSPACE_ARGS}
    COMMENT "Running build.py script ${_LOG_DESC}. PRINT_LEVEL=${_PRINT_LEVEL}; full log: ${LOG_FILE}"
  )
  add_dependencies(${project_name} run_build_py_${_INPUT_NAME})
endmacro()
