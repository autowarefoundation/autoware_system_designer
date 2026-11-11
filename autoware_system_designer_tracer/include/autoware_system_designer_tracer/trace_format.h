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

// On-disk layout of one traced process:
//   <pid>.stats  always: a 64-byte header followed by fixed 64-byte counter slots,
//                one per publisher, subscription and timer;
//   <pid>.names  always: the text table that joins record handles and stats slots
//                to node names, topics, publisher gids and timer periods; a line
//                ends with the slot's counters as its endpoint starts;
//   <pid>.trace  while event recording is on: a 64-byte header followed by fixed
//                64-byte event records.
// <dir>/control is written by the runtime and read by every traced process; it
// switches event recording. The Python readers (autoware_system_designer_runtime/
// _impl/measure/trace_reader.py, stats_reader.py) mirror these files field for field.

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
// Records of the ring: the default holds 30 min of a process at 2300 events/s in 256 MiB.
#define ASD_TRACE_DEFAULT_CAPACITY (1u << 22)
#define ASD_TRACE_MAX_CAPACITY (1ull << 24)

enum asd_record_kind {
  ASD_REC_TAKE = 1,
  ASD_REC_TIMER = 2,
  ASD_REC_PUBLISH = 3,
  ASD_REC_CLOCK = 4,  // ROS time override: the process runs on /clock (use_sim_time)
};

enum asd_record_flags {
  ASD_FLAG_FROM_INTRA = 1,  // take: message_info.from_intra_process
  ASD_FLAG_SERIALIZED = 2,  // take/publish of a serialized message
  ASD_FLAG_LOANED = 4,      // take/publish of a loaned message
  ASD_FLAG_NO_INFO = 8,     // take: caller passed no message_info; t2/gid/seq are unset
  ASD_FLAG_CLOCK_LAST = 16, // clock: last wall time a value was still held (a paused /clock)
};

// The records are a ring: `count` is claimed atomically and claim i lives in slot
// i % capacity, so the file holds the latest `capacity` records and
// count - capacity older ones are overwritten.
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
  uint64_t t_ns;   // take/timer/clock: wall time of the call; publish: entry time
  uint64_t t2_ns;  // take: source_timestamp; publish: exit time; clock: ROS time value
  uint64_t handle; // address of the rcl object; joins the name table (clock: rcl_clock_t)
  uint8_t gid[ASD_TRACE_GID_SIZE];  // take: publisher gid
  uint64_t seq;    // take: publication_sequence_number
} asd_trace_record_t;

// ---- counters ----------------------------------------------------------------

#define ASD_STATS_MAGIC "ASDSTATS"
#define ASD_STATS_VERSION 1u
#define ASD_STATS_HEADER_SIZE 64u
#define ASD_STATS_SLOT_SIZE 64u
#define ASD_STATS_SLOTS_ENV "ASD_STATS_SLOTS"
#define ASD_STATS_DEFAULT_SLOTS 1024u
#define ASD_STATS_MAX_SLOTS 65536u
// Latency samples outside [0, limit] come from unrelated clocks or stale triggers.
#define ASD_STATS_LATENCY_LIMIT_NS 1000000000ull

enum asd_slot_kind {
  ASD_SLOT_PUB = 1,
  ASD_SLOT_SUB = 2,
  ASD_SLOT_TIMER = 3,
};

// `claimed` counts claims of a fresh slot; claims at or past `capacity` are endpoints
// without counters. The magic is written last: a header that has it is complete.
typedef struct asd_stats_header {
  char magic[8];
  uint32_t version;
  uint32_t slot_size;
  uint32_t pid;
  uint32_t header_size;
  uint32_t capacity;
  uint32_t claimed;
  uint64_t start_realtime_ns;
  uint64_t reserved[3];
} asd_stats_header_t;

// Cumulative over the endpoints the slot served: a finalized endpoint's slot goes
// to the next one with its counters as they stand, and the name-table line of
// each endpoint carries the counters it started from. A reader takes rates and
// means from the difference of two reads. `kind` is stored last with release
// semantics.
// Latency: take, wall time of the take minus the source timestamp (communication);
// publish, entry of the publish minus the thread's latest take or timer fire
// (callback time up to the publish); timer, none.
typedef struct asd_stats_slot {
  uint8_t kind;
  uint8_t reserved[7];
  uint64_t handle;
  uint64_t count;       // publishes, takes or timer fires
  uint64_t last_ns;     // wall time of the latest one
  uint64_t lat_sum_ns;
  uint64_t lat_count;
  uint64_t next_slot;   // take/timer: 1 + slot of the latest publish it led to on its thread; 0: none yet
  uint64_t reserved2;
} asd_stats_slot_t;

// ---- control -----------------------------------------------------------------

#define ASD_CONTROL_FILE "control"
#define ASD_CONTROL_MAGIC "ASDCTRL"
#define ASD_CONTROL_VERSION 1u
#define ASD_CONTROL_SIZE 64u

// Without a control file in the trace directory, event recording is always on.
typedef struct asd_control {
  char magic[8];
  uint32_t version;
  uint32_t events;  // nonzero: hooks append event records
  uint64_t reserved[6];
} asd_control_t;

#ifdef __cplusplus
}
#endif

#endif  // AUTOWARE_SYSTEM_DESIGNER_TRACER__TRACE_FORMAT_H_
