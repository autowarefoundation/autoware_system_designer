cmake_minimum_required(VERSION 3.14)
project(autoware_system_designer_tracer C)

find_package(ament_cmake REQUIRED)
find_package(rcl REQUIRED)
find_package(rmw REQUIRED)

# The interposer: preloaded into every traced ROS process, records only.
add_library(${PROJECT_NAME} SHARED src/tracer.c src/writer.c)
target_include_directories(${PROJECT_NAME}
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)
target_link_libraries(${PROJECT_NAME} rcl::rcl rmw::rmw ${CMAKE_DL_LIBS} pthread)
target_compile_definitions(${PROJECT_NAME} PRIVATE _GNU_SOURCE)
target_compile_options(${PROJECT_NAME} PRIVATE -Wall -Wextra -O2 -fno-strict-aliasing)
set_target_properties(${PROJECT_NAME} PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)

install(TARGETS ${PROJECT_NAME} LIBRARY DESTINATION lib)
install(DIRECTORY include/ DESTINATION include)

if(BUILD_TESTING)
  add_executable(test_writer test/test_writer.c src/writer.c)
  target_include_directories(test_writer PRIVATE include src)
  target_compile_definitions(test_writer PRIVATE _GNU_SOURCE)
  target_link_libraries(test_writer pthread)
  add_test(NAME test_writer COMMAND test_writer)
endif()

ament_package()
