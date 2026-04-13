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

macro(autoware_system_designer_build_deploy project_name)
  # Supported invocation patterns:
  #   autoware_system_designer_build_deploy(<project> <deployment_name>.deployments)
  #   autoware_system_designer_build_deploy(<project> <design_file>)
  #   autoware_system_designer_build_deploy(<project> <deployment_or_system_target> PRINT_LEVEL=<LEVEL>)
  #   autoware_system_designer_build_deploy(<project> <deployment_or_system_target> STRICT=<AUTO|ON|OFF>)
  #   autoware_system_designer_build_deploy(<project> <deployment_or_system_target> PRINT_LEVEL=<LEVEL> STRICT=<AUTO|ON|OFF>)
  # PRINT_LEVEL controls what is printed to the terminal (stderr).
  # STRICT controls whether deployment generation failure fails the build.
  #   AUTO: follow AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT (default)
  #   ON:   fail build when deployment generation fails
  #   OFF:  warn and continue local build
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
  set(_STRICT_MODE "AUTO")

  # Parse optional args: PRINT_LEVEL=<LEVEL>, STRICT=<AUTO|ON|OFF>
  if(_EXTRA_LEN GREATER 1)
    math(EXPR _LAST_IDX "${_EXTRA_LEN} - 1")
    foreach(_IDX RANGE 1 ${_LAST_IDX})
      list(GET _EXTRA_ARGS ${_IDX} _OPT)
      if(_OPT MATCHES "^PRINT_LEVEL=.+$")
        string(REPLACE "=" ";" _PAIR "${_OPT}")
        list(GET _PAIR 1 _PRINT_LEVEL)
      elseif(_OPT MATCHES "^STRICT=.+$")
        string(REPLACE "=" ";" _PAIR "${_OPT}")
        list(GET _PAIR 1 _STRICT_MODE)
        string(TOUPPER "${_STRICT_MODE}" _STRICT_MODE)
        if(NOT _STRICT_MODE STREQUAL "AUTO" AND NOT _STRICT_MODE STREQUAL "ON" AND NOT _STRICT_MODE STREQUAL "OFF")
          message(FATAL_ERROR "autoware_system_designer_build_deploy: STRICT must be AUTO, ON, or OFF. Got: ${_OPT}")
        endif()
      else()
        message(FATAL_ERROR "autoware_system_designer_build_deploy: supported optional args are PRINT_LEVEL=<LEVEL> and STRICT=<AUTO|ON|OFF>. Got: ${_OPT}")
      endif()
    endforeach()
  endif()

  # Always call find_package so Python3_VERSION_MAJOR/MINOR are guaranteed set,
  # even when the caller already found Python3 before invoking this macro.
  find_package(Python3 REQUIRED COMPONENTS Interpreter)

  # autoware_system_designer_DIR = <prefix>/share/autoware_system_designer/cmake
  get_filename_component(_AWSD_SCRIPT_DIR "${autoware_system_designer_DIR}/../script" ABSOLUTE)
  set(BUILD_PY_SCRIPT "${_AWSD_SCRIPT_DIR}/deployment_process.py")
  set(SYSTEM_DESIGNER_RUNNER_SCRIPT "${_AWSD_SCRIPT_DIR}/system_designer_runner.py")

  # Derive the installed Python package dir from the interpreter version.
  # Glob + lexicographic sort mis-orders versions (e.g. python3.10 sorts before python3.9),
  # so we use Python3_VERSION_MAJOR/MINOR to construct the exact path instead.
  get_filename_component(_AWSD_INSTALL_PREFIX "${autoware_system_designer_DIR}/../../.." ABSOLUTE)
  set(_AWSD_PYVER "${Python3_VERSION_MAJOR}.${Python3_VERSION_MINOR}")
  set(_AWSD_PYTHON_PATH "")
  foreach(_AWSD_SUBDIR
      "local/lib/python${_AWSD_PYVER}/dist-packages"
      "local/lib/python${_AWSD_PYVER}/site-packages"
      "lib/python${_AWSD_PYVER}/dist-packages"
      "lib/python${_AWSD_PYVER}/site-packages"
  )
    if(EXISTS "${_AWSD_INSTALL_PREFIX}/${_AWSD_SUBDIR}")
      set(_AWSD_PYTHON_PATH "${_AWSD_INSTALL_PREFIX}/${_AWSD_SUBDIR}")
      break()
    endif()
  endforeach()
  set(_AWSD_PYTHONPATH_ARGS "")
  if(_AWSD_PYTHON_PATH)
    set(_AWSD_PYTHONPATH_ARGS "PYTHONPATH=${_AWSD_PYTHON_PATH}")
  endif()

  get_filename_component(SYSTEM_DESIGNER_RESOURCE_DIR "${autoware_system_designer_DIR}/../resource" ABSOLUTE)

  set(OUTPUT_ROOT_DIR "${CMAKE_INSTALL_PREFIX}/share/${CMAKE_PROJECT_NAME}/")
  get_filename_component(WORKSPACE_ROOT "${CMAKE_BINARY_DIR}/../.." ABSOLUTE)
  set(LOG_DIR "${WORKSPACE_ROOT}/log/latest_build/${CMAKE_PROJECT_NAME}")
  set(LOG_FILE "${LOG_DIR}/build_${_INPUT_NAME}.log")
  # If OFF (default), deployment failures are reported but do not fail package build.
  # If ON, deployment failures fail package build (recommended for CI).
  if(NOT DEFINED AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT)
    set(AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT OFF)
  endif()
  string(TOLOWER "${_STRICT_MODE}" _STRICT_MODE_CLI)
  set(_WORKSPACE_ARGS "")
  if(EXISTS "${CMAKE_SOURCE_DIR}/workspace.yaml")
    list(APPEND _WORKSPACE_ARGS "${CMAKE_SOURCE_DIR}/workspace.yaml")
  endif()

  if(_INPUT_NAME MATCHES ".*\\.deployments$")
    # Deployments table name (without .yaml): resolve under this package's deployment directory.
    set(_DEPLOYMENT_FILE "${CMAKE_SOURCE_DIR}/deployment/${_INPUT_NAME}.yaml")
    set(_LOG_DESC "(deployments_table=${_INPUT_NAME})")
  elseif(_INPUT_NAME MATCHES ".*\\.system$")
    # Explicit system entity target.
    set(_DEPLOYMENT_FILE "${_INPUT_NAME}")
    set(_LOG_DESC "(system=${_INPUT_NAME})")
  else()
    message(FATAL_ERROR
      "autoware_system_designer_build_deploy: unsupported target '${_INPUT_NAME}'. "
      "Use '<name>.deployments' or '*.system'."
    )
  endif()

  add_custom_target(run_build_py_${_INPUT_NAME} ALL
    COMMAND ${CMAKE_COMMAND} -E make_directory ${LOG_DIR}
    COMMAND ${CMAKE_COMMAND} -E env
      ${_AWSD_PYTHONPATH_ARGS}
      AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT=${AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT}
      ${Python3_EXECUTABLE} ${SYSTEM_DESIGNER_RUNNER_SCRIPT}
        deploy
        --log-file ${LOG_FILE}
        --print-level ${_PRINT_LEVEL}
        --strict ${_STRICT_MODE_CLI}
        ${BUILD_PY_SCRIPT}
        ${_DEPLOYMENT_FILE}
        ${SYSTEM_DESIGNER_RESOURCE_DIR}
        ${OUTPUT_ROOT_DIR}
        ${_WORKSPACE_ARGS}
    COMMENT "Running build.py script ${_LOG_DESC}. PRINT_LEVEL=${_PRINT_LEVEL}, STRICT=${_STRICT_MODE} (env default=${AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT}); full log: ${LOG_FILE}"
  )
  add_dependencies(${project_name} run_build_py_${_INPUT_NAME})
endmacro()
