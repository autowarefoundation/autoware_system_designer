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

// LD_PRELOAD interposer over the rcl C API. Every hook forwards to the real
// function through dlsym(RTLD_NEXT) and hands what it saw to one observe_*
// function. Two tiers sit behind them:
//   counters  always on: atomic adds on the endpoint's slot of <pid>.stats;
//   events    while the control file says so: one fixed record in <pid>.trace.
// Init hooks register the endpoint's slot and append a name-table line. No hook
// inspects a message or takes a lock on the per-message path. The tracer is
// armed by ASD_TRACE_DIR; without it every hook is a plain forward.
//
// Entry points per ROS distribution (add a row and a hook for a new one):
//   publish   rcl_publish, rcl_publish_serialized_message, rcl_publish_loaned_message
//   take      rcl_take, rcl_take_serialized_message, rcl_take_loaned_message
//   timer     rcl_timer_call (Humble); rcl_timer_call_with_info (Jazzy+, also
//             reached from rcl_timer_call)
//   init      rcl_publisher_init, rcl_subscription_init, rcl_timer_init (Humble);
//             rcl_timer_init2 (Jazzy+, also reached from rcl_timer_init)
//   clock     rcl_set_ros_time_override

#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <rcl/node.h>
#include <rcl/publisher.h>
#include <rcl/subscription.h>
#include <rcl/time.h>
#include <rcl/timer.h>
#include <rmw/rmw.h>

#include "writer.h"

// ---- state -------------------------------------------------------------------

enum tracer_state { TRACER_UNSET = 0, TRACER_OFF = 1, TRACER_ON = 2 };

// One process's tracer. `events` opens on the first record the control file lets through.
typedef struct tracer {
  int state;
  int events_state;
  char dir[4096];
  uint64_t events_capacity;
  const asd_control_t * control;
  asd_names_t names;
  asd_stats_t stats;
  asd_events_t events;
} tracer_t;

static tracer_t g;
static pthread_mutex_t g_state_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t g_clock_lock = PTHREAD_MUTEX_INITIALIZER;
static __thread uint32_t tls_tid = 0;
// This thread's latest take or timer fire: the start of a publish's callback time.
static __thread uint64_t tls_trigger_ns = 0;
static __thread asd_stats_slot_t * tls_trigger_slot = NULL;
// Set while a timer hook forwards to rcl; Jazzy's rcl_timer_call and rcl_timer_init reach the
// hooked rcl_timer_call_with_info and rcl_timer_init2, which then only forward.
static __thread bool tls_in_timer_hook = false;

static uint32_t current_tid(void)
{
  if (tls_tid == 0) {
    tls_tid = (uint32_t)syscall(SYS_gettid);
  }
  return tls_tid;
}

// Both tracer locks are held across fork() so no other thread is inside a
// locked section when the child is cut; the child starts with them released.
static void on_fork_prepare(void)
{
  pthread_mutex_lock(&g_state_lock);
  pthread_mutex_lock(&g_clock_lock);
}

static void on_fork_parent(void)
{
  pthread_mutex_unlock(&g_clock_lock);
  pthread_mutex_unlock(&g_state_lock);
}

// The files belong to one pid; a forked child starts over on its first hook.
static void on_fork_child(void)
{
  pthread_mutex_unlock(&g_clock_lock);
  pthread_mutex_unlock(&g_state_lock);
  memset(&g, 0, sizeof(g));
  tls_tid = 0;
  tls_trigger_ns = 0;
  tls_trigger_slot = NULL;
}

static uint64_t env_count(const char * name, uint64_t fallback, uint64_t limit)
{
  const char * text = getenv(name);
  if (text == NULL || text[0] == '\0') {
    return fallback;
  }
  char * end = NULL;
  errno = 0;
  unsigned long long parsed = strtoull(text, &end, 10);
  bool is_number = text[0] >= '0' && text[0] <= '9' && *end == '\0' && errno == 0;
  if (!is_number || parsed == 0) {
    fprintf(stderr, "[asd_tracer] %s=%s is not a positive integer; using %llu\n", name, text,
            (unsigned long long)fallback);
    return fallback;
  }
  if (parsed > limit) {
    fprintf(stderr, "[asd_tracer] %s=%s exceeds %llu; capped\n", name, text, (unsigned long long)limit);
    return limit;
  }
  return parsed;
}

