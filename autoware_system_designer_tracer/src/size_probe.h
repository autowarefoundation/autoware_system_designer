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

// Serialized size of a ROS message from its type's introspection, without
// serializing it: the CDR layout is walked over the message's fields, so a
// sequence of primitives or of fixed-size messages costs one multiplication
// whatever its length, and no message data is read or copied.

#ifndef AUTOWARE_SYSTEM_DESIGNER_TRACER__SIZE_PROBE_H_
#define AUTOWARE_SYSTEM_DESIGNER_TRACER__SIZE_PROBE_H_

#include <stdint.h>

#include <rosidl_runtime_c/message_type_support_struct.h>

#ifdef __cplusplus
extern "C" {
#endif

// Introspection members of a message type; `members` is NULL where the type has none.
typedef struct asd_size_layout {
  const void * members;
  int cpp;  // nonzero: C++ message structs, else C
  int fastcdr;  // nonzero: Fast-CDR rules of the rmw in use (empty primitive sequences align)
} asd_size_layout_t;

// Looks up the introspection of `type` (a dispatcher or an introspection handle);
// may load the type's introspection library, so it runs once per publisher.
asd_size_layout_t asd_size_layout(const rosidl_message_type_support_t * type);

// Serialized size in bytes of `ros_message`, the 4-byte encapsulation header included,
// as rmw_serialize of the rmw in use produces it.
uint64_t asd_size_of(asd_size_layout_t layout, const void * ros_message);

#ifdef __cplusplus
}
#endif

#endif  // AUTOWARE_SYSTEM_DESIGNER_TRACER__SIZE_PROBE_H_
