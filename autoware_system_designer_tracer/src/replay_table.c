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

// Endpoints of a gated process: the live publishers, subscriptions and timers, found by their
// rcl handle and by the id the link names them with, and their registration with the driver.
// A finalized endpoint leaves the table; the lists hold the live ones of each kind.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <rcl/publisher.h>

#include "replay_state.h"

// Open addressing from a key to a live entry; key 0 is free, a key without an entry was removed.
typedef struct map
{
  uint64_t * keys;
  entry_t ** values;
  uint32_t size;
  uint32_t used;
} map_t;

static map_t by_handle;
static map_t by_id;
static entry_t * live[KIND_COUNT];
static uint32_t live_count;
static uint32_t next_id = 1;

static __thread char tls_node[512] = "";

static uint32_t hash_key(uint64_t x)
{
  x ^= x >> 33;
  x *= 0xff51afd7ed558ccdull;
  x ^= x >> 33;
  return (uint32_t)x;
}

static int kind_index(char kind)
{
  return kind == 'p' ? KIND_PUBLISHER : (kind == 's' ? KIND_SUBSCRIPTION : KIND_TIMER);
}

static uint64_t map_key(const map_t * map, const entry_t * e)
{
  return map == &by_id ? (uint64_t)e->id : (uint64_t)(uintptr_t)e->handle;
}

static void map_put(map_t * map, uint64_t key, entry_t * e)
{
  uint32_t mask = map->size - 1;
  uint32_t i = hash_key(key) & mask;
  while (map->keys[i] != 0 && map->values[i] != NULL) {
    i = (i + 1) & mask;
  }
  if (map->keys[i] == 0) {
    ++map->used;
  }
  map->keys[i] = key;
  map->values[i] = e;
}

static entry_t * map_get(const map_t * map, uint64_t key)
{
  if (map->keys == NULL || key == 0) {
    return NULL;
  }
  uint32_t mask = map->size - 1;
  for (uint32_t i = hash_key(key) & mask; map->keys[i] != 0; i = (i + 1) & mask) {
    if (map->keys[i] == key && map->values[i] != NULL) {
      return map->values[i];
    }
  }
  return NULL;
}

static void map_remove(map_t * map, uint64_t key)
{
  uint32_t mask = map->size - 1;
  for (uint32_t i = hash_key(key) & mask; map->keys[i] != 0; i = (i + 1) & mask) {
    if (map->keys[i] == key && map->values[i] != NULL) {
      map->values[i] = NULL;
      return;
    }
  }
}

// Room for one more entry: the map is rebuilt from the live entries once half of it was used.
static bool map_reserve(map_t * map)
{
  if (map->keys != NULL && 2 * (map->used + 1) <= map->size) {
    return true;
  }
  uint32_t size = 256;
  while (size < 4 * (live_count + 1)) {
    size *= 2;
  }
  uint64_t * keys = calloc(size, sizeof(uint64_t));
  entry_t ** values = calloc(size, sizeof(entry_t *));
  if (keys == NULL || values == NULL) {
    free(keys);
    free(values);
    return false;
  }
  free(map->keys);
  free(map->values);
  map->keys = keys;
  map->values = values;
  map->size = size;
  map->used = 0;
  for (int kind = 0; kind < KIND_COUNT; ++kind) {
    for (entry_t * e = live[kind]; e != NULL; e = e->next) {
      map_put(map, map_key(map, e), e);
    }
  }
  return true;
}

entry_t * rp_find(const void * handle)
{
  return map_get(&by_handle, (uint64_t)(uintptr_t)handle);
}

entry_t * rp_entry(uint32_t id)
{
  return map_get(&by_id, id);
}

entry_t * rp_live(int kind)
{
  return live[kind];
}

void rp_table_at_fork(void)
{
  memset(&by_handle, 0, sizeof(by_handle));
  memset(&by_id, 0, sizeof(by_id));
  memset(live, 0, sizeof(live));
  live_count = 0;
  tls_node[0] = '\0';
}

static void drop_entry(entry_t * e)
{
  map_remove(&by_handle, (uint64_t)(uintptr_t)e->handle);
  map_remove(&by_id, e->id);
  if (e->prev != NULL) {
    e->prev->next = e->next;
  } else {
    live[kind_index(e->kind)] = e->next;
  }
  if (e->next != NULL) {
    e->next->prev = e->prev;
  }
  --live_count;
  if (asd_r.grant == e->id) {
    asd_r.grant = 0;
  }
  rp_send("fini\t%u", e->id);
  free(e->node);
  free(e);
}