static int open_locked(void)
{
  const char * dir = getenv(ASD_TRACE_DIR_ENV);
  if (dir == NULL || dir[0] == '\0' || strlen(dir) >= sizeof(g.dir)) {
    return TRACER_OFF;
  }
  uint32_t pid = (uint32_t)getpid();
  uint32_t slots = (uint32_t)env_count(ASD_STATS_SLOTS_ENV, ASD_STATS_DEFAULT_SLOTS, ASD_STATS_MAX_SLOTS);
  if (asd_names_open(&g.names, dir, pid) != 0) {
    fprintf(stderr, "[asd_tracer] cannot open the name table in %s; tracing disabled\n", dir);
    return TRACER_OFF;
  }
  if (asd_stats_open(&g.stats, dir, pid, slots) != 0) {
    fprintf(stderr, "[asd_tracer] cannot open counters in %s; counters disabled\n", dir);
  }
  strcpy(g.dir, dir);
  g.events_capacity = env_count(ASD_TRACE_CAPACITY_ENV, ASD_TRACE_DEFAULT_CAPACITY, ASD_TRACE_MAX_CAPACITY);
  g.control = asd_control_map(dir);
  return TRACER_ON;
}

static inline bool tracing(void)
{
  int state = __atomic_load_n(&g.state, __ATOMIC_ACQUIRE);
  if (state == TRACER_UNSET) {
    pthread_mutex_lock(&g_state_lock);
    if (g.state == TRACER_UNSET) {
      __atomic_store_n(&g.state, open_locked(), __ATOMIC_RELEASE);
    }
    state = g.state;
    pthread_mutex_unlock(&g_state_lock);
  }
  return state == TRACER_ON;
}

// Event records are wanted and the event file is open. Called only while tracing().
static inline bool recording(void)
{
  if (g.control != NULL && __atomic_load_n(&g.control->events, __ATOMIC_RELAXED) == 0) {
    return false;
  }
  int state = __atomic_load_n(&g.events_state, __ATOMIC_ACQUIRE);
  if (state == TRACER_UNSET) {
    pthread_mutex_lock(&g_state_lock);
    if (g.events_state == TRACER_UNSET) {
      int opened = TRACER_ON;
      if (asd_events_open(&g.events, g.dir, (uint32_t)getpid(), g.events_capacity) != 0) {
        fprintf(stderr, "[asd_tracer] cannot open the event file in %s; events disabled\n", g.dir);
        opened = TRACER_OFF;
      }
      __atomic_store_n(&g.events_state, opened, __ATOMIC_RELEASE);
    }
    state = g.events_state;
    pthread_mutex_unlock(&g_state_lock);
  }
  return state == TRACER_ON;
}

__attribute__((constructor)) static void tracer_init(void)
{
  pthread_atfork(on_fork_prepare, on_fork_parent, on_fork_child);
  (void)tracing();
}

