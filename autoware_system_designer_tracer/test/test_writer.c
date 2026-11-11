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

// The files the writers leave behind must be readable field for field from
// plain files: event header, committed records, overflow accounting, names,
// counters and the control word.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "writer.h"

#define CHECK(cond)                                                              \
  do {                                                                           \
    if (!(cond)) {                                                               \
      fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, #cond);   \
      return 1;                                                                  \
    }                                                                            \
  } while (0)

static int read_file(const char * path, unsigned char ** out, size_t * size)
{
  FILE * f = fopen(path, "rb");
  if (f == NULL) {
    return -1;
  }
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  *out = malloc((size_t)n);
  *size = fread(*out, 1, (size_t)n, f);
  fclose(f);
  return 0;
}

// Counters land in the slot of their handle; a re-registered handle moves to a
// fresh slot; claims past capacity are counted and get none; a finalized handle
// resolves to nothing and its slot serves the next one with its counters kept.
static int check_stats(const char * dir)
{
  asd_stats_t s;
  CHECK(asd_stats_open(&s, dir, 77, 3) == 0);
  int a = 0, b = 0, c = 0;
  CHECK(asd_stats_find(&s, &a) == NULL);
  CHECK(asd_stats_register(&s, ASD_SLOT_PUB, &a) == 0);
  CHECK(asd_stats_register(&s, ASD_SLOT_SUB, &b) == 1);
  asd_stats_slot_t * slot = asd_stats_find(&s, &a);
  CHECK(slot == &s.slots[0]);
  asd_stats_count(slot, 100);
  asd_stats_count(slot, 200);
  asd_stats_latency(slot, 5);
  asd_stats_latency(slot, -1);
  asd_stats_latency(slot, (int64_t)ASD_STATS_LATENCY_LIMIT_NS + 1);
  asd_stats_link(&s, &s.slots[1], slot);
  CHECK(asd_stats_register(&s, ASD_SLOT_PUB, &a) == 2);
  CHECK(asd_stats_find(&s, &a) == &s.slots[2]);
  CHECK(asd_stats_find(&s, &b) == &s.slots[1]);
  CHECK(asd_stats_register(&s, ASD_SLOT_TIMER, &c) == -1);
  CHECK(asd_stats_find(&s, &c) == NULL);
  asd_stats_count(&s.slots[1], 300);
  CHECK(asd_stats_unregister(&s, &b) == &s.slots[1]);
  CHECK(asd_stats_unregister(&s, &b) == NULL);
  CHECK(asd_stats_find(&s, &b) == NULL);
  CHECK(asd_stats_register(&s, ASD_SLOT_TIMER, &c) == 1);
  CHECK(asd_stats_find(&s, &c) == &s.slots[1]);
  // Full again: the address of a live handle registered anew loses its slot.
  CHECK(asd_stats_register(&s, ASD_SLOT_SUB, &a) == -1);
  CHECK(asd_stats_find(&s, &a) == NULL);
  asd_stats_close(&s);

  char path[4200];
  snprintf(path, sizeof(path), "%s/77.stats", dir);
  unsigned char * bytes = NULL;
  size_t size = 0;
  CHECK(read_file(path, &bytes, &size) == 0);
  CHECK(size == ASD_STATS_HEADER_SIZE + 3 * ASD_STATS_SLOT_SIZE);
  const asd_stats_header_t * h = (const asd_stats_header_t *)bytes;
  CHECK(memcmp(h->magic, ASD_STATS_MAGIC, 8) == 0);
  CHECK(h->version == ASD_STATS_VERSION && h->pid == 77 && h->capacity == 3 && h->claimed == 5);
  const asd_stats_slot_t * slots = (const asd_stats_slot_t *)(bytes + ASD_STATS_HEADER_SIZE);
  CHECK(slots[0].kind == ASD_SLOT_PUB && slots[0].handle == (uint64_t)(uintptr_t)&a);
  CHECK(slots[0].count == 2 && slots[0].last_ns == 200);
  CHECK(slots[0].lat_sum_ns == 5 && slots[0].lat_count == 1);
  CHECK(slots[1].kind == ASD_SLOT_TIMER && slots[1].handle == (uint64_t)(uintptr_t)&c);
  CHECK(slots[1].count == 1 && slots[1].last_ns == 300 && slots[1].next_slot == 0);
  CHECK(slots[2].kind == ASD_SLOT_PUB && slots[2].count == 0);
  free(bytes);
  return 0;
}

// Endpoints created and finalized without end keep getting a slot, each at an
// address of its own.
static int check_stats_churn(const char * dir)
{
  enum { HANDLES = 4096 };
  asd_stats_t s;
  CHECK(asd_stats_open(&s, dir, 78, 4) == 0);
  static char handles[HANDLES];
  CHECK(asd_stats_register(&s, ASD_SLOT_PUB, &s) == 0);
  for (int i = 0; i < HANDLES; ++i) {
    int64_t slot = asd_stats_register(&s, ASD_SLOT_SUB, &handles[i]);
    CHECK(slot >= 1 && slot < 4);
    CHECK(asd_stats_find(&s, &handles[i]) == &s.slots[slot]);
    if (i >= 2) {
      CHECK(asd_stats_unregister(&s, &handles[i - 2]) != NULL);
      CHECK(asd_stats_find(&s, &handles[i - 2]) == NULL);
    }
  }
  CHECK(asd_stats_find(&s, &s) == &s.slots[0]);
  CHECK(s.header->claimed == 4);
  asd_stats_close(&s);
  return 0;
}

