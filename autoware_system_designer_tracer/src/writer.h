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

// Writers of one traced process's files. Event records and counters live in
// MAP_SHARED files updated with atomics and no locks, so they survive SIGKILL;
// the name table is mutex-guarded and flushed per line.

#ifndef AUTOWARE_SYSTEM_DESIGNER_TRACER__WRITER_H_
#define AUTOWARE_SYSTEM_DESIGNER_TRACER__WRITER_H_

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>

#include "autoware_system_designer_tracer/trace_format.h"

uint64_t asd_now_realtime_ns(void);

// ---- event records: <pid>.trace -------------------------------------------------

typedef struct asd_events {
  asd_trace_header_t * header;
  asd_trace_record_t * records;
  uint64_t capacity;
  size_t map_size;
} asd_events_t;

// Creates <dir>/<pid>.trace sized for `capacity` records.
// Returns 0 on success, -1 (errno set) on failure; the writer is unusable then.
int asd_events_open(asd_events_t * w, const char * dir, uint32_t pid, uint64_t capacity);
void asd_events_close(asd_events_t * w);
// Claims the next slot; NULL once the file is full (the claim is still counted).
asd_trace_record_t * asd_events_claim(asd_events_t * w);
// Publishes a filled slot to readers.
void asd_events_commit(asd_trace_record_t * record, uint8_t kind);

// ---- name table: <pid>.names ----------------------------------------------------

typedef struct asd_names {
  FILE * file;
  pthread_mutex_t lock;
} asd_names_t;

int asd_names_open(asd_names_t * w, const char * dir, uint32_t pid);
void asd_names_close(asd_names_t * w);
// Appends one line.
void asd_names_line(asd_names_t * w, const char * line);

// ---- counters: <pid>.stats --------------------------------------------------------

typedef struct asd_slot_entry {
  uint64_t handle;  // stored last with release semantics; zero is an empty entry
  uint32_t slot;
  uint32_t reserved;
} asd_slot_entry_t;

// Counter slots plus an open-addressed map from rcl handle to slot. Inserts are
// serialized by `lock`; lookups take no lock.
typedef struct asd_stats {
  asd_stats_header_t * header;
  asd_stats_slot_t * slots;
  uint32_t capacity;
  size_t map_size;
  asd_slot_entry_t * entries;
  uint32_t mask;
  pthread_mutex_t lock;
} asd_stats_t;

int asd_stats_open(asd_stats_t * s, const char * dir, uint32_t pid, uint32_t capacity);
void asd_stats_close(asd_stats_t * s);
// Gives `handle` a fresh slot of `kind`; a handle seen before is remapped, since
// rcl reuses the address of a finalized object. Returns the slot, -1 when full.
int64_t asd_stats_register(asd_stats_t * s, uint8_t kind, const void * handle);
// The slot of `handle`, NULL when it was never registered.
asd_stats_slot_t * asd_stats_find(const asd_stats_t * s, const void * handle);
void asd_stats_count(asd_stats_slot_t * slot, uint64_t t_ns);
// Adds one latency sample; samples outside [0, ASD_STATS_LATENCY_LIMIT_NS] are dropped.
void asd_stats_latency(asd_stats_slot_t * slot, int64_t latency_ns);
// Notes that `trigger` led to a publish on `output`.
void asd_stats_link(const asd_stats_t * s, asd_stats_slot_t * trigger, const asd_stats_slot_t * output);

// ---- control: <dir>/control -------------------------------------------------------

// Read-only mapping of the control file; NULL when the directory has none.
const asd_control_t * asd_control_map(const char * dir);

#endif  // AUTOWARE_SYSTEM_DESIGNER_TRACER__WRITER_H_
