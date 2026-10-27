cmake_minimum_required(VERSION 3.14)
project(autoware_system_design_examples)


find_package(autoware_cmake REQUIRED)
autoware_package()

autoware_system_designer_generate_launcher()

autoware_system_designer_build_deploy(${PROJECT_NAME} vehicle_x.system PRINT_LEVEL=WARNING STRICT=ON)

# Deployment generation scans data bundles under the install share; stage them
# before the deploy target runs (ament installs data/ only after the build phase).
add_custom_target(${PROJECT_NAME}_stage_data ALL
  COMMAND ${CMAKE_COMMAND} -E copy_directory
    ${CMAKE_CURRENT_SOURCE_DIR}/data ${CMAKE_INSTALL_PREFIX}/share/${PROJECT_NAME}/data
)
add_dependencies(${AUTOWARE_SYSTEM_DESIGNER_DEPLOY_TARGET} ${PROJECT_NAME}_stage_data)

autoware_ament_auto_package(INSTALL_TO_SHARE design data)