// The control file maps only with its magic and version.
static int check_control(const char * dir)
{
  CHECK(asd_control_map(dir) == NULL);
  char path[4200];
  snprintf(path, sizeof(path), "%s/%s", dir, ASD_CONTROL_FILE);
  asd_control_t control;
  memset(&control, 0, sizeof(control));
  memcpy(control.magic, ASD_CONTROL_MAGIC, sizeof(ASD_CONTROL_MAGIC));
  control.version = ASD_CONTROL_VERSION;
  control.events = 1;
  FILE * f = fopen(path, "wb");
  CHECK(f != NULL);
  CHECK(fwrite(&control, sizeof(control), 1, f) == 1);
  fclose(f);
  const asd_control_t * mapped = asd_control_map(dir);
  CHECK(mapped != NULL && mapped->events == 1);
  return 0;
}

int main(void)
{
  char dir[] = "/tmp/asd_writer_test_XXXXXX";
  CHECK(mkdtemp(dir) != NULL);

  asd_events_t w;
  CHECK(asd_events_open(&w, dir, 4242, 3) == 0);
  asd_names_t names;
  CHECK(asd_names_open(&names, dir, 4242) == 0);

  asd_trace_record_t * r = asd_events_claim(&w);
  CHECK(r != NULL);
  r->tid = 7;
  r->t_ns = 1000;
  r->t2_ns = 2000;
  r->handle = 0xabc;
  memset(r->gid, 0x11, ASD_TRACE_GID_SIZE);
  r->seq = 5;
  asd_events_commit(r, ASD_REC_TAKE);

  r = asd_events_claim(&w);
  CHECK(r != NULL);
  r->tid = 8;
  r->t_ns = 3000;
  r->handle = 0xdef;
  asd_events_commit(r, ASD_REC_TIMER);

  // The third slot is claimed but left uncommitted: readers must skip it.
  CHECK(asd_events_claim(&w) != NULL);
  // Past capacity the ring wraps: the oldest slot is claimed, unfinished until committed.
  r = asd_events_claim(&w);
  CHECK(r != NULL && r->kind == 0 && r->t_ns == 1000);
  r->tid = 9;
  r->t_ns = 4000;
  r->t2_ns = 5000;
  r->handle = 0x123;
  asd_events_commit(r, ASD_REC_PUBLISH);
  r = asd_events_claim(&w);
  CHECK(r != NULL && r->kind == 0 && r->handle == 0xdef);

  asd_names_line(&names, "1\tpub\tabc\t/ns/node\t/topic\t00ff\t0");
  asd_names_line(&names, "2\ttimer\tdef\t100000000\t1");
  asd_names_close(&names);
  asd_events_close(&w);

  char path[4200];
  snprintf(path, sizeof(path), "%s/4242.trace", dir);
  unsigned char * bytes = NULL;
  size_t size = 0;
  CHECK(read_file(path, &bytes, &size) == 0);
  CHECK(size == ASD_TRACE_HEADER_SIZE + 3 * ASD_TRACE_RECORD_SIZE);

  const asd_trace_header_t * h = (const asd_trace_header_t *)bytes;
  CHECK(memcmp(h->magic, ASD_TRACE_MAGIC, 8) == 0);
  CHECK(h->version == ASD_TRACE_VERSION);
  CHECK(h->record_size == ASD_TRACE_RECORD_SIZE);
  CHECK(h->header_size == ASD_TRACE_HEADER_SIZE);
  CHECK(h->pid == 4242);
  CHECK(h->capacity == 3);
  CHECK(h->count == 5);
  CHECK(h->start_realtime_ns > 0);

  const asd_trace_record_t * recs = (const asd_trace_record_t *)(bytes + ASD_TRACE_HEADER_SIZE);
  CHECK(recs[0].kind == ASD_REC_PUBLISH);
  CHECK(recs[0].tid == 9);
  CHECK(recs[0].t_ns == 4000);
  CHECK(recs[0].t2_ns == 5000);
  CHECK(recs[0].handle == 0x123);
  CHECK(recs[1].kind == 0);
  CHECK(recs[2].kind == 0);
  free(bytes);

  snprintf(path, sizeof(path), "%s/4242.names", dir);
  CHECK(read_file(path, &bytes, &size) == 0);
  const char expected[] =
    "# asd-names 1 pid=4242\n1\tpub\tabc\t/ns/node\t/topic\t00ff\t0\n2\ttimer\tdef\t100000000\t1\n";
  CHECK(size == sizeof(expected) - 1);
  CHECK(memcmp(bytes, expected, size) == 0);
  free(bytes);

  CHECK(sizeof(asd_trace_header_t) == ASD_TRACE_HEADER_SIZE);
  CHECK(sizeof(asd_trace_record_t) == ASD_TRACE_RECORD_SIZE);
  CHECK(sizeof(asd_stats_header_t) == ASD_STATS_HEADER_SIZE);
  CHECK(sizeof(asd_stats_slot_t) == ASD_STATS_SLOT_SIZE);
  CHECK(sizeof(asd_control_t) == ASD_CONTROL_SIZE);
  CHECK(check_stats(dir) == 0);
  CHECK(check_stats_churn(dir) == 0);
  CHECK(check_control(dir) == 0);

  snprintf(path, sizeof(path), "rm -rf %s", dir);
  if (system(path) != 0) {
    return 1;
  }
  printf("ok\n");
  return 0;
}
