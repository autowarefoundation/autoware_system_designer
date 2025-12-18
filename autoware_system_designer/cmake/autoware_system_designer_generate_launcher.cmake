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

macro(autoware_system_designer_generate_launcher)
  # Check if architecture directory exists
  set(ARCHITECTURE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/architecture")
  
  if(EXISTS ${ARCHITECTURE_DIR})
    # Set up paths - use absolute path to the script
    set(GENERATE_LAUNCHER_PY_SCRIPT "${CMAKE_BINARY_DIR}/../autoware_system_designer/script/generate_node_launcher.py")
    set(LAUNCHER_FILE_DIR "${CMAKE_INSTALL_PREFIX}/share/${PROJECT_NAME}/launcher/")
    
    # Set up logging
    get_filename_component(WORKSPACE_ROOT "${CMAKE_BINARY_DIR}/../.." ABSOLUTE)
    set(LOG_DIR "${WORKSPACE_ROOT}/log/latest_build/${PROJECT_NAME}")
    set(LOG_FILE "${LOG_DIR}/launcher_generation.log")
    
    # Find all node YAML files recursively
    file(GLOB_RECURSE NODE_YAML_FILES "${ARCHITECTURE_DIR}/*.node.yaml")
    
    if(NODE_YAML_FILES)
      message(STATUS "Found node YAML files in ${PROJECT_NAME}: ${NODE_YAML_FILES}")
      
      # Create output files list for dependencies and individual commands
      set(LAUNCHER_FILES "")
      set(LAUNCHER_COMMANDS "")
      
      foreach(NODE_YAML_FILE ${NODE_YAML_FILES})
        get_filename_component(NODE_NAME ${NODE_YAML_FILE} NAME_WE)
        set(LAUNCHER_FILE "${LAUNCHER_FILE_DIR}/${NODE_NAME}.launch.xml")
        list(APPEND LAUNCHER_FILES ${LAUNCHER_FILE})
        
        # Create individual custom command for each node
        add_custom_command(
          OUTPUT ${LAUNCHER_FILE}
          COMMAND ${CMAKE_COMMAND} -E make_directory ${LAUNCHER_FILE_DIR}
          COMMAND ${CMAKE_COMMAND} -E make_directory ${LOG_DIR}
          COMMAND python3 ${GENERATE_LAUNCHER_PY_SCRIPT} ${NODE_YAML_FILE} ${LAUNCHER_FILE_DIR} >> ${LOG_FILE} 2>&1
          DEPENDS ${NODE_YAML_FILE} ${GENERATE_LAUNCHER_PY_SCRIPT}
          COMMENT "Generating launcher file ${NODE_NAME}.launch.xml. Check log: ${LOG_FILE}"
          VERBATIM
        )
      endforeach()
      
      # Create custom target for all launcher generation
      add_custom_target(${PROJECT_NAME}_generate_launcher ALL
        DEPENDS ${LAUNCHER_FILES}
      )
      
      # Make sure the launcher generation runs before the main project target
      if(TARGET ${PROJECT_NAME})
        add_dependencies(${PROJECT_NAME} ${PROJECT_NAME}_generate_launcher)
      endif()
      
      # Install generated launcher files
      install(DIRECTORY ${LAUNCHER_FILE_DIR}/
        DESTINATION share/${PROJECT_NAME}/launcher
        FILES_MATCHING PATTERN "*.launch.xml"
      )
      
    else()
      message(STATUS "No node YAML files found for ${PROJECT_NAME} in ${ARCHITECTURE_DIR}")
    endif()
    
  else()
    message(STATUS "No architecture directory found at ${ARCHITECTURE_DIR}")
  endif()
  
endmacro()
