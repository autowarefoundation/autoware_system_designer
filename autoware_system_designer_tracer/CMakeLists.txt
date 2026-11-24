cmake_minimum_required(VERSION 3.14)
project(autoware_system_designer_tracer C CXX)

find_package(ament_cmake REQUIRED)
find_package(rcl REQUIRED)
find_package(rmw REQUIRED)
find_package(rosidl_runtime_c REQUIRED)
find_package(rosidl_runtime_cpp REQUIRED)
find_package(rosidl_typesupport_introspection_c REQUIRED)
find_package(rosidl_typesupport_introspection_cpp REQUIRED)

# The interposer: preloaded into every traced ROS process, records only.
add_library(${PROJECT_NAME} SHARED
  src/tracer.c
  src/writer.c
  src/replay_link.c
  src/replay_table.c
  src/replay_clock.c
  src/replay_gate.c
  src/size_probe.cpp
)
target_include_directories(${PROJECT_NAME}
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)
# Introspection headers only: the type's introspection library is found through its type support.
target_include_directories(${PROJECT_NAME} PRIVATE
  $<TARGET_PROPERTY:rosidl_runtime_cpp::rosidl_runtime_cpp,INTERFACE_INCLUDE_DIRECTORIES>
  $<TARGET_PROPERTY:rosidl_typesupport_introspection_c::rosidl_typesupport_introspection_c,INTERFACE_INCLUDE_DIRECTORIES>
  $<TARGET_PROPERTY:rosidl_typesupport_introspection_cpp::rosidl_typesupport_introspection_cpp,INTERFACE_INCLUDE_DIRECTORIES>
)
target_link_libraries(${PROJECT_NAME} rcl::rcl rmw::rmw rosidl_runtime_c::rosidl_runtime_c ${CMAKE_DL_LIBS} pthread)
target_compile_definitions(${PROJECT_NAME} PRIVATE _GNU_SOURCE)
# -fexceptions: a C++ exception of a callback unwinds through the rclcpp hook.
target_compile_options(${PROJECT_NAME} PRIVATE -Wall -Wextra -O2 -fno-strict-aliasing -fexceptions)
set_target_properties(${PROJECT_NAME} PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON)

install(TARGETS ${PROJECT_NAME} LIBRARY DESTINATION lib)
install(DIRECTORY include/ DESTINATION include)

if(BUILD_TESTING)
  add_executable(test_writer test/test_writer.c src/writer.c)
  target_include_directories(test_writer PRIVATE include src)
  target_compile_definitions(test_writer PRIVATE _GNU_SOURCE)
  target_link_libraries(test_writer pthread)
  add_test(NAME test_writer COMMAND test_writer)

  find_package(rmw_implementation REQUIRED)
  find_package(rosidl_typesupport_cpp REQUIRED)
  find_package(diagnostic_msgs REQUIRED)
  find_package(geometry_msgs REQUIRED)
  find_package(sensor_msgs REQUIRED)
  find_package(std_msgs REQUIRED)
  find_package(tf2_msgs REQUIRED)
  find_package(visualization_msgs REQUIRED)
  add_executable(test_size_probe test/test_size_probe.cpp src/size_probe.cpp)
  target_include_directories(test_size_probe PRIVATE src)
  target_link_libraries(test_size_probe
    rmw::rmw rmw_implementation::rmw_implementation rosidl_runtime_c::rosidl_runtime_c
    rosidl_typesupport_cpp::rosidl_typesupport_cpp
    rosidl_typesupport_introspection_c::rosidl_typesupport_introspection_c
    rosidl_typesupport_introspection_cpp::rosidl_typesupport_introspection_cpp
    ${diagnostic_msgs_TARGETS} ${geometry_msgs_TARGETS} ${sensor_msgs_TARGETS} ${std_msgs_TARGETS}
    ${tf2_msgs_TARGETS} ${visualization_msgs_TARGETS})
  set_target_properties(test_size_probe PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED ON)
  foreach(rmw rmw_cyclonedds_cpp rmw_fastrtps_cpp)
    add_test(NAME test_size_probe_${rmw} COMMAND test_size_probe)
    set_tests_properties(test_size_probe_${rmw} PROPERTIES ENVIRONMENT RMW_IMPLEMENTATION=${rmw})
  endforeach()
endif()

ament_package()
