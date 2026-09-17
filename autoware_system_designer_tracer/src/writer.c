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

#include "writer.h"

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

uint64_t asd_now_realtime_ns(void)
{
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

int asd_writer_open(asd_writer_t * w, const char * dir, uint32_t pid, uint64_t capacity)
{
  memset(w, 0, sizeof(*w));
  if (capacity == 0) {
    errno = EINVAL;
    return -1;
  }

  char path[4096];
  snprintf(path, sizeof(path), "%s/%u.trace", dir, pid);
  int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) {
    return -1;
  }
  size_t size = ASD_TRACE_HEADER_SIZE + (size_t)capacity * ASD_TRACE_RECORD_SIZE;
  if (ftruncate(fd, (off_t)size) != 0) {
    close(fd);
    return -1;
  }
  void * map = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  close(fd);
  if (map == MAP_FAILED) {
    return -1;
  }

  snprintf(path, sizeof(path), "%s/%u.names", dir, pid);
  FILE * names = fopen(path, "w");
  if (names == NULL) {
    munmap(map, size);
    return -1;
  }

  w->header = (asd_trace_header_t *)map;
  w->records = (asd_trace_record_t *)((char *)map + ASD_TRACE_HEADER_SIZE);
  w->capacity = capacity;
  w->map_size = size;
  w->names = names;
  pthread_mutex_init(&w->names_lock, NULL);

  memcpy(w->header->magic, ASD_TRACE_MAGIC, 8);
  w->header->version = ASD_TRACE_VERSION;
  w->header->record_size = ASD_TRACE_RECORD_SIZE;
  w->header->header_size = ASD_TRACE_HEADER_SIZE;
  w->header->pid = pid;
  w->header->capacity = capacity;
  w->header->count = 0;
  w->header->start_realtime_ns = asd_now_realtime_ns();

  fprintf(names, "# asd-names %u pid=%u\n", ASD_TRACE_VERSION, pid);
  fflush(names);
  return 0;
}

void asd_writer_close(asd_writer_t * w)
{
  if (w->header != NULL) {
    munmap(w->header, w->map_size);
  }
  if (w->names != NULL) {
    fclose(w->names);
    pthread_mutex_destroy(&w->names_lock);
  }
  memset(w, 0, sizeof(*w));
}

asd_trace_record_t * asd_writer_claim(asd_writer_t * w)
{
  uint64_t slot = __atomic_fetch_add(&w->header->count, 1, __ATOMIC_RELAXED);
  if (slot >= w->capacity) {
    return NULL;
  }
  return &w->records[slot];
}

void asd_writer_commit(asd_trace_record_t * record, uint8_t kind)
{
  __atomic_store_n(&record->kind, kind, __ATOMIC_RELEASE);
}

void asd_writer_name(asd_writer_t * w, const char * line)
{
  pthread_mutex_lock(&w->names_lock);
  fputs(line, w->names);
  fputc('\n', w->names);
  fflush(w->names);
  pthread_mutex_unlock(&w->names_lock);
}
