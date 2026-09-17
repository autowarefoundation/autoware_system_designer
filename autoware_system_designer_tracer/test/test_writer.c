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

// The trace file the writer leaves behind must be readable field for field
// from a plain file: header, committed records, overflow accounting, names.

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

int main(void)
{
  char dir[] = "/tmp/asd_writer_test_XXXXXX";
  CHECK(mkdtemp(dir) != NULL);

  asd_writer_t w;
  CHECK(asd_writer_open(&w, dir, 4242, 3) == 0);

  asd_trace_record_t * r = asd_writer_claim(&w);
  CHECK(r != NULL);
  r->tid = 7;
  r->t_ns = 1000;
  r->t2_ns = 2000;
  r->handle = 0xabc;
  memset(r->gid, 0x11, ASD_TRACE_GID_SIZE);
  r->seq = 5;
  asd_writer_commit(r, ASD_REC_TAKE);

  r = asd_writer_claim(&w);
  CHECK(r != NULL);
  r->tid = 8;
  r->t_ns = 3000;
  r->handle = 0xdef;
  asd_writer_commit(r, ASD_REC_TIMER);

  // The third slot is claimed but left uncommitted: readers must skip it.
  CHECK(asd_writer_claim(&w) != NULL);
  // Past capacity the claim is counted and dropped.
  CHECK(asd_writer_claim(&w) == NULL);
  CHECK(asd_writer_claim(&w) == NULL);

  asd_writer_name(&w, "1\tpub\tabc\t/ns/node\t/topic\t00ff");
  asd_writer_name(&w, "2\ttimer\tdef\t100000000");
  asd_writer_close(&w);

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
  CHECK(recs[0].kind == ASD_REC_TAKE);
  CHECK(recs[0].tid == 7);
  CHECK(recs[0].t_ns == 1000);
  CHECK(recs[0].t2_ns == 2000);
  CHECK(recs[0].handle == 0xabc);
  CHECK(recs[0].gid[0] == 0x11 && recs[0].gid[23] == 0x11);
  CHECK(recs[0].seq == 5);
  CHECK(recs[1].kind == ASD_REC_TIMER);
  CHECK(recs[1].handle == 0xdef);
  CHECK(recs[2].kind == 0);
  free(bytes);

  snprintf(path, sizeof(path), "%s/4242.names", dir);
  CHECK(read_file(path, &bytes, &size) == 0);
  const char expected[] = "# asd-names 1 pid=4242\n1\tpub\tabc\t/ns/node\t/topic\t00ff\n2\ttimer\tdef\t100000000\n";
  CHECK(size == sizeof(expected) - 1);
  CHECK(memcmp(bytes, expected, size) == 0);
  free(bytes);

  CHECK(sizeof(asd_trace_header_t) == ASD_TRACE_HEADER_SIZE);
  CHECK(sizeof(asd_trace_record_t) == ASD_TRACE_RECORD_SIZE);

  snprintf(path, sizeof(path), "rm -rf %s", dir);
  if (system(path) != 0) {
    return 1;
  }
  printf("ok\n");
  return 0;
}
