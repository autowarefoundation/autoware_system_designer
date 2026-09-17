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

// On-disk layout of one traced process: <pid>.trace holds a 64-byte header
// followed by fixed 64-byte records; <pid>.names is the text table that joins
// record handles to node names, topics, publisher gids and timer periods.
// The Python reader (autoware_system_designer_runtime/_impl/measure/trace_reader.py)
// mirrors this file field for field.

#ifndef AUTOWARE_SYSTEM_DESIGNER_TRACER__TRACE_FORMAT_H_
#define AUTOWARE_SYSTEM_DESIGNER_TRACER__TRACE_FORMAT_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ASD_TRACE_MAGIC "ASDTRACE"
#define ASD_TRACE_VERSION 1u
#define ASD_TRACE_RECORD_SIZE 64u
#define ASD_TRACE_HEADER_SIZE 64u
#define ASD_TRACE_GID_SIZE 24u

// Environment the runtime sets on traced processes.
#define ASD_TRACE_DIR_ENV "ASD_TRACE_DIR"
#define ASD_TRACE_CAPACITY_ENV "ASD_TRACE_CAPACITY"
#define ASD_TRACE_DEFAULT_CAPACITY (1u << 22)

enum asd_record_kind {
  ASD_REC_TAKE = 1,
  ASD_REC_TIMER = 2,
  ASD_REC_PUBLISH = 3,
};

enum asd_record_flags {
  ASD_FLAG_FROM_INTRA = 1,  // take: message_info.from_intra_process
  ASD_FLAG_SERIALIZED = 2,  // take/publish of a serialized message
  ASD_FLAG_LOANED = 4,      // take/publish of a loaned message
  ASD_FLAG_NO_INFO = 8,     // take: caller passed no message_info; t2/gid/seq are unset
};

// `count` is claimed atomically; slots at or past `capacity` are dropped, so
// count - capacity is the number of dropped records.
typedef struct asd_trace_header {
  char magic[8];
  uint32_t version;
  uint32_t record_size;
  uint32_t pid;
  uint32_t header_size;
  uint64_t capacity;
  uint64_t count;
  uint64_t start_realtime_ns;
  uint64_t reserved[2];
} asd_trace_header_t;

// `kind` is stored last with release semantics; a zero kind is an unfinished slot.
// Times are CLOCK_REALTIME nanoseconds so they compare with DDS source timestamps.
typedef struct asd_trace_record {
  uint8_t kind;
  uint8_t flags;
  uint16_t reserved;
  uint32_t tid;
  uint64_t t_ns;   // take/timer: return time; publish: entry time
  uint64_t t2_ns;  // take: source_timestamp; publish: exit time
  uint64_t handle; // address of the rcl object; joins the name table
  uint8_t gid[ASD_TRACE_GID_SIZE];  // take: publisher gid
  uint64_t seq;    // take: publication_sequence_number
} asd_trace_record_t;

#ifdef __cplusplus
}
#endif

#endif  // AUTOWARE_SYSTEM_DESIGNER_TRACER__TRACE_FORMAT_H_
