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

// Plain CDR (XCDR1, little endian) as the rmw implementations write it: a 4-byte
// encapsulation header, then the fields in order, each primitive aligned to its size
// (8 at most) from the end of the header; strings and sequences carry a 4-byte count.
// Fast-CDR (rmw_fastrtps, rmw_zenoh) also aligns an empty primitive sequence and
// writes an empty message as 4 bytes.

#include "size_probe.h"

#include <cstring>
#include <string>

#include <rcutils/error_handling.h>
#include <rmw/rmw.h>
#include <rosidl_runtime_c/string.h>
#include <rosidl_runtime_c/u16string.h>
#include <rosidl_typesupport_introspection_c/field_types.h>
#include <rosidl_typesupport_introspection_c/message_introspection.h>
#include <rosidl_typesupport_introspection_cpp/message_introspection.hpp>

namespace
{

constexpr const char * kIntrospectionC = "rosidl_typesupport_introspection_c";
constexpr const char * kIntrospectionCpp = "rosidl_typesupport_introspection_cpp";
constexpr uint64_t kHeaderBytes = 4;

inline uint64_t align(uint64_t offset, uint64_t alignment)
{
  return (offset + alignment - 1) & ~(alignment - 1);
}

// Bytes of one primitive; 0 for strings and messages.
uint64_t primitive_bytes(uint8_t type_id)
{
  switch (type_id) {
    case rosidl_typesupport_introspection_c__ROS_TYPE_BOOLEAN:
    case rosidl_typesupport_introspection_c__ROS_TYPE_OCTET:
    case rosidl_typesupport_introspection_c__ROS_TYPE_CHAR:
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT8:
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT8:
      return 1;
    case rosidl_typesupport_introspection_c__ROS_TYPE_WCHAR:
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT16:
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT16:
      return 2;
    case rosidl_typesupport_introspection_c__ROS_TYPE_FLOAT:
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT32:
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT32:
      return 4;
    case rosidl_typesupport_introspection_c__ROS_TYPE_DOUBLE:
    case rosidl_typesupport_introspection_c__ROS_TYPE_UINT64:
    case rosidl_typesupport_introspection_c__ROS_TYPE_INT64:
      return 8;
    case rosidl_typesupport_introspection_c__ROS_TYPE_LONG_DOUBLE:
      return 16;
    default:
      return 0;
  }
}

inline uint64_t primitive_alignment(uint64_t bytes)
{
  return bytes > 8 ? 8 : bytes;
}

// The C and C++ message structs differ in their strings and in how a sequence is reached.
struct CTypes
{
  using Members = rosidl_typesupport_introspection_c__MessageMembers;
  using Member = rosidl_typesupport_introspection_c__MessageMember;
  using String = rosidl_runtime_c__String;
  using WString = rosidl_runtime_c__U16String;
  static uint64_t string_length(const void * s) {return static_cast<const String *>(s)->size;}
  static uint64_t wstring_length(const void * s) {return static_cast<const WString *>(s)->size;}
  static size_t string_size_of() {return sizeof(String);}
};

struct CppTypes
{
  using Members = rosidl_typesupport_introspection_cpp::MessageMembers;
  using Member = rosidl_typesupport_introspection_cpp::MessageMember;
  static uint64_t string_length(const void * s) {return static_cast<const std::string *>(s)->size();}
  static uint64_t wstring_length(const void * s) {return static_cast<const std::u16string *>(s)->size();}
  static size_t string_size_of() {return sizeof(std::string);}
};

// C sequence struct: data, size, capacity.
struct CSequence
{
  const void * data;
  size_t size;
  size_t capacity;
};

template<typename T>
const typename T::Members * nested(const typename T::Member & member)
{
  return static_cast<const typename T::Members *>(member.members_->data);
}

template<typename T>
size_t element_count(const typename T::Member & member, const void * field)
{
  const bool sequence = member.array_size_ == 0 || member.is_upper_bound_;
  if (!sequence) {
    return member.array_size_;
  }
  if (member.size_function != nullptr) {
    return member.size_function(field);
  }
  return static_cast<const CSequence *>(field)->size;
}

// Element `index` of an array or sequence of strings or messages.
template<typename T>
const void * element(const typename T::Member & member, const void * field, size_t index, size_t element_size)
{
  if (member.get_const_function != nullptr) {
    return member.get_const_function(field, index);
  }
  const bool sequence = member.array_size_ == 0 || member.is_upper_bound_;
  const auto * base =
    static_cast<const uint8_t *>(sequence ? static_cast<const CSequence *>(field)->data : field);
  return base + index * element_size;
}

inline uint64_t add_string(uint64_t offset, uint64_t length)
{
  return align(offset, 4) + 4 + length + 1;
}

inline uint64_t add_wstring(uint64_t offset, uint64_t length)
{
  return align(offset, 4) + 4 + 2 * length;
}

// Size and alignment of a message whose layout does not depend on its values
// (primitives and fixed arrays of them, recursively), laid out from offset 0;
// false where some field has a variable size.
template<typename T>
bool fixed_layout(const typename T::Members * members, uint64_t * size_out, uint64_t * align_out)
{
  uint64_t offset = 0;
  uint64_t max_align = 1;
  for (uint32_t i = 0; i < members->member_count_; ++i) {
    const auto & member = members->members_[i];
    if (member.is_array_ && (member.array_size_ == 0 || member.is_upper_bound_)) {
      return false;
    }
    const uint64_t count = member.is_array_ ? member.array_size_ : 1;
    if (member.type_id_ == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE) {
      uint64_t size = 0;
      uint64_t alignment = 1;
      if (!fixed_layout<T>(nested<T>(member), &size, &alignment) || size % alignment != 0) {
        return false;
      }
      offset = align(offset, alignment) + count * size;
      max_align = alignment > max_align ? alignment : max_align;
      continue;
    }
    const uint64_t bytes = primitive_bytes(member.type_id_);
    if (bytes == 0) {
      return false;
    }
    const uint64_t alignment = primitive_alignment(bytes);
    offset = align(offset, alignment) + count * bytes;
    max_align = alignment > max_align ? alignment : max_align;
  }
  *size_out = offset;
  *align_out = max_align;
  return true;
}

template<typename T>
uint64_t walk(const typename T::Members * members, const void * message, uint64_t offset, bool fastcdr);

template<typename T>
uint64_t add_messages(
  const typename T::Member & member, const void * field, size_t count, uint64_t offset, bool fastcdr)
{
  const auto * sub = nested<T>(member);
  uint64_t size = 0;
  uint64_t alignment = 1;
  if (fixed_layout<T>(sub, &size, &alignment) && size % alignment == 0) {
    return align(offset, alignment) + count * size;
  }
  for (size_t i = 0; i < count; ++i) {
    offset = walk<T>(sub, element<T>(member, field, i, sub->size_of_), offset, fastcdr);
  }
  return offset;
}

template<typename T>
uint64_t walk(const typename T::Members * members, const void * message, uint64_t offset, bool fastcdr)
{
  for (uint32_t i = 0; i < members->member_count_; ++i) {
    const auto & member = members->members_[i];
    const void * field = static_cast<const uint8_t *>(message) + member.offset_;
    const uint8_t type = member.type_id_;
    if (!member.is_array_) {
      if (type == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE) {
        offset = walk<T>(nested<T>(member), field, offset, fastcdr);
      } else if (type == rosidl_typesupport_introspection_c__ROS_TYPE_STRING) {
        offset = add_string(offset, T::string_length(field));
      } else if (type == rosidl_typesupport_introspection_c__ROS_TYPE_WSTRING) {
        offset = add_wstring(offset, T::wstring_length(field));
      } else {
        const uint64_t bytes = primitive_bytes(type);
        offset = align(offset, primitive_alignment(bytes)) + bytes;
      }
      continue;
    }
    const size_t count = element_count<T>(member, field);
    if (member.array_size_ == 0 || member.is_upper_bound_) {
      offset = align(offset, 4) + 4;
    }
    const uint64_t bytes = primitive_bytes(type);
    if (bytes != 0 && (count != 0 || fastcdr)) {
      offset = align(offset, primitive_alignment(bytes)) + count * bytes;
      continue;
    }
    if (count == 0) {
      continue;
    }
    if (type == rosidl_typesupport_introspection_c__ROS_TYPE_MESSAGE) {
      offset = add_messages<T>(member, field, count, offset, fastcdr);
    } else {
      const bool wide = type == rosidl_typesupport_introspection_c__ROS_TYPE_WSTRING;
      for (size_t k = 0; k < count; ++k) {
        const void * s = element<T>(member, field, k, T::string_size_of());
        offset = wide ? add_wstring(offset, T::wstring_length(s)) : add_string(offset, T::string_length(s));
      }
    }
  }
  return offset;
}

// A message without fields holds one placeholder member.
template<typename T>
bool empty_message(const typename T::Members * members)
{
  return members->member_count_ == 1 &&
         std::strcmp(members->members_[0].name_, "structure_needs_at_least_one_member") == 0;
}

template<typename T>
uint64_t body_size(const void * members_ptr, const void * message, bool fastcdr)
{
  const auto * members = static_cast<const typename T::Members *>(members_ptr);
  if (fastcdr && empty_message<T>(members)) {
    return 4;
  }
  return walk<T>(members, message, 0, fastcdr);
}

bool fastcdr_rmw()
{
  const char * rmw = rmw_get_implementation_identifier();
  return rmw != nullptr && (std::strstr(rmw, "fastrtps") != nullptr || std::strstr(rmw, "zenoh") != nullptr);
}

const rosidl_message_type_support_t * introspection(
  const rosidl_message_type_support_t * type, const char * identifier)
{
  if (std::strcmp(type->typesupport_identifier, identifier) == 0) {
    return type;
  }
  const rosidl_message_type_support_t * found = nullptr;
  try {
    found = get_message_typesupport_handle(type, identifier);
  } catch (...) {
    found = nullptr;
  }
  if (found == nullptr) {
    rcutils_reset_error();
  }
  return found;
}

}  // namespace

