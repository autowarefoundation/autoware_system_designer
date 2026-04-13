cmake_minimum_required(VERSION 3.14)
project(autoware_system_designer)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

# Install Python package
ament_python_install_package(${PROJECT_NAME})

# Install scripts to share directory
install(
  DIRECTORY script/
  DESTINATION share/${PROJECT_NAME}/script
  USE_SOURCE_PERMISSIONS
)

# Collect system descriptions at install time.
# ${CMAKE_CURRENT_SOURCE_DIR} and ${Python3_EXECUTABLE} are expanded at configure time;
install(CODE "
  set(_dest_prefix \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}\")
  execute_process(
    COMMAND \"${CMAKE_COMMAND}\" -E remove_directory
      \"\${_dest_prefix}/share/${PROJECT_NAME}/resource\"
  )
  execute_process(
    COMMAND \"${Python3_EXECUTABLE}\"
      \"${CMAKE_CURRENT_SOURCE_DIR}/script/collect_system_design_manifests.py\"
      \"${CMAKE_CURRENT_SOURCE_DIR}\"
      \"\${_dest_prefix}/share/${PROJECT_NAME}/resource\"
      \"\${CMAKE_INSTALL_PREFIX}\"
    COMMAND_ERROR_IS_FATAL ANY
  )
")

ament_package(
  CONFIG_EXTRAS "autoware_system_designer-extras.cmake"
)

# Copy the script to the build directory
# Reconfigure when scripts change so build/*/script stays up-to-date.
file(GLOB_RECURSE SCRIPT_FILES CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/script/*.py)

foreach(SCRIPT_FILE ${SCRIPT_FILES})
  get_filename_component(SCRIPT_FILE_NAME ${SCRIPT_FILE} NAME)
  configure_file(${SCRIPT_FILE} ${CMAKE_BINARY_DIR}/script/${SCRIPT_FILE_NAME} COPYONLY)
endforeach()

install(
  DIRECTORY cmake
  DESTINATION share/${PROJECT_NAME}
)
