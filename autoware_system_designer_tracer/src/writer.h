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

// Lock-free record appender over a MAP_SHARED trace file plus a mutex-guarded
// name table. Records survive SIGKILL; nothing is buffered in the process.

#ifndef AUTOWARE_SYSTEM_DESIGNER_TRACER__WRITER_H_
#define AUTOWARE_SYSTEM_DESIGNER_TRACER__WRITER_H_

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>

#include "autoware_system_designer_tracer/trace_format.h"

typedef struct asd_writer {
  asd_trace_header_t * header;
  asd_trace_record_t * records;
  uint64_t capacity;
  size_t map_size;
  FILE * names;
  pthread_mutex_t names_lock;
} asd_writer_t;

// Creates <dir>/<pid>.trace sized for `capacity` records and <dir>/<pid>.names.
// Returns 0 on success, -1 (errno set) on failure; the writer is unusable then.
int asd_writer_open(asd_writer_t * w, const char * dir, uint32_t pid, uint64_t capacity);
void asd_writer_close(asd_writer_t * w);

// Claims the next slot; NULL once the file is full (the claim is still counted).
asd_trace_record_t * asd_writer_claim(asd_writer_t * w);
// Publishes a filled slot to readers.
void asd_writer_commit(asd_trace_record_t * record, uint8_t kind);

// Appends one line to the name table.
void asd_writer_name(asd_writer_t * w, const char * line);

uint64_t asd_now_realtime_ns(void);

#endif  // AUTOWARE_SYSTEM_DESIGNER_TRACER__WRITER_H_
