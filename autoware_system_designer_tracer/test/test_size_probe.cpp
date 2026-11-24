// Copyright 2026 TIER IV, inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// The size probe against rmw_serialize of the rmw implementation in use, for C++ and C
// message structs with sizes and alignments that vary per round.

#include <cstdio>
#include <cstdlib>
#include <random>
#include <string>

#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <rcutils/allocator.h>
#include <rmw/rmw.h>
#include <rmw/serialized_message.h>
#include <rosidl_runtime_c/primitives_sequence_functions.h>
#include <rosidl_runtime_c/string_functions.h>
#include <rosidl_typesupport_cpp/message_type_support.hpp>
#include <sensor_msgs/msg/battery_state.h>
#include <sensor_msgs/msg/camera_info.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <sensor_msgs/msg/point_cloud2.h>
#include <sensor_msgs/msg/point_cloud2.hpp>
#include <sensor_msgs/msg/point_field.h>
#include <std_msgs/msg/empty.hpp>
#include <std_msgs/msg/string.h>
#include <std_msgs/msg/string.hpp>
#include <tf2_msgs/msg/tf_message.hpp>
#include <visualization_msgs/msg/marker_array.hpp>

#include "size_probe.h"

namespace
{

int failures = 0;
std::mt19937 rng(7);

size_t pick(size_t limit)
{
  return std::uniform_int_distribution<size_t>(0, limit)(rng);
}

std::string text(size_t limit)
{
  return std::string(pick(limit), 'x');
}

uint64_t serialized_size(const void * message, const rosidl_message_type_support_t * type)
{
  rcutils_allocator_t allocator = rcutils_get_default_allocator();
  rmw_serialized_message_t out = rmw_get_zero_initialized_serialized_message();
  if (rmw_serialized_message_init(&out, 0, &allocator) != RMW_RET_OK) {
    return 0;
  }
  uint64_t size = rmw_serialize(message, type, &out) == RMW_RET_OK ? out.buffer_length : 0;
  rmw_serialized_message_fini(&out);
  return size;
}

void check(const char * name, const void * message, const rosidl_message_type_support_t * type)
{
  const asd_size_layout_t layout = asd_size_layout(type);
  const uint64_t probed = asd_size_of(layout, message);
  const uint64_t expected = serialized_size(message, type);
  if (layout.members == nullptr || probed != expected) {
    std::fprintf(
      stderr, "FAIL %s: probe %llu, rmw_serialize %llu\n", name, (unsigned long long)probed,
      (unsigned long long)expected);
    ++failures;
  }
}

template<typename M>
void check_cpp(const char * name, const M & message)
{
  check(name, &message, rosidl_typesupport_cpp::get_message_type_support_handle<M>());
}

void round_cpp()
{
  std_msgs::msg::String s;
  s.data = text(9);
  check_cpp("cpp String", s);
  check_cpp("cpp Empty", std_msgs::msg::Empty());

  sensor_msgs::msg::PointCloud2 cloud;
  cloud.header.frame_id = text(12);
  for (size_t i = pick(5); i > 0; --i) {
    sensor_msgs::msg::PointField field;
    field.name = text(7);
    field.offset = static_cast<uint32_t>(i);
    cloud.fields.push_back(field);
  }
  cloud.data.resize(pick(1 << 20));
  check_cpp("cpp PointCloud2", cloud);

  visualization_msgs::msg::MarkerArray markers;
  for (size_t i = pick(6); i > 0; --i) {
    visualization_msgs::msg::Marker marker;
    marker.header.frame_id = text(5);
    marker.ns = text(9);
    marker.text = text(3);
    marker.points.resize(pick(40));
    marker.colors.resize(pick(3));
    markers.markers.push_back(marker);
  }
  check_cpp("cpp MarkerArray", markers);

  geometry_msgs::msg::PoseWithCovarianceStamped pose;
  pose.header.frame_id = text(6);
  check_cpp("cpp PoseWithCovarianceStamped", pose);

  tf2_msgs::msg::TFMessage tf;
  for (size_t i = pick(5); i > 0; --i) {
    geometry_msgs::msg::TransformStamped t;
    t.header.frame_id = text(6);
    t.child_frame_id = text(11);
    tf.transforms.push_back(t);
  }
  check_cpp("cpp TFMessage", tf);

  diagnostic_msgs::msg::DiagnosticArray diag;
  for (size_t i = pick(4); i > 0; --i) {
    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = text(10);
    for (size_t k = pick(3); k > 0; --k) {
      diagnostic_msgs::msg::KeyValue kv;
      kv.key = text(4);
      kv.value = text(5);
      status.values.push_back(kv);
    }
    diag.status.push_back(status);
  }
  check_cpp("cpp DiagnosticArray", diag);

  sensor_msgs::msg::JointState joints;
  for (size_t i = pick(4); i > 0; --i) {
    joints.name.push_back(text(6));
  }
  joints.position.resize(pick(4));
  joints.effort.resize(pick(4));
  check_cpp("cpp JointState", joints);

  sensor_msgs::msg::CameraInfo info;
  info.distortion_model = text(5);
  info.d.resize(pick(8));
  check_cpp("cpp CameraInfo", info);
}

void round_c()
{
  std_msgs__msg__String s;
  std_msgs__msg__String__init(&s);
  std::string data = text(9);
  rosidl_runtime_c__String__assign(&s.data, data.c_str());
  check("c String", &s, ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, String));
  std_msgs__msg__String__fini(&s);

  sensor_msgs__msg__PointCloud2 cloud;
  sensor_msgs__msg__PointCloud2__init(&cloud);
  rosidl_runtime_c__String__assign(&cloud.header.frame_id, text(12).c_str());
  sensor_msgs__msg__PointField__Sequence__init(&cloud.fields, pick(5));
  for (size_t i = 0; i < cloud.fields.size; ++i) {
    rosidl_runtime_c__String__assign(&cloud.fields.data[i].name, text(7).c_str());
  }
  rosidl_runtime_c__uint8__Sequence__init(&cloud.data, pick(1 << 20));
  check("c PointCloud2", &cloud, ROSIDL_GET_MSG_TYPE_SUPPORT(sensor_msgs, msg, PointCloud2));
  sensor_msgs__msg__PointCloud2__fini(&cloud);

  sensor_msgs__msg__BatteryState battery;
  sensor_msgs__msg__BatteryState__init(&battery);
  rosidl_runtime_c__float__Sequence__init(&battery.cell_voltage, pick(5));
  rosidl_runtime_c__String__assign(&battery.location, text(5).c_str());
  rosidl_runtime_c__String__assign(&battery.serial_number, text(5).c_str());
  check("c BatteryState", &battery, ROSIDL_GET_MSG_TYPE_SUPPORT(sensor_msgs, msg, BatteryState));
  sensor_msgs__msg__BatteryState__fini(&battery);
}

}  // namespace

int main()
{
  for (int round = 0; round < 200; ++round) {
    round_cpp();
    round_c();
  }
  if (failures != 0) {
    std::fprintf(stderr, "%d mismatches\n", failures);
    return EXIT_FAILURE;
  }
  std::printf("size probe matches rmw_serialize (%s)\n", rmw_get_implementation_identifier());
  return EXIT_SUCCESS;
}
