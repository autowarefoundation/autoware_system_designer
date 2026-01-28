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

# Collect system descriptions
# Scans the workspace source directory
# and generates resource manifests in the build directory.
add_custom_target(collect_system_design_manifests ALL
  COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/script/collect_system_design_manifests.py
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_BINARY_DIR}/resource
    ${CMAKE_INSTALL_PREFIX}
  COMMENT "Collecting autoware system descriptions"
)

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
