cmake_minimum_required(VERSION 3.14)
project(autoware_architect_examples)

find_package(autoware_cmake REQUIRED)
find_package(autoware_architect REQUIRED)

autoware_package()

ament_auto_package(
  INSTALL_TO_SHARE
  architecture
)

autoware_architect_configure()

autoware_architect_generate_launcher()

# Add a dummy library to make a target
add_library(${PROJECT_NAME} SHARED src/empty.cpp)

autoware_architect_build_deploy(${PROJECT_NAME} vehicle_x.deployment)