// An rcl object initialized on the caller's stack is copied to its final place afterwards
// (rcl's own rosout publisher is), so its address names nothing once the caller returns.
static bool on_stack(const void * handle)
{
  pthread_attr_t attr;
  void * base = NULL;
  size_t size = 0;
  bool inside = false;
  if (pthread_getattr_np(pthread_self(), &attr) != 0) {
    return false;
  }
  if (pthread_attr_getstack(&attr, &base, &size) == 0) {
    inside = (const char *)handle >= (const char *)base && (const char *)handle < (const char *)base + size;
  }
  pthread_attr_destroy(&attr);
  return inside;
}

// A new entry for `handle`; NULL when it cannot be tracked, and the endpoint stays ungated.
static entry_t * add_entry(const void * handle, char kind, const char * node)
{
  if (handle == NULL || on_stack(handle)) {
    return NULL;
  }
  entry_t * stale = rp_find(handle);
  if (stale != NULL) {
    drop_entry(stale);
  }
  if (!map_reserve(&by_handle) || !map_reserve(&by_id)) {
    return NULL;
  }
  entry_t * e = calloc(1, sizeof(entry_t));
  char * name = strdup(node);
  if (e == NULL || name == NULL) {
    free(e);
    free(name);
    return NULL;
  }
  e->id = next_id++;
  e->handle = handle;
  e->kind = kind;
  e->node = name;
  e->next_ns = INT64_MIN;
  entry_t ** head = &live[kind_index(kind)];
  e->next = *head;
  if (*head != NULL) {
    (*head)->prev = e;
  }
  *head = e;
  ++live_count;
  map_put(&by_handle, (uint64_t)(uintptr_t)handle, e);
  map_put(&by_id, e->id, e);
  return e;
}

// ---- registration --------------------------------------------------------------------

void asd_replay_node(const rcl_node_t * node)
{
  const char * fqn = rcl_node_get_fully_qualified_name(node);
  snprintf(tls_node, sizeof(tls_node), "%s", fqn != NULL ? fqn : "?");
  pthread_mutex_lock(&asd_r_lock);
  rp_send("node\t%s", tls_node);
  pthread_mutex_unlock(&asd_r_lock);
}

void asd_replay_endpoint(
  char kind, const void * handle, const rcl_node_t * node, const char * topic, const rmw_qos_profile_t * qos)
{
  const char * fqn = node != NULL ? rcl_node_get_fully_qualified_name(node) : NULL;
  if (fqn == NULL) {
    fqn = "?";
  }
  pthread_mutex_lock(&asd_r_lock);
  entry_t * e = add_entry(handle, kind, fqn);
  if (e != NULL) {
    e->gated = kind == 's';
    size_t depth = 0;
    int reliability = 0, durability = 0, history = 0;
    if (qos != NULL) {
      reliability = (int)qos->reliability;
      durability = (int)qos->durability;
      history = (int)qos->history;
      depth = qos->depth;
      if (qos->history != RMW_QOS_POLICY_HISTORY_KEEP_ALL) {
        e->depth = depth > 0 ? (uint32_t)depth : 1;
      }
    }
    rp_send(
      "ep\t%u\t%c\t%s\t%s\t%d\t%d\t%d\t%zu", e->id, kind, fqn, topic != NULL ? topic : "?", reliability,
      durability, history, depth);
  }
  pthread_mutex_unlock(&asd_r_lock);
}

void asd_replay_timer(const void * timer, rcl_clock_t * clock, int64_t period_ns, bool swapped)
{
  pthread_mutex_lock(&asd_r_lock);
  // A timer created inside a callback belongs to that callback's node.
  const char * node = tls_node[0] != '\0' ? tls_node : "?";
  entry_t * granted = rp_entry(rp_own_token());
  if (granted != NULL) {
    node = granted->node;
  }
  entry_t * e = add_entry(timer, 't', node);
  if (e != NULL) {
    e->clock = clock;
    e->gated = clock->type == RCL_ROS_TIME;
    char clock_kind = swapped ? 'w' : (e->gated ? 'r' : 'f');
    e->next_ns = e->gated ? rp_timer_next(e) : -1;
    rp_send(
      "timer\t%u\t%s\t%lld\t%c\t%lld", e->id, e->node, (long long)period_ns, clock_kind, (long long)e->next_ns);
  }
  pthread_mutex_unlock(&asd_r_lock);
}

void asd_replay_release(const void * handle)
{
  pthread_mutex_lock(&asd_r_lock);
  entry_t * e = rp_find(handle);
  if (e != NULL) {
    drop_entry(e);
  }
  pthread_mutex_unlock(&asd_r_lock);
}

void rp_on_match(unsigned long long seq)
{
  for (const entry_t * e = live[KIND_PUBLISHER]; e != NULL; e = e->next) {
    size_t matched = 0;
    if (rcl_publisher_get_subscription_count((const rcl_publisher_t *)e->handle, &matched) == RCL_RET_OK) {
      rp_send("match\t%u\t%zu", e->id, matched);
    }
  }
  rp_send("matched\t%llu", seq);
}
