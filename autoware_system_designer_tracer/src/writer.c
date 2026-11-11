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

// A new MAP_SHARED file of `size` zero bytes.
static void * map_new_file(const char * path, size_t size)
{
  int fd = open(path, O_RDWR | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0) {
    return NULL;
  }
  if (ftruncate(fd, (off_t)size) != 0) {
    close(fd);
    return NULL;
  }
  void * map = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  close(fd);
  return map == MAP_FAILED ? NULL : map;
}

// ---- event records ------------------------------------------------------------------

int asd_events_open(asd_events_t * w, const char * dir, uint32_t pid, uint64_t capacity)
{
  memset(w, 0, sizeof(*w));
  if (capacity == 0 || capacity > ASD_TRACE_MAX_CAPACITY) {
    errno = EINVAL;
    return -1;
  }
  char path[4096];
  snprintf(path, sizeof(path), "%s/%u.trace", dir, pid);
  size_t size = ASD_TRACE_HEADER_SIZE + (size_t)capacity * ASD_TRACE_RECORD_SIZE;
  void * map = map_new_file(path, size);
  if (map == NULL) {
    return -1;
  }
  w->header = (asd_trace_header_t *)map;
  w->records = (asd_trace_record_t *)((char *)map + ASD_TRACE_HEADER_SIZE);
  w->capacity = capacity;
  w->map_size = size;

  memcpy(w->header->magic, ASD_TRACE_MAGIC, 8);
  w->header->version = ASD_TRACE_VERSION;
  w->header->record_size = ASD_TRACE_RECORD_SIZE;
  w->header->header_size = ASD_TRACE_HEADER_SIZE;
  w->header->pid = pid;
  w->header->capacity = capacity;
  w->header->count = 0;
  w->header->start_realtime_ns = asd_now_realtime_ns();
  return 0;
}

void asd_events_close(asd_events_t * w)
{
  if (w->header != NULL) {
    munmap(w->header, w->map_size);
  }
  memset(w, 0, sizeof(*w));
}

asd_trace_record_t * asd_events_claim(asd_events_t * w)
{
  uint64_t slot = __atomic_fetch_add(&w->header->count, 1, __ATOMIC_RELAXED);
  if (slot >= w->capacity) {
    return NULL;
  }
  return &w->records[slot];
}

void asd_events_commit(asd_trace_record_t * record, uint8_t kind)
{
  __atomic_store_n(&record->kind, kind, __ATOMIC_RELEASE);
}

// ---- name table -----------------------------------------------------------------------

int asd_names_open(asd_names_t * w, const char * dir, uint32_t pid)
{
  memset(w, 0, sizeof(*w));
  char path[4096];
  snprintf(path, sizeof(path), "%s/%u.names", dir, pid);
  int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0600);
  FILE * file = fd < 0 ? NULL : fdopen(fd, "w");
  if (file == NULL) {
    if (fd >= 0) {
      close(fd);
    }
    return -1;
  }
  w->file = file;
  pthread_mutex_init(&w->lock, NULL);
  fprintf(file, "# asd-names %u pid=%u\n", ASD_TRACE_VERSION, pid);
  fflush(file);
  return 0;
}

void asd_names_close(asd_names_t * w)
{
  if (w->file != NULL) {
    fclose(w->file);
    pthread_mutex_destroy(&w->lock);
  }
  memset(w, 0, sizeof(*w));
}

void asd_names_line(asd_names_t * w, const char * line)
{
  pthread_mutex_lock(&w->lock);
  fputs(line, w->file);
  fputc('\n', w->file);
  fflush(w->file);
  pthread_mutex_unlock(&w->lock);
}

// ---- counters -------------------------------------------------------------------------

static uint32_t map_index(uint64_t handle, uint32_t mask)
{
  return (uint32_t)((handle * 0x9E3779B97F4A7C15ull) >> 32) & mask;
}

static asd_slot_table_t * table_new(uint32_t mask)
{
  return calloc(1, sizeof(asd_slot_table_t) + ((size_t)mask + 1) * sizeof(asd_slot_entry_t));
}

// The entry of `key`, else the empty entry that ends its probe sequence.
static asd_slot_entry_t * table_entry(asd_slot_table_t * table, uint32_t mask, uint64_t key)
{
  for (uint32_t i = map_index(key, mask);; i = (i + 1) & mask) {
    asd_slot_entry_t * entry = &table->entries[i];
    uint64_t current = __atomic_load_n(&entry->handle, __ATOMIC_ACQUIRE);
    if (current == key || current == 0) {
      return entry;
    }
  }
}

