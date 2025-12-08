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

macro(autoware_architect_parameter)
  # Check if schema directory exists
  set(SCHEMA_DIR "${CMAKE_CURRENT_SOURCE_DIR}/schema")
  
  if(EXISTS ${SCHEMA_DIR})
    # Set up paths - use absolute path to the script
    set(PARAMETER_PROCESS_SCRIPT "${CMAKE_BINARY_DIR}/../autoware_architect/script/parameter_process.py")
    set(CONFIG_OUTPUT_DIR "${CMAKE_INSTALL_PREFIX}/share/${PROJECT_NAME}/config")
    
    # Set up logging
    get_filename_component(WORKSPACE_ROOT "${CMAKE_BINARY_DIR}/../.." ABSOLUTE)
    set(LOG_DIR "${WORKSPACE_ROOT}/log/latest_build/${PROJECT_NAME}")
    set(LOG_FILE "${LOG_DIR}/parameter_generation.log")
    
    # Find all schema files
    file(GLOB SCHEMA_FILES "${SCHEMA_DIR}/*.schema.json")
    
    if(SCHEMA_FILES)
      message(STATUS "Found schema files in ${PROJECT_NAME}: ${SCHEMA_FILES}")
      
      # Create output files list for dependencies
      set(CONFIG_FILES "")
      foreach(SCHEMA_FILE ${SCHEMA_FILES})
        get_filename_component(SCHEMA_NAME ${SCHEMA_FILE} NAME_WE)
        list(APPEND CONFIG_FILES "${CONFIG_OUTPUT_DIR}/${SCHEMA_NAME}.param.yaml")
      endforeach()
      
      # Create custom command for parameter generation
      add_custom_command(
        OUTPUT ${CONFIG_FILES}
        COMMAND ${CMAKE_COMMAND} -E make_directory ${CONFIG_OUTPUT_DIR}
        COMMAND ${CMAKE_COMMAND} -E make_directory ${LOG_DIR}
        COMMAND python3 ${PARAMETER_PROCESS_SCRIPT} ${SCHEMA_DIR} ${CONFIG_OUTPUT_DIR} --package-name ${PROJECT_NAME} > ${LOG_FILE} 2>&1
        DEPENDS ${SCHEMA_FILES} ${PARAMETER_PROCESS_SCRIPT}
        COMMENT "Generating parameter files for ${PROJECT_NAME} from schema files. Check log: ${LOG_FILE}"
        VERBATIM
      )
      
      # Create custom target for parameter generation
      add_custom_target(${PROJECT_NAME}_generate_parameters ALL
        DEPENDS ${CONFIG_FILES}
      )
      
      # Make sure the parameter generation runs before the main project target
      if(TARGET ${PROJECT_NAME})
        add_dependencies(${PROJECT_NAME} ${PROJECT_NAME}_generate_parameters)
      endif()
      
      # Install generated config files
      install(DIRECTORY ${CONFIG_OUTPUT_DIR}/
        DESTINATION share/${PROJECT_NAME}/config
        FILES_MATCHING PATTERN "*.param.yaml"
      )
      
    else()
      message(STATUS "No schema files found in ${SCHEMA_DIR}")
    endif()
    
  else()
    message(STATUS "No schema directory found at ${SCHEMA_DIR}")
  endif()
  
endmacro()
