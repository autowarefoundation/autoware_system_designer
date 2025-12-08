cmake_minimum_required(VERSION 3.14)
project(autoware_architect)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

# Install Python package
ament_python_install_package(${PROJECT_NAME})

# Install template files to share directory (accessible via get_package_share_directory)
install(
  DIRECTORY template/
  DESTINATION share/${PROJECT_NAME}/template
)

# Install scripts to share directory
install(
  DIRECTORY script/
  DESTINATION share/${PROJECT_NAME}/script
  USE_SOURCE_PERMISSIONS
)

ament_package(
  CONFIG_EXTRAS "autoware_architect-extras.cmake"
)

# Copy the script to the build directory
file(GLOB_RECURSE SCRIPT_FILES ${CMAKE_SOURCE_DIR}/script/*.py)

foreach(SCRIPT_FILE ${SCRIPT_FILES})
  get_filename_component(SCRIPT_FILE_NAME ${SCRIPT_FILE} NAME)
  configure_file(${SCRIPT_FILE} ${CMAKE_BINARY_DIR}/script/${SCRIPT_FILE_NAME} COPYONLY)
endforeach()

install(
  DIRECTORY cmake
  DESTINATION share/${PROJECT_NAME}
)
