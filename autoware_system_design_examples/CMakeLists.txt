cmake_minimum_required(VERSION 3.14)
project(autoware_system_design_examples)

find_package(autoware_cmake REQUIRED)
find_package(autoware_system_designer REQUIRED)

autoware_package()

ament_auto_package(
  INSTALL_TO_SHARE
  architecture
)


autoware_system_designer_generate_launcher()

# Add a dummy library to make a target
add_library(${PROJECT_NAME} SHARED src/empty.cpp)

autoware_system_designer_build_deploy(${PROJECT_NAME} vehicle_x.deployment)