static void table_put(asd_slot_table_t * table, uint32_t mask, uint64_t key, uint32_t slot)
{
  asd_slot_entry_t * entry = table_entry(table, mask, key);
  if (entry->handle == key) {
    __atomic_store_n(&entry->slot, slot, __ATOMIC_RELEASE);
    return;
  }
  entry->slot = slot;
  __atomic_store_n(&entry->handle, key, __ATOMIC_RELEASE);
  table->used += 1;
}

// Replaces the table by one of its live handles; keeps it when memory is short.
static asd_slot_table_t * table_compact(asd_stats_t * s)
{
  asd_slot_table_t * old = s->table;
  asd_slot_table_t * table = table_new(s->mask);
  if (table == NULL) {
    return old;
  }
  for (uint32_t i = 0; i <= s->mask; ++i) {
    const asd_slot_entry_t * entry = &old->entries[i];
    if (entry->handle != 0 && entry->slot != ASD_SLOT_NONE) {
      table_put(table, s->mask, entry->handle, entry->slot);
    }
  }
  table->replaced = old;
  __atomic_store_n(&s->table, table, __ATOMIC_RELEASE);
  return table;
}

int asd_stats_open(asd_stats_t * s, const char * dir, uint32_t pid, uint32_t capacity)
{
  memset(s, 0, sizeof(*s));
  if (capacity == 0 || capacity > ASD_STATS_MAX_SLOTS) {
    errno = EINVAL;
    return -1;
  }
  // At most half full of live handles: each holds one entry and owns one slot.
  uint32_t entries = 4;
  while (entries < 2 * capacity) {
    entries <<= 1;
  }
  s->table = table_new(entries - 1);
  s->free_slots = calloc(capacity, sizeof(uint32_t));
  char path[4096];
  snprintf(path, sizeof(path), "%s/%u.stats", dir, pid);
  size_t size = ASD_STATS_HEADER_SIZE + (size_t)capacity * ASD_STATS_SLOT_SIZE;
  void * map = s->table != NULL && s->free_slots != NULL ? map_new_file(path, size) : NULL;
  if (map == NULL) {
    free(s->table);
    free(s->free_slots);
    memset(s, 0, sizeof(*s));
    return -1;
  }
  s->header = (asd_stats_header_t *)map;
  s->slots = (asd_stats_slot_t *)((char *)map + ASD_STATS_HEADER_SIZE);
  s->capacity = capacity;
  s->map_size = size;
  s->mask = entries - 1;
  pthread_mutex_init(&s->lock, NULL);

  s->header->version = ASD_STATS_VERSION;
  s->header->slot_size = ASD_STATS_SLOT_SIZE;
  s->header->header_size = ASD_STATS_HEADER_SIZE;
  s->header->pid = pid;
  s->header->capacity = capacity;
  s->header->claimed = 0;
  s->header->start_realtime_ns = asd_now_realtime_ns();
  // The magic goes last: a reader takes the header as complete once it is there.
  memcpy(s->header->magic, ASD_STATS_MAGIC, 8);
  return 0;
}

void asd_stats_close(asd_stats_t * s)
{
  if (s->header != NULL) {
    munmap(s->header, s->map_size);
    pthread_mutex_destroy(&s->lock);
  }
  for (asd_slot_table_t * table = s->table; table != NULL;) {
    asd_slot_table_t * replaced = table->replaced;
    free(table);
    table = replaced;
  }
  free(s->free_slots);
  memset(s, 0, sizeof(*s));
}

int64_t asd_stats_register(asd_stats_t * s, uint8_t kind, const void * handle)
{
  uint64_t key = (uint64_t)(uintptr_t)handle;
  if (s->header == NULL || key == 0) {
    return -1;
  }
  pthread_mutex_lock(&s->lock);
  asd_slot_table_t * table = s->table;
  // Three quarters of the entries in use: finalized handles hold at least a quarter.
  if (table->used >= (s->mask + 1) / 4 * 3) {
    table = table_compact(s);
  }
  int64_t slot = -1;
  if (table->used < s->mask) {
    if (s->free_count > 0) {
      slot = s->free_slots[--s->free_count];
    } else {
      uint32_t claimed = __atomic_fetch_add(&s->header->claimed, 1, __ATOMIC_RELAXED);
      slot = claimed < s->capacity ? (int64_t)claimed : -1;
    }
  }
  if (slot >= 0) {
    asd_stats_slot_t * target = &s->slots[slot];
    target->handle = key;
    __atomic_store_n(&target->next_slot, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&target->kind, kind, __ATOMIC_RELEASE);
    table_put(table, s->mask, key, (uint32_t)slot);
  } else {
    // An address rcl reuses must not resolve to the slot of its previous owner.
    asd_slot_entry_t * entry = table_entry(table, s->mask, key);
    if (entry->handle == key && entry->slot != ASD_SLOT_NONE) {
      s->free_slots[s->free_count++] = entry->slot;
      __atomic_store_n(&entry->slot, ASD_SLOT_NONE, __ATOMIC_RELEASE);
    }
  }
  pthread_mutex_unlock(&s->lock);
  return slot;
}