extern "C" asd_size_layout_t asd_size_layout(const rosidl_message_type_support_t * type)
{
  asd_size_layout_t layout{nullptr, 0, 0};
  if (type == nullptr || type->typesupport_identifier == nullptr) {
    return layout;
  }
  // A C++ dispatcher only knows C++ type supports and a C one only C type supports.
  const bool cpp_first = std::strstr(type->typesupport_identifier, "_cpp") != nullptr;
  const char * order[2] = {
    cpp_first ? kIntrospectionCpp : kIntrospectionC, cpp_first ? kIntrospectionC : kIntrospectionCpp};
  for (const char * identifier : order) {
    const rosidl_message_type_support_t * found = introspection(type, identifier);
    if (found != nullptr && found->data != nullptr) {
      layout.members = found->data;
      layout.cpp = identifier == kIntrospectionCpp;
      layout.fastcdr = fastcdr_rmw();
      return layout;
    }
  }
  return layout;
}

extern "C" uint64_t asd_size_of(asd_size_layout_t layout, const void * ros_message)
{
  if (layout.members == nullptr || ros_message == nullptr) {
    return 0;
  }
  const bool fastcdr = layout.fastcdr != 0;
  return kHeaderBytes + (layout.cpp ? body_size<CppTypes>(layout.members, ros_message, fastcdr) :
                         body_size<CTypes>(layout.members, ros_message, fastcdr));
}
