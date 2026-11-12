cmake_minimum_required(VERSION 3.14)
project(autoware_system_dummy_modules)

find_package(autoware_cmake REQUIRED)
autoware_package()

# One node class per node design, generated at configure time with the lists of sources and plugins.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
file(GLOB_RECURSE NODE_DESIGNS CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/design/node/*.node.yaml")
set(GENERATOR "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate_dummy_nodes.py")
set(GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
execute_process(
  COMMAND ${Python3_EXECUTABLE} ${GENERATOR}
    --source-dir "${GENERATED_DIR}"
    --cmake "${GENERATED_DIR}/dummy_nodes.cmake"
    ${NODE_DESIGNS}
  RESULT_VARIABLE GENERATOR_RESULT
)
if(NOT GENERATOR_RESULT EQUAL 0)
  message(FATAL_ERROR "generate_dummy_nodes.py failed")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${GENERATOR} ${NODE_DESIGNS})
include("${GENERATED_DIR}/dummy_nodes.cmake")

ament_auto_add_library(${PROJECT_NAME} SHARED ${DUMMY_NODE_SOURCES})

foreach(plugin executable IN ZIP_LISTS DUMMY_NODE_PLUGINS DUMMY_NODE_EXECUTABLES)
  rclcpp_components_register_node(${PROJECT_NAME}
    PLUGIN ${plugin}
    EXECUTABLE ${executable}
  )
endforeach()

autoware_system_designer_generate_launcher()

autoware_ament_auto_package(INSTALL_TO_SHARE config design)