asd_stats_slot_t * asd_stats_unregister(asd_stats_t * s, const void * handle)
{
  uint64_t key = (uint64_t)(uintptr_t)handle;
  if (s->header == NULL || key == 0) {
    return NULL;
  }
  asd_stats_slot_t * released = NULL;
  pthread_mutex_lock(&s->lock);
  asd_slot_entry_t * entry = table_entry(s->table, s->mask, key);
  if (entry->handle == key && entry->slot != ASD_SLOT_NONE) {
    released = &s->slots[entry->slot];
    s->free_slots[s->free_count++] = entry->slot;
    __atomic_store_n(&entry->slot, ASD_SLOT_NONE, __ATOMIC_RELEASE);
  }
  pthread_mutex_unlock(&s->lock);
  return released;
}

asd_stats_slot_t * asd_stats_find(const asd_stats_t * s, const void * handle)
{
  uint64_t key = (uint64_t)(uintptr_t)handle;
  const asd_slot_table_t * table = __atomic_load_n(&s->table, __ATOMIC_ACQUIRE);
  if (table == NULL || key == 0) {
    return NULL;
  }
  for (uint32_t i = map_index(key, s->mask);; i = (i + 1) & s->mask) {
    const asd_slot_entry_t * entry = &table->entries[i];
    uint64_t current = __atomic_load_n(&entry->handle, __ATOMIC_ACQUIRE);
    if (current == key) {
      uint32_t slot = __atomic_load_n(&entry->slot, __ATOMIC_ACQUIRE);
      return slot == ASD_SLOT_NONE ? NULL : &s->slots[slot];
    }
    if (current == 0) {
      return NULL;
    }
  }
}

void asd_stats_count(asd_stats_slot_t * slot, uint64_t t_ns)
{
  __atomic_fetch_add(&slot->count, 1, __ATOMIC_RELAXED);
  __atomic_store_n(&slot->last_ns, t_ns, __ATOMIC_RELAXED);
}

void asd_stats_latency(asd_stats_slot_t * slot, int64_t latency_ns)
{
  if (latency_ns < 0 || (uint64_t)latency_ns > ASD_STATS_LATENCY_LIMIT_NS) {
    return;
  }
  __atomic_fetch_add(&slot->lat_sum_ns, (uint64_t)latency_ns, __ATOMIC_RELAXED);
  __atomic_fetch_add(&slot->lat_count, 1, __ATOMIC_RELAXED);
}

void asd_stats_link(const asd_stats_t * s, asd_stats_slot_t * trigger, const asd_stats_slot_t * output)
{
  uint64_t next = (uint64_t)(output - s->slots) + 1;
  // The common case repeats the same link; skipping the store keeps the cache line shared.
  if (__atomic_load_n(&trigger->next_slot, __ATOMIC_RELAXED) != next) {
    __atomic_store_n(&trigger->next_slot, next, __ATOMIC_RELAXED);
  }
}

// ---- control --------------------------------------------------------------------------

const asd_control_t * asd_control_map(const char * dir)
{
  char path[4096];
  snprintf(path, sizeof(path), "%s/%s", dir, ASD_CONTROL_FILE);
  int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (fd < 0) {
    return NULL;
  }
  struct stat st;
  if (fstat(fd, &st) != 0 || st.st_size < (off_t)ASD_CONTROL_SIZE) {
    close(fd);
    return NULL;
  }
  void * map = mmap(NULL, ASD_CONTROL_SIZE, PROT_READ, MAP_SHARED, fd, 0);
  close(fd);
  if (map == MAP_FAILED) {
    return NULL;
  }
  const asd_control_t * control = (const asd_control_t *)map;
  if (memcmp(control->magic, ASD_CONTROL_MAGIC, sizeof(ASD_CONTROL_MAGIC)) != 0 ||
      control->version != ASD_CONTROL_VERSION) {
    munmap(map, ASD_CONTROL_SIZE);
    return NULL;
  }
  return control;
}
