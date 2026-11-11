cmake_minimum_required(VERSION 3.14)
project(multi_unit_demo)

find_package(ament_cmake REQUIRED)
find_package(autoware_system_designer REQUIRED)

# Target the generated launchers and the export attach to.
add_custom_target(${PROJECT_NAME} ALL)

autoware_system_designer_generate_launcher()
autoware_system_designer_build_deploy(${PROJECT_NAME} MultiUnitDemo.system PRINT_LEVEL=WARNING STRICT=ON)

install(DIRECTORY design DESTINATION share/${PROJECT_NAME})

ament_package()
