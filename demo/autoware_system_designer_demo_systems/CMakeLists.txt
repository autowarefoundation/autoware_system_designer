cmake_minimum_required(VERSION 3.14)
project(autoware_system_designer_demo_systems)


find_package(autoware_cmake REQUIRED)
autoware_package()

# Add a dummy library to make a target
add_library(${PROJECT_NAME} SHARED src/empty.cpp)

autoware_system_designer_generate_launcher()

# Crop box computed from the vehicle info by a package script.
autoware_system_designer_generate_param_files(crop_box
  SCRIPT scripts/generate_crop_box_params.py
  ARGS ${CMAKE_CURRENT_SOURCE_DIR}/config/vehicle_info.param.yaml
  OUTPUTS config/crop_box.param.yaml
  DEPENDS config/vehicle_info.param.yaml
)

# Robot description of each vehicle's individual parameters.
autoware_system_designer_generate_robot_description(
  XACRO urdf/vehicle.xacro
  CONFIG_DIRS config/individual_params/vehicle_x
)

autoware_system_designer_build_deploy(${PROJECT_NAME} type_alpha.deployments PRINT_LEVEL=WARNING STRICT=ON)

autoware_ament_auto_package(INSTALL_TO_SHARE config design urdf)
