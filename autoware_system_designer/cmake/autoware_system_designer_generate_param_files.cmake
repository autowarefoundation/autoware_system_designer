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

# Build-time parameter files of a package, written into its install share directory.
# The deployment build loads parameter files from the install share, so every deploy target of the
# package depends on the aggregate target returned by this function.
function(autoware_system_designer_generated_param_files_target out_var)
  set(_TARGET ${PROJECT_NAME}_generated_param_files)
  if(NOT TARGET ${_TARGET})
    add_custom_target(${_TARGET} ALL)
  endif()
  set(${out_var} ${_TARGET} PARENT_SCOPE)
endfunction()

# Generate parameter files whose values a package-provided Python script computes.
#
#   autoware_system_designer_generate_param_files(<name>
#     SCRIPT <generator_script>
#     OUTPUTS <file>...
#     [ARGS <arg>...]
#     [DEPENDS <file>...]
#   )
#
# The script defines generate(args) -> {output: ros__parameters}, called with ARGS; the tool
# writes each returned '/**' parameter file. OUTPUTS are paths relative to the package share
# directory and must match the returned files. Relative SCRIPT and DEPENDS paths resolve against
# the current source directory; DEPENDS lists the files the script reads.
function(autoware_system_designer_generate_param_files name)
  cmake_parse_arguments(ARG "" "SCRIPT" "OUTPUTS;ARGS;DEPENDS" ${ARGN})
  if(NOT ARG_SCRIPT OR NOT ARG_OUTPUTS)
    message(FATAL_ERROR "autoware_system_designer_generate_param_files(${name}): SCRIPT and OUTPUTS are required")
  endif()

  if(NOT Python3_EXECUTABLE)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
  endif()
  get_filename_component(_AWSD_SCRIPT_DIR "${autoware_system_designer_DIR}/../script" ABSOLUTE)
  set(_TOOL "${_AWSD_SCRIPT_DIR}/generate_param_files.py")
  get_filename_component(_SCRIPT "${ARG_SCRIPT}" ABSOLUTE BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
  set(_SHARE_DIR "${CMAKE_INSTALL_PREFIX}/share/${PROJECT_NAME}")

  set(_OUTPUTS "")
  foreach(_OUTPUT ${ARG_OUTPUTS})
    list(APPEND _OUTPUTS "${_SHARE_DIR}/${_OUTPUT}")
  endforeach()
  set(_DEPENDS "")
  foreach(_DEPEND ${ARG_DEPENDS})
    get_filename_component(_DEPEND "${_DEPEND}" ABSOLUTE BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
    list(APPEND _DEPENDS "${_DEPEND}")
  endforeach()

  add_custom_command(
    OUTPUT ${_OUTPUTS}
    COMMAND ${Python3_EXECUTABLE} ${_TOOL} ${_SCRIPT} ${_SHARE_DIR}
      --package-dir ${CMAKE_CURRENT_SOURCE_DIR} --outputs ${ARG_OUTPUTS} -- ${ARG_ARGS}
    DEPENDS ${_TOOL} ${_SCRIPT} ${_DEPENDS}
    COMMENT "Generating ${name} parameter files of ${PROJECT_NAME}"
    VERBATIM
  )
  add_custom_target(${PROJECT_NAME}_generate_${name} DEPENDS ${_OUTPUTS})
  autoware_system_designer_generated_param_files_target(_AGGREGATE_TARGET)
  add_dependencies(${_AGGREGATE_TARGET} ${PROJECT_NAME}_generate_${name})
endfunction()

# Generate robot_state_publisher parameter files by expanding a xacro file once per config
# directory.
#
#   autoware_system_designer_generate_robot_description(
#     XACRO <xacro_file>
#     CONFIG_DIRS <dir>...
#     [CONFIG_DIR_ARG <name>]
#     [OUTPUT_NAME <file_name>]
#     [XACRO_ARGS <name:=value>...]
#   )
#
# Each config directory is passed to the xacro as <CONFIG_DIR_ARG>:=<dir> (default config_dir),
# and the result is written to <dir>/<OUTPUT_NAME> (default robot_description.param.yaml) under
# the package share directory. Config directories lie inside the package source directory.
# Every file the expansion reads is a build dependency, listed by xacro --deps at configure time;
# a newly included file takes effect after CMake re-runs. The package build-depends on xacro and on
# the description packages the xacro includes. One call per XACRO file in a package.
function(autoware_system_designer_generate_robot_description)
  cmake_parse_arguments(ARG "" "XACRO;CONFIG_DIR_ARG;OUTPUT_NAME" "CONFIG_DIRS;XACRO_ARGS" ${ARGN})
  if(NOT ARG_XACRO OR NOT ARG_CONFIG_DIRS)
    message(FATAL_ERROR "autoware_system_designer_generate_robot_description: XACRO and CONFIG_DIRS are required")
  endif()
  if(NOT ARG_CONFIG_DIR_ARG)
    set(ARG_CONFIG_DIR_ARG config_dir)
  endif()
  if(NOT ARG_OUTPUT_NAME)
    set(ARG_OUTPUT_NAME robot_description.param.yaml)
  endif()

  if(NOT Python3_EXECUTABLE)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
  endif()
  find_program(XACRO_EXECUTABLE xacro)
  if(NOT XACRO_EXECUTABLE)
    message(FATAL_ERROR "autoware_system_designer_generate_robot_description: xacro not found")
  endif()

  get_filename_component(_AWSD_SCRIPT_DIR "${autoware_system_designer_DIR}/../script" ABSOLUTE)
  set(_SCRIPT "${_AWSD_SCRIPT_DIR}/generate_robot_description.py")
  set(_WRITER "${_AWSD_SCRIPT_DIR}/generate_param_files.py")
  get_filename_component(_XACRO "${ARG_XACRO}" ABSOLUTE BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
  set(_SHARE_DIR "${CMAKE_INSTALL_PREFIX}/share/${PROJECT_NAME}")

  set(_OUTPUTS "")
  foreach(_CONFIG_DIR ${ARG_CONFIG_DIRS})
    get_filename_component(_CONFIG_DIR "${_CONFIG_DIR}" ABSOLUTE BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
    file(RELATIVE_PATH _REL_DIR "${CMAKE_CURRENT_SOURCE_DIR}" "${_CONFIG_DIR}")
    if(_REL_DIR MATCHES "^\\.\\./")
      message(FATAL_ERROR "autoware_system_designer_generate_robot_description: ${_CONFIG_DIR} is outside ${CMAKE_CURRENT_SOURCE_DIR}")
    endif()
    set(_MAPPINGS ${ARG_CONFIG_DIR_ARG}:=${_CONFIG_DIR} ${ARG_XACRO_ARGS})

    # Included xacro and loaded YAML files of this expansion.
    execute_process(
      COMMAND ${XACRO_EXECUTABLE} --deps ${_XACRO} ${_MAPPINGS}
      OUTPUT_VARIABLE _XACRO_DEPS
      ERROR_VARIABLE _XACRO_ERROR
      RESULT_VARIABLE _XACRO_RESULT
      OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    if(NOT _XACRO_RESULT EQUAL 0)
      message(FATAL_ERROR "xacro --deps failed for ${_REL_DIR}:\n${_XACRO_ERROR}")
    endif()
    separate_arguments(_XACRO_DEPS UNIX_COMMAND "${_XACRO_DEPS}")

    set(_OUTPUT "${_SHARE_DIR}/${_REL_DIR}/${ARG_OUTPUT_NAME}")
    add_custom_command(
      OUTPUT ${_OUTPUT}
      COMMAND ${Python3_EXECUTABLE} ${_SCRIPT} ${_XACRO} ${_OUTPUT}
        --package-dir ${CMAKE_CURRENT_SOURCE_DIR} ${_MAPPINGS}
      DEPENDS ${_SCRIPT} ${_WRITER} ${_XACRO} ${_XACRO_DEPS}
      COMMENT "Generating robot description of ${_REL_DIR}"
      VERBATIM
    )
    list(APPEND _OUTPUTS ${_OUTPUT})
  endforeach()

  get_filename_component(_XACRO_NAME "${_XACRO}" NAME_WE)
  set(_TARGET ${PROJECT_NAME}_generate_robot_description_${_XACRO_NAME})
  add_custom_target(${_TARGET} DEPENDS ${_OUTPUTS})
  autoware_system_designer_generated_param_files_target(_AGGREGATE_TARGET)
  add_dependencies(${_AGGREGATE_TARGET} ${_TARGET})
endfunction()
