cmake_minimum_required(VERSION 3.14)
project(autoware_architecture_dummy_modules)

find_package(autoware_cmake REQUIRED)
autoware_package()

ament_auto_package(
  INSTALL_TO_SHARE
  architecture
)

autoware_architect_configure(dummy_modules)