// Exit only turns the hooks off: a spinner thread may still be inside one, and
// the shared mappings and the flushed name table are complete without a close.
__attribute__((destructor)) static void tracer_fini(void)
{
  int on = TRACER_ON;
  __atomic_compare_exchange_n(&g.state, &on, TRACER_OFF, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
}

// ---- event records --------------------------------------------------------------

static asd_trace_record_t * claim_record(uint64_t handle, uint64_t t_ns, uint64_t t2_ns, uint8_t flags)
{
  asd_trace_record_t * r = asd_events_claim(&g.events);
  if (r == NULL) {
    return NULL;
  }
  r->flags = flags;
  r->reserved = 0;
  r->tid = current_tid();
  r->t_ns = t_ns;
  r->t2_ns = t2_ns;
  r->handle = handle;
  memset(r->gid, 0, ASD_TRACE_GID_SIZE);
  r->seq = 0;
  return r;
}

static void write_clock_record(const rcl_clock_t * clock, uint64_t t_ns, int64_t ros_ns, uint8_t flags)
{
  asd_trace_record_t * r = claim_record((uint64_t)(uintptr_t)clock, t_ns, (uint64_t)ros_ns, flags);
  if (r != NULL) {
    asd_events_commit(r, ASD_REC_CLOCK);
  }
}

// ---- observers ----------------------------------------------------------------------

static void observe_publish(const void * publisher, uint64_t t_in, uint8_t flags)
{
  asd_stats_slot_t * slot = asd_stats_find(&g.stats, publisher);
  if (slot != NULL) {
    asd_stats_count(slot, t_in);
    if (tls_trigger_ns != 0 && t_in >= tls_trigger_ns && t_in - tls_trigger_ns <= ASD_STATS_LATENCY_LIMIT_NS) {
      asd_stats_latency(slot, (int64_t)(t_in - tls_trigger_ns));
      if (tls_trigger_slot != NULL) {
        asd_stats_link(&g.stats, tls_trigger_slot, slot);
      }
    }
  }
  if (recording()) {
    asd_trace_record_t * r = claim_record((uint64_t)(uintptr_t)publisher, t_in, asd_now_realtime_ns(), flags);
    if (r != NULL) {
      asd_events_commit(r, ASD_REC_PUBLISH);
    }
  }
}

static void observe_take(const void * subscription, const rmw_message_info_t * info, uint8_t flags)
{
  uint64_t t_ns = asd_now_realtime_ns();
  asd_stats_slot_t * slot = asd_stats_find(&g.stats, subscription);
  tls_trigger_ns = t_ns;
  tls_trigger_slot = slot;
  if (slot != NULL) {
    asd_stats_count(slot, t_ns);
    if (info->source_timestamp > 0) {
      asd_stats_latency(slot, (int64_t)t_ns - (int64_t)info->source_timestamp);
    }
  }
  if (!recording()) {
    return;
  }
  asd_trace_record_t * r =
    claim_record((uint64_t)(uintptr_t)subscription, t_ns, (uint64_t)info->source_timestamp, flags);
  if (r == NULL) {
    return;
  }
  memcpy(r->gid, info->publisher_gid.data, ASD_TRACE_GID_SIZE);
  r->seq = info->publication_sequence_number;
  if (info->from_intra_process) {
    r->flags |= ASD_FLAG_FROM_INTRA;
  }
  asd_events_commit(r, ASD_REC_TAKE);
}

static void observe_timer(const void * timer)
{
  uint64_t t_ns = asd_now_realtime_ns();
  asd_stats_slot_t * slot = asd_stats_find(&g.stats, timer);
  tls_trigger_ns = t_ns;
  tls_trigger_slot = slot;
  if (slot != NULL) {
    asd_stats_count(slot, t_ns);
  }
  if (recording()) {
    asd_trace_record_t * r = claim_record((uint64_t)(uintptr_t)timer, t_ns, 0, 0);
    if (r != NULL) {
      asd_events_commit(r, ASD_REC_TIMER);
    }
  }
}

// One record per distinct ROS time value: every node clock of a process receives
// the same /clock message, and the mapping only needs the first. A value held for
// longer than twice the gap before it (a paused /clock keeps publishing its frozen
// value) also records the last wall time it was seen, so the reader keeps the
// plateau flat.
static void observe_clock(const rcl_clock_t * clock, int64_t ros_ns)
{
  static int64_t held_ros_ns = -1;
  static uint64_t held_first_ns = 0;
  static uint64_t held_last_ns = 0;
  static uint64_t gap_before_ns = 0;

  if (!recording()) {
    return;
  }
  uint64_t t_ns = asd_now_realtime_ns();
  pthread_mutex_lock(&g_clock_lock);
  if (ros_ns == held_ros_ns) {
    if (t_ns > held_last_ns) {
      held_last_ns = t_ns;
    }
    pthread_mutex_unlock(&g_clock_lock);
    return;
  }
  int64_t previous = held_ros_ns;
  uint64_t plateau_end = 0;
  if (previous >= 0 && gap_before_ns > 0 && held_last_ns - held_first_ns > 2 * gap_before_ns) {
    plateau_end = held_last_ns;
  }
  gap_before_ns = (previous >= 0 && t_ns > held_last_ns) ? t_ns - held_last_ns : 0;
  held_ros_ns = ros_ns;
  held_first_ns = t_ns;
  held_last_ns = t_ns;
  pthread_mutex_unlock(&g_clock_lock);

  if (plateau_end != 0) {
    write_clock_record(clock, plateau_end, previous, ASD_FLAG_CLOCK_LAST);
  }
  write_clock_record(clock, t_ns, ros_ns, 0);
}

// ---- name table ----------------------------------------------------------------------

static void gid_hex(const rmw_gid_t * gid, char out[ASD_TRACE_GID_SIZE * 2 + 1])
{
  static const char digits[] = "0123456789abcdef";
  for (unsigned i = 0; i < ASD_TRACE_GID_SIZE; ++i) {
    out[2 * i] = digits[gid->data[i] >> 4];
    out[2 * i + 1] = digits[gid->data[i] & 0xf];
  }
  out[ASD_TRACE_GID_SIZE * 2] = '\0';
}

// Name-table line: time, kind, handle, node, topic, publisher gid, stats slot (-1: none).
static void register_endpoint(
  const char * kind, uint8_t slot_kind, const void * handle, const rcl_node_t * node, const char * topic,
  const rmw_gid_t * gid)
{
  int64_t slot = asd_stats_register(&g.stats, slot_kind, handle);
  const char * fqn = node != NULL ? rcl_node_get_fully_qualified_name(node) : NULL;
  char gid_text[ASD_TRACE_GID_SIZE * 2 + 1] = "";
  if (gid != NULL) {
    gid_hex(gid, gid_text);
  }
  char line[4096];
  snprintf(
    line, sizeof(line), "%llu\t%s\t%llx\t%s\t%s\t%s\t%lld", (unsigned long long)asd_now_realtime_ns(), kind,
    (unsigned long long)(uintptr_t)handle, fqn != NULL ? fqn : "?", topic != NULL ? topic : "?", gid_text,
    (long long)slot);
  asd_names_line(&g.names, line);
}

// Name-table line: time, "timer", handle, period, stats slot (-1: none).
static void register_timer(const void * handle, int64_t period)
{
  int64_t slot = asd_stats_register(&g.stats, ASD_SLOT_TIMER, handle);
  char line[256];
  snprintf(
    line, sizeof(line), "%llu\ttimer\t%llx\t%lld\t%lld", (unsigned long long)asd_now_realtime_ns(),
    (unsigned long long)(uintptr_t)handle, (long long)period, (long long)slot);
  asd_names_line(&g.names, line);
}

// ---- real-function lookup ---------------------------------------------------------

// Binds `real` to the next definition of `name`; the forward type is spelled out per
// hook so no hook depends on an rcl declaration that a distribution deprecates.
#define FORWARD(fn_type, name)                                     \
  static fn_type real_slot = NULL;                                 \
  fn_type real = __atomic_load_n(&real_slot, __ATOMIC_ACQUIRE);    \
  if (real == NULL) {                                              \
    real = (fn_type)dlsym(RTLD_NEXT, name);                        \
    if (real == NULL) {                                            \
      return RCL_RET_ERROR;                                        \
    }                                                              \
    __atomic_store_n(&real_slot, real, __ATOMIC_RELEASE);          \
  }

// ---- publish hooks ------------------------------------------------------------------

typedef rcl_ret_t (*publish_fn)(const rcl_publisher_t *, const void *, rmw_publisher_allocation_t *);
typedef rcl_ret_t (*publish_serialized_fn)(
  const rcl_publisher_t *, const rcl_serialized_message_t *, rmw_publisher_allocation_t *);
typedef rcl_ret_t (*publish_loaned_fn)(const rcl_publisher_t *, void *, rmw_publisher_allocation_t *);

rcl_ret_t rcl_publish(
  const rcl_publisher_t * publisher, const void * ros_message, rmw_publisher_allocation_t * allocation)
{
  FORWARD(publish_fn, "rcl_publish");
  if (!tracing()) {
    return real(publisher, ros_message, allocation);
  }
  uint64_t t_in = asd_now_realtime_ns();
  rcl_ret_t ret = real(publisher, ros_message, allocation);
  if (ret == RCL_RET_OK) {
    observe_publish(publisher, t_in, 0);
  }
  return ret;
}

rcl_ret_t rcl_publish_serialized_message(
  const rcl_publisher_t * publisher, const rcl_serialized_message_t * serialized_message,
  rmw_publisher_allocation_t * allocation)
{
  FORWARD(publish_serialized_fn, "rcl_publish_serialized_message");
  if (!tracing()) {
    return real(publisher, serialized_message, allocation);
  }
  uint64_t t_in = asd_now_realtime_ns();
  rcl_ret_t ret = real(publisher, serialized_message, allocation);
  if (ret == RCL_RET_OK) {
    observe_publish(publisher, t_in, ASD_FLAG_SERIALIZED);
  }
  return ret;
}

rcl_ret_t rcl_publish_loaned_message(
  const rcl_publisher_t * publisher, void * ros_message, rmw_publisher_allocation_t * allocation)
{
  FORWARD(publish_loaned_fn, "rcl_publish_loaned_message");
  if (!tracing()) {
    return real(publisher, ros_message, allocation);
  }
  uint64_t t_in = asd_now_realtime_ns();
  rcl_ret_t ret = real(publisher, ros_message, allocation);
  if (ret == RCL_RET_OK) {
    observe_publish(publisher, t_in, ASD_FLAG_LOANED);
  }
  return ret;
}

// ---- take hooks ----------------------------------------------------------------------

typedef rcl_ret_t (*take_fn)(
  const rcl_subscription_t *, void *, rmw_message_info_t *, rmw_subscription_allocation_t *);
typedef rcl_ret_t (*take_serialized_fn)(
  const rcl_subscription_t *, rcl_serialized_message_t *, rmw_message_info_t *, rmw_subscription_allocation_t *);
typedef rcl_ret_t (*take_loaned_fn)(
  const rcl_subscription_t *, void **, rmw_message_info_t *, rmw_subscription_allocation_t *);

// A caller that passes no message_info gets one of the hook's own, which rcl fills.
#define TAKE_INFO(message_info)                                  \
  rmw_message_info_t local;                                       \
  rmw_message_info_t * info = (message_info);                     \
  if (info == NULL) {                                             \
    memset(&local, 0, sizeof(local));                             \
    info = &local;                                                \
  }

rcl_ret_t rcl_take(
  const rcl_subscription_t * subscription, void * ros_message, rmw_message_info_t * message_info,
  rmw_subscription_allocation_t * allocation)
{
  FORWARD(take_fn, "rcl_take");
  if (!tracing()) {
    return real(subscription, ros_message, message_info, allocation);
  }
  TAKE_INFO(message_info);
  rcl_ret_t ret = real(subscription, ros_message, info, allocation);
  if (ret == RCL_RET_OK) {
    observe_take(subscription, info, 0);
  }
  return ret;
}

rcl_ret_t rcl_take_serialized_message(
  const rcl_subscription_t * subscription, rcl_serialized_message_t * serialized_message,
  rmw_message_info_t * message_info, rmw_subscription_allocation_t * allocation)
{
  FORWARD(take_serialized_fn, "rcl_take_serialized_message");
  if (!tracing()) {
    return real(subscription, serialized_message, message_info, allocation);
  }
  TAKE_INFO(message_info);
  rcl_ret_t ret = real(subscription, serialized_message, info, allocation);
  if (ret == RCL_RET_OK) {
    observe_take(subscription, info, ASD_FLAG_SERIALIZED);
  }
  return ret;
}

rcl_ret_t rcl_take_loaned_message(
  const rcl_subscription_t * subscription, void ** loaned_message, rmw_message_info_t * message_info,
  rmw_subscription_allocation_t * allocation)
{
  FORWARD(take_loaned_fn, "rcl_take_loaned_message");
  if (!tracing()) {
    return real(subscription, loaned_message, message_info, allocation);
  }
  TAKE_INFO(message_info);
  rcl_ret_t ret = real(subscription, loaned_message, info, allocation);
  if (ret == RCL_RET_OK) {
    observe_take(subscription, info, ASD_FLAG_LOANED);
  }
  return ret;
}

// ---- timer hooks ----------------------------------------------------------------------

struct rcl_timer_call_info_s;
typedef rcl_ret_t (*timer_call_fn)(rcl_timer_t *);
typedef rcl_ret_t (*timer_call_with_info_fn)(rcl_timer_t *, struct rcl_timer_call_info_s *);
typedef rcl_ret_t (*timer_init_fn)(
  rcl_timer_t *, rcl_clock_t *, rcl_context_t *, int64_t, const rcl_timer_callback_t, rcl_allocator_t);
typedef rcl_ret_t (*timer_init2_fn)(
  rcl_timer_t *, rcl_clock_t *, rcl_context_t *, int64_t, const rcl_timer_callback_t, rcl_allocator_t, bool);

rcl_ret_t rcl_timer_call(rcl_timer_t * timer)
{
  FORWARD(timer_call_fn, "rcl_timer_call");
  if (!tracing()) {
    return real(timer);
  }
  tls_in_timer_hook = true;
  rcl_ret_t ret = real(timer);
  tls_in_timer_hook = false;
  if (ret == RCL_RET_OK) {
    observe_timer(timer);
  }
  return ret;
}

// Jazzy and later: a process that never calls it never reaches the NULL forward on Humble.
rcl_ret_t rcl_timer_call_with_info(rcl_timer_t * timer, struct rcl_timer_call_info_s * call_info)
{
  FORWARD(timer_call_with_info_fn, "rcl_timer_call_with_info");
  if (tls_in_timer_hook || !tracing()) {
    return real(timer, call_info);
  }
  rcl_ret_t ret = real(timer, call_info);
  if (ret == RCL_RET_OK) {
    observe_timer(timer);
  }
  return ret;
}

rcl_ret_t rcl_timer_init(
  rcl_timer_t * timer, rcl_clock_t * clock, rcl_context_t * context, int64_t period,
  const rcl_timer_callback_t callback, rcl_allocator_t allocator)
{
  FORWARD(timer_init_fn, "rcl_timer_init");
  tls_in_timer_hook = true;
  rcl_ret_t ret = real(timer, clock, context, period, callback, allocator);
  tls_in_timer_hook = false;
  if (ret == RCL_RET_OK && tracing()) {
    register_timer(timer, period);
  }
  return ret;
}

// Jazzy and later: a process that never calls it never reaches the NULL forward on Humble.
rcl_ret_t rcl_timer_init2(
  rcl_timer_t * timer, rcl_clock_t * clock, rcl_context_t * context, int64_t period,
  const rcl_timer_callback_t callback, rcl_allocator_t allocator, bool autostart)
{
  FORWARD(timer_init2_fn, "rcl_timer_init2");
  rcl_ret_t ret = real(timer, clock, context, period, callback, allocator, autostart);
  if (ret == RCL_RET_OK && !tls_in_timer_hook && tracing()) {
    register_timer(timer, period);
  }
  return ret;
}

// ---- clock hook -----------------------------------------------------------------------

typedef rcl_ret_t (*set_ros_time_override_fn)(rcl_clock_t *, rcl_time_point_value_t);

// rclcpp's TimeSource sets the override on every /clock message while use_sim_time
// is on; the records map wall time to ROS time for the analysis.
rcl_ret_t rcl_set_ros_time_override(rcl_clock_t * clock, rcl_time_point_value_t time_value)
{
  FORWARD(set_ros_time_override_fn, "rcl_set_ros_time_override");
  rcl_ret_t ret = real(clock, time_value);
  if (ret == RCL_RET_OK && tracing()) {
    observe_clock(clock, time_value);
  }
  return ret;
}

// ---- endpoint init hooks ---------------------------------------------------------------

typedef rcl_ret_t (*publisher_init_fn)(
  rcl_publisher_t *, const rcl_node_t *, const rosidl_message_type_support_t *, const char *,
  const rcl_publisher_options_t *);
typedef rcl_ret_t (*subscription_init_fn)(
  rcl_subscription_t *, const rcl_node_t *, const rosidl_message_type_support_t *, const char *,
  const rcl_subscription_options_t *);

rcl_ret_t rcl_publisher_init(
  rcl_publisher_t * publisher, const rcl_node_t * node, const rosidl_message_type_support_t * type_support,
  const char * topic_name, const rcl_publisher_options_t * options)
{
  FORWARD(publisher_init_fn, "rcl_publisher_init");
  rcl_ret_t ret = real(publisher, node, type_support, topic_name, options);
  if (ret == RCL_RET_OK && tracing()) {
    rmw_gid_t gid;
    memset(&gid, 0, sizeof(gid));
    const rmw_gid_t * gid_ptr = NULL;
    rmw_publisher_t * rmw_pub = rcl_publisher_get_rmw_handle(publisher);
    if (rmw_pub != NULL && rmw_get_gid_for_publisher(rmw_pub, &gid) == RMW_RET_OK) {
      gid_ptr = &gid;
    }
    register_endpoint("pub", ASD_SLOT_PUB, publisher, node, rcl_publisher_get_topic_name(publisher), gid_ptr);
  }
  return ret;
}

rcl_ret_t rcl_subscription_init(
  rcl_subscription_t * subscription, const rcl_node_t * node,
  const rosidl_message_type_support_t * type_support, const char * topic_name,
  const rcl_subscription_options_t * options)
{
  FORWARD(subscription_init_fn, "rcl_subscription_init");
  rcl_ret_t ret = real(subscription, node, type_support, topic_name, options);
  if (ret == RCL_RET_OK && tracing()) {
    register_endpoint(
      "sub", ASD_SLOT_SUB, subscription, node, rcl_subscription_get_topic_name(subscription), NULL);
  }
  return ret;
}
